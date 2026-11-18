from pathlib import Path
from langgraph.graph import StateGraph, END
from core.llm import call_structured_llm
from core.schemas import AgentState, TriageDecision, ReproTestDraft, JudgeVerdict
from core.tools import read_file_snippet, grep_codebase
from core.sandbox import PolyglotSandbox
from memory.vector_store import RepoVectorMemory


INFRA_ERROR_MARKER = "Sandbox execution error"


def is_infra_failure(exit_code: int, stderr: str) -> bool:
    """Timeouts and Docker/runtime errors: retrying the coder cannot fix these."""
    return exit_code == 124 or INFRA_ERROR_MARKER in (stderr or "")


def deterministic_verdict(exit_code: int, stderr: str) -> JudgeVerdict | None:
    """Verdicts that must never be left to the LLM. Returns None when the LLM should judge."""
    if is_infra_failure(exit_code, stderr):
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary="Sandbox execution timed out or hit a container runtime error.",
            suggested_fix_direction="Check that Docker is running and the images are built, or narrow the target package.",
            github_markdown_report="Sandbox execution did not finish, so the bug could not be verified.",
        )
    if exit_code == 0:
        return JudgeVerdict(
            status="NOT_REPRODUCED",
            root_cause_summary="The generated test passed, so the reported behavior was not triggered.",
            suggested_fix_direction="Refine the reproduction with more details from the issue, or the bug may already be fixed.",
            github_markdown_report="I could not reproduce this: the generated test passed (exit code 0).",
        )
    return None


def _get_vector_memory(repo_root: Path) -> RepoVectorMemory:
    kensho_dir = repo_root / ".kensho"
    return RepoVectorMemory(kensho_dir)


def build_triage_graph(repo_dir: Path, stack_spec=None):
    sandbox = PolyglotSandbox(repo_dir=repo_dir, timeout_sec=180, stack_spec=stack_spec)

    def triage_node(state: dict) -> dict:
        repo_root = Path(state.get("repo_root", repo_dir))
        title = state.get("title", "")
        body = state.get("body", "")
        
        # RAG Semantic Memory Lookup
        memory = _get_vector_memory(repo_root)
        similar_bugs = memory.query_similar(title, body, top_k=2)
        
        rag_context = ""
        if similar_bugs:
            rag_context = "\n\nSIMILAR PAST BUGS & REPRODUCTIONS (RAG CONTEXT):\n"
            for sb in similar_bugs:
                rag_context += f"- [{sb.item_id}] {sb.title} ({sb.status})\n  Root Cause: {sb.root_cause}\n  Test Snippet:\n{sb.test_snippet[:400]}\n\n"

        system_prompt = (
            "You are a Senior Maintainer triaging a GitHub issue or PR. "
            "Use the Repo Architecture Map and any retrieved historical bug context to determine whether this is primarily a Python or Go issue, "
            "pinpoint the exact subdirectory and suspect files, and form a bug-reproduction hypothesis."
        )
        user_prompt = (
            f"REPO ARCHITECTURE MAP:\n{state['repo_map_json']}\n\n"
            f"{rag_context}"
            f"TYPE: {state['item_type'].upper()} #{state['item_number']}\n"
            f"TITLE: {state['title']}\n\n"
            f"DESCRIPTION:\n{state['body']}\n\n"
            f"DIFF SUMMARY:\n{state['diff_summary']}"
        )
        decision = call_structured_llm(system_prompt, user_prompt, TriageDecision)
        return {
            "target_language": decision.target_language,
            "target_subdir": decision.target_subdir,
            "suspect_files": decision.suspect_files,
            "search_keywords": decision.search_keywords,
            "hypothesis": decision.hypothesis,
            "retry_count": 0,
        }

    def explorer_coder_node(state: AgentState) -> dict:
        code_context = state.get("code_context", "")
        if not code_context:
            snippets = []
            for fpath in state["suspect_files"][:4]:
                snippets.append(read_file_snippet(repo_dir, fpath, start_line=1, max_lines=160))
            for kw in state["search_keywords"][:3]:
                snippets.append(f"=== GREP: {kw} ===\n" + grep_codebase(repo_dir, kw, subdir=state["target_subdir"]))
            code_context = "\n\n".join(snippets)[:14000]

        retry_count = state.get("retry_count", 0)
        feedback_section = ""
        if retry_count > 0:
            feedback_section = (
                f"\n\nPREVIOUS ATTEMPT FAILED DUE TO TEST/ENV ERROR (Attempt {retry_count}):\n"
                f"Previous Code:\n{state['test_code']}\n\n"
                f"Sandbox Exit Code: {state['exit_code']}\n"
                f"STDOUT:\n{state['stdout'][-2000:]}\n"
                f"STDERR:\n{state['stderr'][-2000:]}\n"
                "Fix the imports, function signatures, or syntax in your test so it actually executes the target code."
            )

        lang_instructions = (
            "Write a standalone pytest file with at least one `def test_repro_*():` function. "
            "Do NOT make external network calls. Assert the expected valid behavior so that if the bug exists, "
            "pytest fails with an AssertionError or unhandled exception."
            if state["target_language"] == "python"
            else "Write a standalone Go unit test in the same package with `func TestRepro(t *testing.T)`. "
            "Do NOT make external network calls. Fail with `t.Fatalf(...)` if the buggy behavior occurs."
        )

        system_prompt = (
            "You are an Autonomous Bug-Reproduction Engineer. Inspect the real source code context "
            f"and write a minimal, deterministic {state['target_language']} test that triggers the reported bug.\n"
            f"{lang_instructions}"
        )
        user_prompt = (
            f"ISSUE/PR: #{state['item_number']} - {state['title']}\n"
            f"HYPOTHESIS: {state['hypothesis']}\n\n"
            f"CODEBASE CONTEXT:\n{code_context}"
            f"{feedback_section}"
        )

        draft = call_structured_llm(system_prompt, user_prompt, ReproTestDraft)
        return {
            "code_context": code_context,
            "test_code": draft.test_code,
        }

    def sandbox_node(state: AgentState) -> dict:
        res = sandbox.run_repro(
            language=state["target_language"],
            test_code=state["test_code"],
            target_subdir=state["target_subdir"],
        )
        return {
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "duration_sec": res.duration_sec,
            "retry_count": state.get("retry_count", 0) + 1,
        }

    def judge_node(state: AgentState) -> dict:
        repo_root = Path(state.get("repo_root", repo_dir))
        memory = _get_vector_memory(repo_root)

        forced = deterministic_verdict(state["exit_code"], state["stderr"])
        if forced is not None:
            verdict_dict = forced.model_dump()
            state["verdict"] = verdict_dict
            memory.save_run(state)
            return {"verdict": verdict_dict}

        system_prompt = (
            "You are a Principal Engineer verifying a bug reproduction run inside an isolated Docker sandbox. "
            "Compare the original issue/PR description with the actual pytest/go test output.\n"
            "- Mark 'TEST_ERROR' ONLY if the generated test itself had a syntax error, ImportError, or build failure before reaching target logic.\n"
            "- Mark 'VERIFIED' if the test executed the target module and failed with an AssertionError, panic, or exception matching the bug.\n"
            "- Mark 'NOT_REPRODUCED' if the test passed (exit code 0)."
        )
        user_prompt = (
            f"ISSUE/PR #{state['item_number']}: {state['title']}\n"
            f"REPORTED DESCRIPTION:\n{state['body'][:4000]}\n\n"
            f"GENERATED TEST CODE:\n{state['test_code']}\n\n"
            f"SANDBOX EXIT CODE: {state['exit_code']} (Duration: {state['duration_sec']}s)\n"
            f"STDOUT:\n{state['stdout']}\n\n"
            f"STDERR:\n{state['stderr']}"
        )
        verdict = call_structured_llm(system_prompt, user_prompt, JudgeVerdict)
        verdict_dict = verdict.model_dump()
        state["verdict"] = verdict_dict
        
        memory.save_run(state)
        return {"verdict": verdict_dict}

    def route_after_judge(state: AgentState) -> str:
        status = state["verdict"].get("status", "NOT_REPRODUCED")
        if is_infra_failure(state["exit_code"], state["stderr"]):
            return "done"
        if status == "TEST_ERROR" and state["retry_count"] <= state["max_retries"]:
            return "retry_coder"
        return "done"

    workflow = StateGraph(AgentState)
    workflow.add_node("triage", triage_node)
    workflow.add_node("explorer_coder", explorer_coder_node)
    workflow.add_node("sandbox", sandbox_node)
    workflow.add_node("judge", judge_node)

    workflow.set_entry_point("triage")
    workflow.add_edge("triage", "explorer_coder")
    workflow.add_edge("explorer_coder", "sandbox")
    workflow.add_edge("sandbox", "judge")
    workflow.add_conditional_edges(
        "judge",
        route_after_judge,
        {
            "retry_coder": "explorer_coder",
            "done": END,
        },
    )

    return workflow.compile()