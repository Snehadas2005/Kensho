import functools
import re
import sqlite3
import time
from pathlib import Path
from langgraph.graph import StateGraph, END
from core.llm import (
    call_structured_llm,
    detail_text,
    merge_detail,
    pop_telemetry,
    summarize_calls,
    telemetry_node,
)
from core.schemas import AgentState, TriageDecision, ReproTestDraft, JudgeVerdict
from core.tools import get_git_blame, grep_codebase, read_file_snippet
from core.sandbox import PolyglotSandbox
from core.ast_symbols import build_symbol_map, format_hits, load_symbol_map, save_symbol_map
from core.checkout import create_pr_worktree, remove_pr_worktree
from memory.updater import update_profile_from_run
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


def _safe_save_run(repo_root: Path, state: dict) -> None:
    """Stores the run in ChromaDB. Offline / model-download / disk errors must never fail a finished run."""
    try:
        _get_vector_memory(repo_root).save_run(state)
    except Exception:  # noqa: BLE001
        pass


def _symbol_map(kensho_dir: Path, repo_dir: Path):
    """Cached tree-sitter symbol map; built (and cached) on first use if `init` has not made it yet."""
    cached = load_symbol_map(kensho_dir)
    if cached is not None and cached.files:
        return cached
    try:
        fresh = build_symbol_map(repo_dir)
        save_symbol_map(fresh, kensho_dir)
        return fresh
    except Exception:  # noqa: BLE001
        return None


def _identifiers(*texts: str, limit: int = 25) -> list[str]:
    """CamelCase / snake_case identifiers from free text (deterministic AST lookup keys)."""
    seen: list[str] = []
    for text in texts:
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text or ""):
            if (any(c.isupper() for c in tok[1:]) or "_" in tok) and tok not in seen:
                seen.append(tok)
    return seen[:limit]


def telemetered(node_name: str):
    """Tags LLM calls inside a node and merges tokens/latency into state['model_telemetry']."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state):
            with telemetry_node(node_name):
                delta = fn(state)
            calls = pop_telemetry(node_name)
            if calls:
                _, detail = summarize_calls(calls)
                details = dict(state.get("telemetry_detail") or {})
                details[node_name] = merge_detail(details.get(node_name), detail)
                delta = dict(delta or {})
                delta["telemetry_detail"] = details
                delta["model_telemetry"] = {k: detail_text(v) for k, v in details.items()}
            return delta
        return wrapper
    return decorator


class _ThreadedGraph:
    """Adds a unique LangGraph thread_id so callers can keep using graph.stream(state, ...)."""

    def __init__(self, compiled):
        self._compiled = compiled

    def _config(self, state, config):
        if config:
            return config
        tid = f"{state.get('repo_name', 'repo')}-{state.get('item_type', 'item')}-{state.get('item_number', 0)}-{int(time.time())}"
        return {"configurable": {"thread_id": tid}}

    def stream(self, state, config=None, **kwargs):
        return self._compiled.stream(state, self._config(state, config), **kwargs)

    def invoke(self, state, config=None, **kwargs):
        return self._compiled.invoke(state, self._config(state, config), **kwargs)

    def __getattr__(self, name):
        return getattr(self._compiled, name)


def _compile_with_checkpointer(workflow, repo_dir: Path):
    """SQLite-persisted run state in <repo>/.kensho/checkpoints.sqlite (falls back to in-memory)."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        kensho_dir = repo_dir / ".kensho"
        kensho_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(kensho_dir / "checkpoints.sqlite"), check_same_thread=False)
        return _ThreadedGraph(workflow.compile(checkpointer=SqliteSaver(conn)))
    except Exception:  # noqa: BLE001  (package missing / db locked: keep the agent usable)
        return workflow.compile()


def build_triage_graph(repo_dir: Path, stack_spec=None):
    sandbox = PolyglotSandbox(repo_dir=repo_dir, timeout_sec=180, stack_spec=stack_spec)

    def _wd(state) -> Path:
        """Directory the run operates on: the PR worktree if one was created, else the local repo."""
        return Path(state.get("work_dir") or repo_dir)

    def prepare_checkout_node(state: dict) -> dict:
        if state.get("item_type") != "pr":
            return {"work_dir": "", "checkout_note": ""}
        path, note = create_pr_worktree(repo_dir, int(state["item_number"]))
        return {"work_dir": str(path) if path else "", "checkout_note": note}

    @telemetered("triage")
    def triage_node(state: dict) -> dict:
        repo_root = Path(state.get("repo_root", repo_dir))
        wd = _wd(state)
        title = state.get("title", "")
        body = state.get("body", "")
        current_id = f"{state.get('item_type', 'issue')}-{state.get('item_number', 0)}"

        # RAG Semantic Memory Lookup (never fatal: memory is an enhancement)
        try:
            memory = _get_vector_memory(repo_root)
            similar_bugs = memory.query_similar(title, body, top_k=3, exclude_id=current_id)
        except Exception:  # noqa: BLE001
            similar_bugs = []

        # Deterministic AST lookup: real symbols whose names appear in the issue text
        symbol_map = _symbol_map(repo_root / ".kensho", wd)
        ast_hits = symbol_map.find(_identifiers(title, body, state.get("diff_summary", "")[:2000])) if symbol_map else []
        ast_section = (
            "REAL CODE SYMBOLS MENTIONED IN THE ISSUE (tree-sitter, ground truth):\n"
            + "\n".join(format_hits(ast_hits)) + "\n\n"
            if ast_hits else ""
        )
        
        rag_context = ""
        if similar_bugs:
            rag_context = "\n\nSIMILAR PAST BUGS & REPRODUCTIONS (RAG CONTEXT):\n"
            for sb in similar_bugs:
                rag_context += f"- [{sb.item_id}] {sb.title} ({sb.status})\n  Root Cause: {sb.root_cause}\n  Test Snippet:\n{sb.test_snippet[:400]}\n\n"
        
        layout = layout_hint(wd)
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
            f"{ast_section}"
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
            real = resolve_existing_path(wd, fpath)
            if real and real not in valid_files:
                valid_files.append(real)
        if not valid_files and decision.target_language == "go":
            mod_guess = find_go_module_root(wd, decision.target_subdir, [])
            if mod_guess is not None:
                words = list(decision.search_keywords) + [title, decision.hypothesis]
                valid_files = find_candidate_files(wd, mod_guess, words)
        if not valid_files and symbol_map is not None:
            # Language-agnostic fallback (covers Python too): files holding symbols named in the keywords
            keyword_hits = symbol_map.find(list(decision.search_keywords) + _identifiers(title, body))
            for path, _sym in keyword_hits:
                if path not in valid_files and (wd / path).exists():
                    valid_files.append(path)
                if len(valid_files) >= 3:
                    break
        target_subdir = decision.target_subdir
        if not (wd / target_subdir).exists():
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
            "similar_prs": [m.model_dump() for m in similar_bugs],
            "ast_hits": format_hits(ast_hits),
        }

    @telemetered("explorer_coder")
    def explorer_coder_node(state: AgentState) -> dict:
        wd = _wd(state)
        code_context = state.get("code_context", "")
        blame_context = state.get("blame_context", "")
        if not code_context:
            snippets = []
            for fpath in state["suspect_files"][:4]:
                snippets.append(read_file_snippet(wd, fpath, start_line=1, max_lines=400))
            for kw in state["search_keywords"][:3]:
                snippets.append(f"=== GREP: {kw} ===\n" + grep_codebase(wd, kw, subdir=state["target_subdir"]))
            code_context = "\n\n".join(snippets)[:24000]
            # git blame around the most relevant lines of the first suspect file (recent changes = prime suspects)
            if state["suspect_files"]:
                first = state["suspect_files"][0]
                line_no = 1
                for hit in state.get("ast_hits") or []:
                    if hit.startswith(f"{first}:"):
                        line_no = int(hit.split(":")[1].split()[0])
                        break
                blame_context = get_git_blame(wd, first, max(1, line_no - 10), line_no + 30)[:3000]
                code_context += "\n\n=== RECENT CHANGES (git blame) ===\n" + blame_context
            if state["target_language"] == "go":
                mod_for_summary = find_go_module_root(wd, state["target_subdir"], state["suspect_files"])
                if mod_for_summary is not None:
                    prio = [str(Path(x).parent.as_posix()) for x in state["suspect_files"]]
                    code_context = go_module_summary(wd, mod_for_summary, prio) + "\n\n" + code_context

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
            mod = find_go_module_root(wd, state["target_subdir"], state["suspect_files"])
            if mod is not None:
                test_dir = resolve_go_test_dir(wd, mod, state["target_subdir"], state["suspect_files"])
                pkg = detect_go_package(wd, test_dir)
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
            "blame_context": blame_context,
        }

    def sandbox_node(state: AgentState) -> dict:
        wd = _wd(state)
        runner = sandbox if wd == repo_dir else PolyglotSandbox(repo_dir=wd, timeout_sec=180, stack_spec=stack_spec)
        res = runner.run_repro(
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

    @telemetered("judge")
    def judge_node(state: AgentState) -> dict:
        repo_root = Path(state.get("repo_root", repo_dir))

        forced = deterministic_verdict(state["exit_code"], state["stderr"]) or guards.deterministic_verdict(state)
        if forced is not None:
            verdict_dict = forced.model_dump()
            state["verdict"] = verdict_dict
            _safe_save_run(repo_root, state)
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
        
        _safe_save_run(repo_root, state)
        return {"verdict": verdict_dict}

    def route_after_judge(state: AgentState) -> str:
        status = state["verdict"].get("status", "NOT_REPRODUCED")
        if is_infra_failure(state["exit_code"], state["stderr"]):
            return "done"
        if status == "TEST_ERROR" and state["retry_count"] <= state["max_retries"]:
            return "retry_coder"
        return "done"

    def memory_updater_node(state: AgentState) -> dict:
        """Post-run: update profile.json from this run, then remove the PR worktree."""
        repo_root = Path(state.get("repo_root", repo_dir))
        try:
            update_profile_from_run(repo_root / ".kensho", dict(state))
        except Exception:  # noqa: BLE001  (profile upkeep must never break a finished run)
            pass
        finally:
            if state.get("work_dir"):
                remove_pr_worktree(repo_dir, state.get("work_dir"), int(state.get("item_number", 0)) or None)
        return {"checkout_note": state.get("checkout_note", "")}

    workflow = StateGraph(AgentState)
    workflow.add_node("prepare_checkout", prepare_checkout_node)
    workflow.add_node("triage", triage_node)
    workflow.add_node("explorer_coder", explorer_coder_node)
    workflow.add_node("sandbox", sandbox_node)
    workflow.add_node("judge", judge_node)
    workflow.add_node("memory_updater", memory_updater_node)

    workflow.set_entry_point("prepare_checkout")
    workflow.add_edge("prepare_checkout", "triage")
    workflow.add_edge("triage", "explorer_coder")
    workflow.add_edge("explorer_coder", "sandbox")
    workflow.add_edge("sandbox", "judge")
    workflow.add_conditional_edges(
        "judge",
        route_after_judge,
        {
            "retry_coder": "explorer_coder",
            "done": "memory_updater",
        },
    )
    workflow.add_edge("memory_updater", END)

    return _compile_with_checkpointer(workflow, repo_dir)
