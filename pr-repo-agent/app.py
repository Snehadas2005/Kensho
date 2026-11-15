import json
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from core import VERSION
from core.github_client import fetch_github_item
from core.graph import build_triage_graph
from core.guards import is_sandbox_error, is_timeout
from ui_theme import brand_header_html, build_css, find_logo


BASE_DIR = Path(__file__).resolve().parent


def find_logo() -> Path | None:
    """Finds kensho-logo.png inside pr-repo-agent/assets or parent repo assets/."""
    candidates = [
        BASE_DIR / "assets" / "kensho-logo.png",
        BASE_DIR.parent / "assets" / "kensho-logo.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


_logo = find_logo()
st.set_page_config(
    page_title="Kensho",
    page_icon=str(_logo) if _logo else "🔍",
    layout="wide",
)

WORKSPACE_DIR = Path("workspace")
REPO_DIR = WORKSPACE_DIR / "pipelines"
MAP_PATH = WORKSPACE_DIR / "repo_map.json"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"

STEP_LABELS = {
    "triage": "Triage & localization",
    "explorer_coder": "Writing reproduction test",
    "sandbox": "Running in Docker sandbox",
    "judge": "Judging result",
}

# Theme: the toggle lives in the sidebar; its value is already in session_state on every rerun.
st.markdown(build_css(st.session_state.get("light_mode", False)), unsafe_allow_html=True)


def classify(state: dict) -> tuple[str, str, str]:
    """Returns (label, banner_kind, explanation). Timeouts and Docker errors get their own states."""
    status = (state.get("verdict") or {}).get("status", "UNKNOWN")
    if is_timeout(state):
        return "TIMEOUT", "warning", "The sandbox timed out before the test finished. Nothing was verified."
    if is_sandbox_error(state):
        return "SANDBOX ERROR", "error", "The Docker sandbox failed to run. Check Docker and the repro images."
    if status == "VERIFIED":
        return "VERIFIED", "success", "The generated test failed in the way the report describes."
    if status == "NOT_REPRODUCED":
        return "NOT REPRODUCED", "warning", "The generated test passed, so the bug was not triggered."
    if status == "TEST_ERROR":
        return "TEST ERROR", "error", "The generated test itself was broken (syntax, imports or setup)."
    return "UNKNOWN", "info", "No verdict available."


def show_banner(kind: str, text: str) -> None:
    {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(text)


def preflight() -> list[str]:
    problems = []
    if not MAP_PATH.exists():
        problems.append("`workspace/repo_map.json` not found. Run `python main.py index` in your terminal first.")
    if not REPO_DIR.exists():
        problems.append("`workspace/pipelines` not found. Clone the target repo as described in the README.")
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        problems.append("`GEMINI_API_KEY` is not set. Add it to your `.env` file.")
    return problems


def run_agent(repo_name: str, item_type: str, item_number: int, max_retries: int) -> dict | None:
    """Runs the graph, streaming each node's completion into a live status box."""
    started = time.perf_counter()
    state: dict = {}

    with st.status(f"Fetching {item_type.upper()} #{item_number}…", expanded=True) as status:
        try:
            work_item = fetch_github_item(repo_name, item_number, item_type=item_type)
            st.write(f"📥 Fetched: **{work_item.title}**")

            graph = build_triage_graph(REPO_DIR)  # raises if Docker is not running
            initial = {
                "repo_name": repo_name,
                "item_type": item_type,
                "item_number": item_number,
                "title": work_item.title,
                "body": work_item.body,
                "diff_summary": work_item.diff_summary,
                "repo_map_json": MAP_PATH.read_text(encoding="utf-8"),
                "retry_count": 0,
                "max_retries": max_retries,
            }
            state = dict(initial)

            for chunk in graph.stream(initial, stream_mode="updates"):
                for node, update in chunk.items():
                    state.update(update)
                    status.update(label=f"{STEP_LABELS.get(node, node)} ✓", state="running")
                    if node == "triage":
                        st.write(f"🧭 Triage → `{state.get('target_language')}` in `{state.get('target_subdir')}`")
                    elif node == "explorer_coder":
                        st.write(f"🧪 Test drafted (attempt {state.get('retry_count', 0) + 1})")
                    elif node == "sandbox":
                        st.write(f"🐳 Sandbox finished: exit code `{state.get('exit_code')}` in {state.get('duration_sec')}s")
                    elif node == "judge":
                        st.write(f"⚖️ Verdict: **{(state.get('verdict') or {}).get('status', 'UNKNOWN')}**")

            state["total_duration_sec"] = round(time.perf_counter() - started, 1)
            state["saved_at"] = datetime.now().isoformat(timespec="seconds")
            LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_RUN_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            status.update(label="Done", state="complete", expanded=False)
            return state

        except Exception as e:  # noqa: BLE001 - surface every failure as a readable message
            status.update(label="Run failed", state="error", expanded=True)
            name = type(e).__name__
            if name == "DockerException" or ("docker" in str(e).lower() and "connect" in str(e).lower()):
                st.error("Docker isn't reachable. Start Docker Desktop and try again.")
            elif name == "GithubException":
                st.error(f"GitHub API error: {getattr(e, 'data', e)}. Check the repo name, item number and `GITHUB_TOKEN`.")
            elif name == "RuntimeError" and "fallback models failed" in str(e):
                st.error("All LLM models failed (likely rate limits on the free tier). Wait a minute and retry.")
                st.code(str(e), language="text")
            else:
                st.error(f"{name}: {e}")
            with st.expander("Technical details"):
                st.exception(e)
            return None


# ───────────────────────────── Header ─────────────────────────────
st.markdown(brand_header_html(VERSION), unsafe_allow_html=True)

# ───────────────────────────── Sidebar ─────────────────────────────
with st.sidebar:
    st.toggle("☀️ Light mode", key="light_mode")
    st.divider()
    st.header("Target")
    repo_name = st.text_input("GitHub repository", value="kubeflow/pipelines")
    item_type = st.selectbox("Item type", ["issue", "pr"])
    item_number = st.number_input("Issue / PR number", min_value=1, value=13401, step=1)
    max_retries = st.slider("Max self-correction retries", 0, 3, 2)
    run_btn = st.button("Run triage & repro", type="primary", use_container_width=True)

    st.divider()
    if MAP_PATH.exists():
        repo_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        st.caption(f"Indexed SHA: `{repo_map.get('last_indexed_sha', '')[:7]}`")
        st.caption(f"Indexed modules: {len(repo_map.get('modules', []))}")
    else:
        st.caption("Repo not indexed yet.")
    if not os.getenv("GITHUB_TOKEN"):
        st.caption("⚠️ No `GITHUB_TOKEN`: GitHub calls are limited to 60/hour.")

# ───────────────────────────── Run / load ─────────────────────────────
state = None
is_cached = False

if run_btn:
    problems = preflight()
    if problems:
        for p in problems:
            st.error(p)
    else:
        state = run_agent(repo_name, item_type, int(item_number), int(max_retries))
        if state:
            st.session_state["state"] = state
elif "state" in st.session_state:
    state = st.session_state["state"]
elif LAST_RUN_PATH.exists():
    try:
        state = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
        is_cached = True
    except json.JSONDecodeError:
        st.warning("`last_run.json` is corrupted. Run a new triage.")

# ───────────────────────────── Results ─────────────────────────────
if not state:
    if not run_btn:
        st.info("Pick an issue or PR in the sidebar and click **Run triage & repro** "
                "(or run `python main.py issue <num>` in your terminal).")
    st.stop()

verdict = state.get("verdict") or {}
label, kind, explanation = classify(state)

if is_cached:
    saved = state.get("saved_at", "an earlier run")
    st.caption(f"Showing last run ({saved}). Click **Run triage & repro** for a fresh one.")

show_banner(kind, f"**{label}** — #{state.get('item_number', '?')}: {state.get('title', '(untitled)')}  \n{explanation}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Status", label)
m2.metric("Runtime", str(state.get("target_language", "—")).capitalize())
m3.metric("Attempts", state.get("retry_count", "—"))
m4.metric("Sandbox time", f"{state.get('duration_sec', 0)}s")

tab_triage, tab_test, tab_sandbox, tab_report = st.tabs(["🧭 Triage", "🧪 Test", "🐳 Sandbox", "📝 Report"])

with tab_triage:
    st.markdown(f"**Target:** `{state.get('target_language', '—')}` in `{state.get('target_subdir', '—')}`")
    st.markdown("**Suspect files**")
    for f in state.get("suspect_files", []) or []:
        st.markdown(f"- `{f}`")
    if state.get("search_keywords"):
        st.markdown("**Search keywords:** " + ", ".join(f"`{k}`" for k in state["search_keywords"]))
    st.markdown("**Hypothesis**")
    st.info(state.get("hypothesis", "") or "(none)")
    if state.get("models_used"):
        st.caption("Models used: " + ", ".join(f"{k}: `{v}`" for k, v in state["models_used"].items()))

with tab_test:
    st.caption("Generated reproduction test")
    st.code(state.get("test_code", "") or "(none)", language=state.get("target_language", "python"), line_numbers=True)

with tab_sandbox:
    code = state.get("exit_code")
    if is_timeout(state):
        st.warning("Exit code 124: the sandbox timed out. This is not a reproduction.")
    elif code == 0:
        st.success("Exit code 0: the test passed.")
    else:
        st.markdown(f"**Exit code:** `{code}`")
    with st.expander("STDOUT", expanded=True):
        st.code(state.get("stdout", "") or "(empty)", language="text")
    with st.expander("STDERR", expanded=bool(state.get("stderr"))):
        st.code(state.get("stderr", "") or "(empty)", language="text")

with tab_report:
    if verdict.get("root_cause_summary"):
        st.markdown("**Root cause**")
        st.write(verdict["root_cause_summary"])
    if verdict.get("suggested_fix_direction"):
        st.markdown("**Suggested fix direction**")
        st.write(verdict["suggested_fix_direction"])
    st.divider()
    report = verdict.get("github_markdown_report", "")
    left, right = st.columns(2)
    with left:
        st.markdown("**Markdown** (use the copy icon, top right of the box)")
        st.code(report or "(empty)", language="markdown")
    with right:
        st.markdown("**Preview**")
        with st.container(border=True):
            st.markdown(report or "_(empty)_")