import json
from pathlib import Path
import streamlit as st

from core import __version__
from core.errors import friendly_error
from core.github_client import fetch_github_item
from core.graph import build_triage_graph

st.set_page_config(page_title="Kensho", page_icon="🔍", layout="wide")

# Fixed dark theme: hide the settings menu (which holds the light/dark switch) and the deploy button
st.markdown(
    """
    <style>
    [data-testid="stMainMenu"], [data-testid="stAppDeployButton"], .stAppDeployButton,
    #MainMenu { display: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

WORKSPACE_DIR = Path(__file__).resolve().parent / "workspace"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"
PENDING_RUN_PATH = WORKSPACE_DIR / "pending_run.json"

# Sidebar defaults; a pending request written by `kensho issue <n>` overrides them once per session
st.session_state.setdefault("repo_name", "kubeflow/pipelines")
st.session_state.setdefault("item_type", "issue")
st.session_state.setdefault("item_number", 13401)
st.session_state.setdefault("max_retries", 2)
st.session_state.setdefault("repo_dir", str(WORKSPACE_DIR / "pipelines"))
st.session_state.setdefault("map_path", str(WORKSPACE_DIR / "repo_map.json"))

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

NODE_LABELS = {
    "triage": "Triage & localization",
    "explorer_coder": "Writing reproduction test",
    "sandbox": "Running in Docker sandbox",
    "judge": "Judging result",
}

# ---------- Header ----------
st.title("Kensho")
st.caption(f"Autonomous PR triage & bug reproduction · v{__version__}")

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Target")
    repo_name = st.text_input("GitHub repository", key="repo_name")
    item_type = st.selectbox("Item type", ["issue", "pr"], key="item_type")
    item_number = st.number_input("Issue / PR number", min_value=1, step=1, key="item_number")
    max_retries = st.slider("Max self-correction retries", 0, 3, key="max_retries")
    run_btn = st.button("Run triage & repro", type="primary", use_container_width=True)
    run_btn = run_btn or st.session_state.pop("autorun", False)

    if MAP_PATH.exists():
        try:
            repo_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
            st.divider()
            st.caption("Repo map")
            st.markdown(
                f"SHA `{repo_map.get('last_indexed_sha', '')[:7]}` · "
                f"{len(repo_map.get('modules', []))} modules"
            )
        except json.JSONDecodeError:
            st.warning("`repo_map.json` is unreadable. Re-run `kensho index`.")


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


# ---------- State ----------
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

# ---------- Results ----------
if not state or "verdict" not in state:
    st.info("Pick an issue or PR in the sidebar and click **Run triage & repro**, or run `kensho issue <number>` in your terminal.")
    st.stop()

verdict = state.get("verdict", {})
status = verdict.get("status", "UNKNOWN")
timed_out = state.get("exit_code") == 124
heading = f"#{state.get('item_number', '?')}: {state.get('title', '')}"

if is_last_run:
    st.caption("Showing your last saved run.")

if status == "VERIFIED":
    st.success(f"**Verified reproduction** — {heading}")
elif status == "NOT_REPRODUCED":
    st.warning(f"**Not reproduced** — {heading}")
elif timed_out:
    st.error(f"**Sandbox timed out** — {heading}")
else:
    st.error(f"**Test / environment error** — {heading}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Status", status.replace("_", " ").title())
m2.metric("Runtime", str(state.get("target_language", "—")).title())
m3.metric("Attempts", state.get("retry_count", "—"))
m4.metric("Duration", f"{state.get('duration_sec', 0)}s")

if verdict.get("root_cause_summary") or verdict.get("suggested_fix_direction"):
    with st.container(border=True):
        if verdict.get("root_cause_summary"):
            st.markdown(f"**Root cause** — {verdict['root_cause_summary']}")
        if verdict.get("suggested_fix_direction"):
            st.markdown(f"**Fix direction** — {verdict['suggested_fix_direction']}")

tab_triage, tab_test, tab_sandbox, tab_report = st.tabs(
    ["Triage", "Reproduction test", "Sandbox output", "GitHub comment"]
)

with tab_triage:
    st.markdown(f"**Subdirectory:** `{state.get('target_subdir', '')}`")
    suspects = state.get("suspect_files", [])
    st.markdown("**Suspect files**")
    for f in suspects:
        st.markdown(f"- `{f}`")
    if not suspects:
        st.caption("None identified.")
    st.markdown("**Hypothesis**")
    st.info(state.get("hypothesis", "") or "—")

with tab_test:
    st.code(state.get("test_code", "") or "(no test generated)", language=state.get("target_language", "python"), line_numbers=True)

with tab_sandbox:
    st.caption(f"Exit code: {state.get('exit_code')}")
    with st.expander("STDOUT", expanded=True):
        st.code(state.get("stdout", "") or "(empty)", language="text")
    with st.expander("STDERR", expanded=bool(state.get("stderr"))):
        st.code(state.get("stderr", "") or "(empty)", language="text")

with tab_report:
    report = verdict.get("github_markdown_report", "")
    st.caption("Use the copy icon in the top-right of the block to copy the Markdown.")
    st.code(report or "(no report)", language="markdown")
    with st.expander("Preview"):
        st.markdown(report)
