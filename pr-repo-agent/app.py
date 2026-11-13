import json
from pathlib import Path
import streamlit as st

from core.github_client import fetch_github_item
from core.graph import build_triage_graph

st.set_page_config(page_title="Kensho", layout="wide")
st.title("Kensho (v1)")

WORKSPACE_DIR = Path("workspace")
REPO_DIR = WORKSPACE_DIR / "pipelines"
MAP_PATH = WORKSPACE_DIR / "repo_map.json"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"

with st.sidebar:
    st.header("Target Configuration")
    repo_name = st.text_input("GitHub Repository", value="kubeflow/pipelines")
    item_type = st.selectbox("Item Type", ["issue", "pr"])
    item_number = st.number_input("Issue / PR Number", min_value=1, value=13401, step=1)
    max_retries = st.slider("Max Self-Correction Retries", 0, 3, 2)
    run_btn = st.button("Run Triage & Repro", type="primary", use_container_width=True)

    if MAP_PATH.exists():
        repo_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        st.divider()
        st.caption(f"Indexed SHA: `{repo_map.get('last_indexed_sha', '')[:7]}`")
        st.caption(f"Indexed Modules: {len(repo_map.get('modules', []))}")

state = None

if run_btn:
    if not MAP_PATH.exists():
        st.error("`workspace/repo_map.json` not found. Run `python main.py index` in your terminal first.")
    else:
        with st.spinner(f"Fetching {item_type.upper()} #{item_number} and running agent loop..."):
            work_item = fetch_github_item(repo_name, int(item_number), item_type=item_type)
            graph = build_triage_graph(REPO_DIR)
            state = graph.invoke({
                "repo_name": repo_name,
                "item_type": item_type,
                "item_number": int(item_number),
                "title": work_item.title,
                "body": work_item.body,
                "diff_summary": work_item.diff_summary,
                "repo_map_json": MAP_PATH.read_text(encoding="utf-8"),
                "retry_count": 0,
                "max_retries": max_retries,
            })
            LAST_RUN_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
elif LAST_RUN_PATH.exists():
    state = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))

if state:
    verdict = state.get("verdict", {})
    status = verdict.get("status", "UNKNOWN")

    if status == "VERIFIED":
        st.success(f"VERIFIED REPRODUCTION — #{state['item_number']}: {state['title']}")
    elif status == "NOT_REPRODUCED":
        st.warning(f"NOT REPRODUCED — #{state['item_number']}: {state['title']}")
    else:
        st.error(f"TEST / ENVIRONMENT ERROR — #{state['item_number']}: {state['title']}")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("1. Triage & Localization")
        st.markdown(f"**Runtime:** `{state.get('target_language')}` (`{state.get('target_subdir')}`)")
        st.markdown(f"**Suspect Files:** `{', '.join(state.get('suspect_files', []))}`")
        st.info(state.get("hypothesis", ""))

        st.subheader("2. Generated Reproduction Test")
        st.code(state.get("test_code", ""), language=state.get("target_language", "python"))

    with col2:
        st.subheader(f"3. Docker Sandbox Output (Exit Code: {state.get('exit_code')})")
        with st.expander("STDOUT", expanded=True):
            st.code(state.get("stdout", "") or "(empty)", language="text")
        with st.expander("STDERR", expanded=bool(state.get("stderr"))):
            st.code(state.get("stderr", "") or "(empty)", language="text")

        st.subheader("4. Ready-to-Post GitHub Comment")
        st.text_area("Copy Markdown", value=verdict.get("github_markdown_report", ""), height=220)
else:
    st.info("Enter an Issue or PR number in the sidebar and click **Run Triage & Repro** (or run `python main.py issue <num>` in your terminal).")