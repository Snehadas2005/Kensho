from pathlib import Path
from langgraph.graph import StateGraph, END
from core.llm import call_structured_llm
from core.schemas import AgentState, TriageDecision, ReproTestDraft, JudgeVerdict
from core.tools import read_file_snippet, grep_codebase
from core.sandbox import PolyglotSandbox
from memory.vector_store import RepoVectorMemory
from core import guards
from core.go_module import (
    detect_go_package,
    find_candidate_files,
    find_go_module_root,
    go_module_summary,
    normalize_go_test,
    layout_hint,
    resolve_existing_path,
    resolve_go_test_dir,
)


INFRA_ERROR_MARKER = "Sandbox execution error"


def is_infra_failure(exit_code: int, stderr: str) -> bool:
    """Timeouts, Docker/runtime errors and module/dependency setup errors: retrying the coder cannot fix these."""
    return (
        exit_code == 124
        or INFRA_ERROR_MARKER in (stderr or "")
        or (exit_code != 0 and guards.is_environment_error(stderr or ""))
    )


def deterministic_verdict(exit_code: int, stderr: str) -> JudgeVerdict | None:
    """Verdicts that must never be left to the LLM. Returns None when the LLM should judge."""
    if exit_code != 0 and guards.is_environment_error(stderr or ""):
        detail = guards.environment_error_detail(stderr or "")
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary=(
                "The sandbox could not build the project (module/dependency problem), so the test never ran. "
                f"Details: {detail}"
            ),
            suggested_fix_direction="Check Docker has internet for the first dependency download and that the Go module (go.mod) is in the checkout.",
            github_markdown_report="Sandbox environment/setup failure; the bug could not be verified.",
        )
    
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
        
        layout = layout_hint(repo_dir)
        layout_section = (
            f"REAL ON-DISK LAYOUT (only use paths that appear here or in the map; never invent paths):\n{layout}\n\n"
            if layout else ""
        )
        
        system_prompt = (
            "You are a Senior Maintainer triaging a GitHub issue or PR. "
            "Use the Repo Architecture Map and any retrieved historical bug context to determine whether this is primarily a Python or Go issue, "
            "pinpoint the exact subdirectory and suspect files, and form a bug-reproduction hypothesis."
        )
        user_prompt = (
            f"REPO ARCHITECTURE MAP:\n{state['repo_map_json']}\n\n"
            f"{layout_section}"
            f"{rag_context}"
            f"TYPE: {state['item_type'].upper()} #{state['item_number']}\n"
            f"TITLE: {state['title']}\n\n"
            f"DESCRIPTION:\n{state['body']}\n\n"
            f"DIFF SUMMARY:\n{state['diff_summary']}"
        )

        decision = call_structured_llm(system_prompt, user_prompt, TriageDecision)

        # The LLM sometimes invents paths. Keep only files that really exist (or resolve by basename).
        valid_files: list[str] = []
        for fpath in decision.suspect_files:
            real = resolve_existing_path(repo_dir, fpath)
            if real and real not in valid_files:
                valid_files.append(real)
        if not valid_files and decision.target_language == "go":
            mod_guess = find_go_module_root(repo_dir, decision.target_subdir, [])
            if mod_guess is not None:
                words = list(decision.search_keywords) + [title, decision.hypothesis]
                valid_files = find_candidate_files(repo_dir, mod_guess, words)
        target_subdir = decision.target_subdir
        if not (repo_dir / target_subdir).exists():
            target_subdir = str(Path(valid_files[0]).parent.as_posix()) if valid_files else ""
        if target_subdir == ".":
            target_subdir = ""

        return {
            "target_language": decision.target_language,
            "target_subdir": target_subdir,
            "suspect_files": valid_files or decision.suspect_files,
            "search_keywords": decision.search_keywords,
            "hypothesis": decision.hypothesis,
            "retry_count": 0,
        }

    def explorer_coder_node(state: AgentState) -> dict:
        code_context = state.get("code_context", "")
        if not code_context:
            snippets = []
            for fpath in state["suspect_files"][:4]:
                snippets.append(read_file_snippet(repo_dir, fpath, start_line=1, max_lines=400))
            for kw in state["search_keywords"][:3]:
                snippets.append(f"=== GREP: {kw} ===\n" + grep_codebase(repo_dir, kw, subdir=state["target_subdir"]))
            code_context = "\n\n".join(snippets)[:24000]
            if state["target_language"] == "go":
                mod_for_summary = find_go_module_root(repo_dir, state["target_subdir"], state["suspect_files"])
                if mod_for_summary is not None:
                    prio = [str(Path(x).parent.as_posix()) for x in state["suspect_files"]]
                    code_context = go_module_summary(repo_dir, mod_for_summary, prio) + "\n\n" + code_context

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

        go_pkg_note = ""
        if state["target_language"] == "go":
            mod = find_go_module_root(repo_dir, state["target_subdir"], state["suspect_files"])
            if mod is not None:
                test_dir = resolve_go_test_dir(repo_dir, mod, state["target_subdir"], state["suspect_files"])
                pkg = detect_go_package(repo_dir, test_dir)
                go_pkg_note = (
                    f" The test file will be placed in `{test_dir}` (Go module root `{mod}`)"
                    + (f" and MUST declare `package {pkg}`." if pkg else ".")
                )

        lang_instructions = (
            "Write a standalone pytest file with at least one `def test_repro_*():` function. "
            "Do NOT make external network calls. Assert the expected valid behavior so that if the bug exists, "
            "pytest fails with an AssertionError or unhandled exception."
            if state["target_language"] == "python"
            else "Write a standalone Go unit test in the same package with `func TestRepro(t *testing.T)`. "
            "Do NOT make external network calls. Fail with `t.Fatalf(...)` if the buggy behavior occurs. "
            "NEVER use `t.Skip` and never give up: import the real package code from the module shown in the context. "
            "The test function MUST be named exactly `TestRepro`; import the standard package as plain \"testing\" (no alias). "
            "The test must FAIL on the current buggy code and would PASS once the bug is fixed: assert the CORRECT expected behaviour, never assert the buggy outcome. "
            "Import ONLY packages listed in GO MODULE FACTS (or the standard library / required modules) and call ONLY exported identifiers that appear in the code shown; never invent packages, constructors or fields."
            + go_pkg_note
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
        test_code = normalize_go_test(draft.test_code) if state["target_language"] == "go" else draft.test_code
        return {
            "code_context": code_context,
            "test_code": test_code,
        }

    def sandbox_node(state: AgentState) -> dict:
        res = sandbox.run_repro(
            language=state["target_language"],
            test_code=state["test_code"],
            target_subdir=state["target_subdir"],
            suspect_files=state.get("suspect_files"),
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

        forced = deterministic_verdict(state["exit_code"], state["stderr"]) or guards.deterministic_verdict(state)
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