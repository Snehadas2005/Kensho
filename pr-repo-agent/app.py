import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import streamlit as st

import ui_theme as ui
from core import __version__
from core.errors import friendly_error
from core.github_client import fetch_github_item
from core.graph import build_triage_graph

st.set_page_config(page_title="Kensho", page_icon="🔍", layout="wide", initial_sidebar_state="expanded")

# Theme: follows the native Streamlit toggle (3 dots -> Settings). Default = light (see .streamlit/config.toml)
st.markdown(ui.build_css(ui.current_mode()), unsafe_allow_html=True)

ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = ROOT / "workspace"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"
PENDING_RUN_PATH = WORKSPACE_DIR / "pending_run.json"
ALT_MAP_PATH = ROOT / ".kensho" / "repo_map.json"  # used when workspace/repo_map.json does not exist

NODE_LABELS = {
    "triage": "Triage & localization",
    "explorer_coder": "Writing reproduction test",
    "sandbox": "Running in Docker sandbox",
    "judge": "Judging result",
}


# ---------- small helpers ----------
def _git(*args: str, cwd: str | None = None) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _slug(url: str) -> str:
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url or "")
    return m.group(1) if m else ""


@st.cache_data(ttl=300, show_spinner=False)
def detect_repo_context(repo_dir: str) -> dict:
    """Fork / upstream relationship read from the git remotes of the target clone."""
    if not Path(repo_dir).is_dir():
        return {}
    return {
        "origin": _slug(_git("remote", "get-url", "origin", cwd=repo_dir)),
        "upstream": _slug(_git("remote", "get-url", "upstream", cwd=repo_dir)),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def github_username(token: str, fallback: str) -> str:
    """Authenticated GitHub login (needs GITHUB_TOKEN), else git config / remote owner."""
    if token:
        try:
            req = urllib.request.Request(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                login = json.loads(resp.read().decode()).get("login")
                if login:
                    return login
        except Exception:  # noqa: BLE001
            pass
    return fallback or _git("config", "user.name")


def pick(source: dict, *keys, default=None):
    """First non-empty value among several possible state keys."""
    for k in keys:
        v = source.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def excerpt(text: str, limit: int = 260) -> str:
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"[#>*`_\[\]]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit].rstrip() + "…" if len(t) > limit else t


def as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
    return [str(v) for v in value]


def runtime_stack(repo_map: dict) -> str:
    stack = pick(repo_map, "runtimes", "runtime_stack", "languages", "stack")
    if isinstance(stack, dict):
        stack = list(stack.keys())
    if not stack:
        stack = {m.get("language") for m in repo_map.get("modules", []) if isinstance(m, dict) and m.get("language")}
    return ", ".join(sorted(as_list(list(stack) if not isinstance(stack, (str, list)) else stack)))


# ---------- state defaults ----------
_boot_ctx = detect_repo_context(str(WORKSPACE_DIR / "notebooks"))
st.session_state.setdefault("repo_name", _boot_ctx.get("upstream") or "kubeflow/notebooks")
st.session_state.setdefault("item_type", "issue")
st.session_state.setdefault("item_number", 1596)
st.session_state.setdefault("max_retries", 2)
st.session_state.setdefault("repo_dir", str(WORKSPACE_DIR / st.session_state["repo_name"].split("/")[-1]))
st.session_state.setdefault("map_path", str(WORKSPACE_DIR / "repo_map.json"))

# a pending request written by `kensho issue <n>` overrides the defaults once per session
if "pending_checked" not in st.session_state:
    st.session_state["pending_checked"] = True
    if PENDING_RUN_PATH.exists():
        try:
            st.session_state.update(json.loads(PENDING_RUN_PATH.read_text(encoding="utf-8")))
            st.session_state["autorun"] = True
        except (json.JSONDecodeError, OSError):
            pass
        finally:
            PENDING_RUN_PATH.unlink(missing_ok=True)

REPO_DIR = Path(st.session_state["repo_dir"])
MAP_PATH = Path(st.session_state["map_path"])
if not MAP_PATH.exists() and ALT_MAP_PATH.exists():
    MAP_PATH = ALT_MAP_PATH

repo_map: dict = {}
if MAP_PATH.exists():
    try:
        repo_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        repo_map = {}

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown(ui.sidebar_title_html("Target"), unsafe_allow_html=True)
    repo_name = st.text_input("GitHub repository", key="repo_name")
    item_type = st.selectbox(
        "Item Type", ["issue", "pr"], key="item_type", format_func=lambda v: {"issue": "Issue", "pr": "PR"}[v]
    )
    item_number = st.number_input("Issue / PR Number", min_value=1, step=1, key="item_number")
    max_retries = st.slider("Maximum self-correction retries", 0, 3, key="max_retries")
    run_btn = st.button("Run Triage & Repro", type="primary", use_container_width=True)
    run_btn = run_btn or st.session_state.pop("autorun", False)

    # compact info block: user, fork/upstream, repo map, runtime stack, sandbox image
    ctx = detect_repo_context(str(REPO_DIR))
    token = os.environ.get("GITHUB_TOKEN", "")
    owner_fallback = (ctx.get("origin") or "").split("/")[0]
    user = github_username(token, owner_fallback)

    def row(label: str, value: str) -> str:
        return f'<div class="k-meta-row"><span>{ui.esc(label)}</span><b>{ui.esc(value)}</b></div>'

    rows = [row("GitHub user", f"@{user}" if user else "unknown")]
    if ctx.get("upstream") and ctx.get("origin"):
        rows.append(row("Fork → Upstream", f"{ctx['origin']} → {ctx['upstream']}"))
    elif ctx.get("origin"):
        rows.append(row("Remote", ctx["origin"]))
    if repo_map:
        sha = str(repo_map.get("last_indexed_sha", ""))[:7] or "—"
        rows.append(row("Repo map", f"{sha} · {len(repo_map.get('modules', []))} modules"))
        stack = runtime_stack(repo_map)
        if stack:
            rows.append(row("Runtime stack", stack))
    elif MAP_PATH.exists():
        rows.append('<div class="k-meta-warn">repo_map.json unreadable. Re-run <code>kensho index</code>.</div>')
    image = "kensho-sandbox-" + re.sub(r"[^a-z0-9]+", "-", repo_name.lower()).strip("-") + ":latest"
    rows.append(row("Sandbox image", image))
    if not token:
        rows.append('<div class="k-meta-warn">⚠ GITHUB_TOKEN is not set (rate limits apply)</div>')
    st.markdown(f'<div class="k-meta">{"".join(rows)}</div>', unsafe_allow_html=True)


# ---------- Agent ----------
def run_agent() -> dict | None:
    """Runs the graph, streaming node progress into the UI. Returns final state or None on failure."""
    try:
        with st.status("Running agent loop…", expanded=True) as status:
            st.write("Fetching item from GitHub…")
            work_item = fetch_github_item(repo_name, int(item_number), item_type=item_type)
            graph = build_triage_graph(REPO_DIR)
            initial = {
                "repo_name": repo_name,
                "item_type": item_type,
                "item_number": int(item_number),
                "title": work_item.title,
                "body": work_item.body,
                "diff_summary": work_item.diff_summary,
                "repo_map_json": MAP_PATH.read_text(encoding="utf-8"),
                "retry_count": 0,
                "max_retries": max_retries,
            }
            final = dict(initial)
            for update in graph.stream(initial, stream_mode="updates"):
                for node, delta in update.items():
                    final.update(delta or {})
                    label = NODE_LABELS.get(node, node)
                    extra = ""
                    if node == "sandbox":
                        extra = f" (exit code {final.get('exit_code')}, attempt {final.get('retry_count')})"
                    st.write(f"✓ {label}{extra}")
            status.update(label="Agent loop finished", state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001
        st.error(friendly_error(exc))
        return None

    LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps(final, indent=2, default=str), encoding="utf-8")
    return final


# ---------- Masthead ----------
st.markdown(ui.masthead_html(__version__), unsafe_allow_html=True)

# ---------- Load / run ----------
state = None
is_last_run = False

if run_btn:
    if not MAP_PATH.exists():
        st.error(f"`{MAP_PATH}` not found. Run `kensho index` in your terminal first.")
    else:
        state = run_agent()
elif LAST_RUN_PATH.exists():
    try:
        state = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
        is_last_run = True
    except json.JSONDecodeError:
        st.warning("`last_run.json` is corrupted. Run the agent again.")

if not state or "verdict" not in state:
    st.info("Pick an issue or PR in the sidebar and click **Run Triage & Repro**, or run `kensho issue <number>` in your terminal.")
    st.stop()

# ---------- Results: banner + metrics ----------
verdict = state.get("verdict") or {}
raw_status = str(verdict.get("status", "UNKNOWN")).upper()
exit_code = state.get("exit_code")

if raw_status == "VERIFIED":
    kind, label = "ok", "VERIFIED"
elif raw_status == "NOT_REPRODUCED":
    kind, label = "warn", "NOT REPRODUCED"
elif exit_code == 124:
    kind, label = "err", "TIMEOUT"
elif "SANDBOX" in raw_status:
    kind, label = "err", "SANDBOX ERROR"
elif "ERROR" in raw_status:
    kind, label = "err", "TEST ERROR"
else:
    kind, label = "err", raw_status.replace("_", " ")

if is_last_run:
    st.markdown(
        ui.caption_html("Showing your last saved run. Click Run Triage & Repro for a new one"),
        unsafe_allow_html=True,
    )

banner_title = f"{label} - #{state.get('item_number', '?')}: {state.get('title', '')}"
st.markdown(ui.banner_html(kind, banner_title, excerpt(state.get("body", ""))), unsafe_allow_html=True)

try:
    duration = f"{float(state.get('duration_sec', 0)):.2f}s"
except (TypeError, ValueError):
    duration = "—"

st.markdown(
    ui.metrics_html(
        [
            ("Status", label),
            ("Runtime", str(state.get("target_language") or "—")),
            ("Attempts", str(state.get("retry_count", "—"))),
            ("Sandbox time", duration),
        ]
    ),
    unsafe_allow_html=True,
)

# ---------- Tabs ----------
tab_triage, tab_test, tab_sandbox, tab_report = st.tabs(["Triage", "Test", "Sandbox", "Report"])

with tab_triage:
    lang = str(state.get("target_language") or "—").lower()
    subdir = state.get("target_subdir") or ""
    target_txt = f"{lang} in {subdir}" if subdir else lang
    st.markdown(f'<div class="k-line"><b>Target</b>: {ui.esc(target_txt)}</div>', unsafe_allow_html=True)

    suspects = as_list(state.get("suspect_files"))
    st.markdown('<div class="k-line"><b>Suspected files</b></div>', unsafe_allow_html=True)
    if suspects:
        items = "".join(f"<li>{ui.esc(f)}</li>" for f in suspects)
        st.markdown(f'<ul class="k-list">{items}</ul>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="k-list k-muted">None identified.</div>', unsafe_allow_html=True)

    keywords = as_list(pick(state, "search_keywords", "keywords", "symbols") or pick(verdict, "search_keywords", "keywords"))
    chips = "".join(f'<span class="k-chip">{ui.esc(k)}</span>' for k in keywords) or '<span class="k-muted">—</span>'
    st.markdown(f'<div class="k-line"><b>Search Keywords:</b> {chips}</div>', unsafe_allow_html=True)

    st.markdown('<div class="k-line"><b>Hypothesis</b></div>', unsafe_allow_html=True)
    st.markdown(state.get("hypothesis") or "—")

    models = pick(state, "models", "models_used", "model_telemetry")
    if models:
        if isinstance(models, dict):
            txt = " · ".join(f"{k}: {v}" for k, v in models.items())
        else:
            txt = ", ".join(as_list(models))
        st.markdown(f'<div class="k-line"><b>Models</b>: <span class="k-muted">{ui.esc(txt)}</span></div>', unsafe_allow_html=True)

with tab_test:
    st.code(
        state.get("test_code", "") or "(no test generated)",
        language=str(state.get("target_language") or "python").lower(),
        line_numbers=True,
    )

with tab_sandbox:
    if exit_code == 0:
        meaning, pill_cls = "clean exit", ""
    elif exit_code == 124:
        meaning, pill_cls = "timed out", "err"
    else:
        meaning, pill_cls = "non-zero exit", ""
    st.markdown(
        f'<div class="k-line"><b>Exit code</b>: <span class="k-pill {pill_cls}">{ui.esc(exit_code)} · {meaning}</span>'
        f' &nbsp; <b>Sandbox time</b>: {ui.esc(duration)} &nbsp; <b>Attempts</b>: {ui.esc(state.get("retry_count", "—"))}</div>',
        unsafe_allow_html=True,
    )
    with st.expander("STDOUT", expanded=True):
        st.code(state.get("stdout", "") or "(empty)", language="text")
    with st.expander("STDERR", expanded=bool(state.get("stderr"))):
        st.code(state.get("stderr", "") or "(empty)", language="text")

with tab_report:
    if verdict.get("root_cause_summary"):
        st.markdown('<div class="k-line"><b>Root cause</b></div>', unsafe_allow_html=True)
        st.markdown(verdict["root_cause_summary"])
    if verdict.get("suggested_fix_direction"):
        st.markdown('<div class="k-line"><b>Suggested fix direction</b></div>', unsafe_allow_html=True)
        st.markdown(verdict["suggested_fix_direction"])

    report = verdict.get("github_markdown_report", "")
    st.markdown('<div class="k-line"><b>GitHub comment</b> <span class="k-muted">(copy icon, top-right of the block)</span></div>', unsafe_allow_html=True)
    col_raw, col_prev = st.columns(2)
    with col_raw:
        st.code(report or "(no report)", language="markdown")
    with col_prev:
        with st.container(border=True):
            st.markdown(report or "_(no report)_")