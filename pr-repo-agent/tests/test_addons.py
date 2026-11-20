"""AST symbols, LLM telemetry, git tools, PR checkout, and the full graph (fake LLM + fake sandbox)."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import HashEmbedding

import core.graph as graph_mod
from core import llm
from core.ast_symbols import build_symbol_map, load_symbol_map, save_symbol_map, symbol_digest
from core.checkout import create_pr_worktree, remove_pr_worktree
from core.sandbox import SandboxResult
from core.schemas import JudgeVerdict, ReproTestDraft, TriageDecision
from core.tools import get_git_blame, search_similar_prs
from memory.vector_store import RepoVectorMemory


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t.t")
    git(path, "config", "user.name", "Tester")
    (path / "helper").mkdir()
    (path / "helper" / "core.py").write_text(
        "class Copier:\n    def copy_fields(self, a, b):\n        return a == b\n\n\ndef top_level(x):\n    return x\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial")
    return path


# ------------------------------------------------------------------ tree-sitter AST
def test_ast_symbols_python_and_go(tmp_path):
    (tmp_path / "a.py").write_text("class Foo:\n    def bar(self, x):\n        pass\n\n\ndef top(a):\n    pass\n")
    (tmp_path / "b.go").write_text(
        "package x\n\ntype Srv struct{}\n\ntype I interface{ M() }\n\nfunc (s *Srv) Run(n int) error { return nil }\n\nfunc Helper() {}\n")
    (tmp_path / "skip_test.go").write_text("package x\nfunc TestIgnored() {}\n")
    m = build_symbol_map(tmp_path)
    assert "skip_test.go" not in m.files
    py = {(s.kind, s.name, s.parent) for s in m.files["a.py"]}
    assert ("class", "Foo", "") in py and ("method", "bar", "Foo") in py and ("function", "top", "") in py
    go = {(s.kind, s.name, s.parent) for s in m.files["b.go"]}
    assert ("struct", "Srv", "") in go and ("interface", "I", "") in go
    assert ("method", "Run", "Srv") in go and ("function", "Helper", "") in go
    run = [s for s in m.files["b.go"] if s.name == "Run"][0]
    assert "Run(n int) error" in run.signature and run.line == 7
    assert m.find(["run"])[0][0] == "b.go"
    assert "b.go: Srv, I, Srv.Run, Helper" in symbol_digest(m)


def test_ast_symbol_map_roundtrip(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    save_symbol_map(build_symbol_map(tmp_path, "abc"), tmp_path / ".kensho")
    loaded = load_symbol_map(tmp_path / ".kensho")
    assert loaded.root_sha == "abc" and "a.py" in loaded.files


# ------------------------------------------------------------------ telemetry
def test_llm_telemetry_records_tokens_latency_and_failover(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    llm.pop_telemetry()
    attempts = []

    def fake_completion(**kwargs):
        attempts.append(kwargs["model"])
        if len(attempts) == 1:
            raise RuntimeError("429")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=ReproTestDraft(explanation="e", test_code="c").model_dump_json()))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)
    with llm.telemetry_node("explorer_coder"):
        out = llm.call_structured_llm("s", "u", ReproTestDraft)
    assert out.test_code == "c"
    calls = llm.pop_telemetry("explorer_coder")
    text, detail = llm.summarize_calls(calls)
    assert detail["prompt_tokens"] == 100 and detail["completion_tokens"] == 20 and detail["failovers"] == 1
    assert "120 tok" in text and "1 failover" in text
    merged = llm.merge_detail(detail, detail)
    assert merged["calls"] == 4 and merged["prompt_tokens"] == 200


# ------------------------------------------------------------------ git blame / similar PR tools
def test_git_blame_tool(tmp_path):
    repo = make_repo(tmp_path / "r")
    out = get_git_blame(repo, "helper/core.py", 1, 3)
    assert "Tester" in out and "class Copier" in out
    assert "[File not found" in get_git_blame(repo, "nope.py")
    assert "[File not found" in get_git_blame(repo, "../../etc/passwd")


def test_search_similar_prs_formats_matches():
    fake = SimpleNamespace(query_similar=lambda t, b, top_k: [
        SimpleNamespace(item_id="issue-1", title="T", status="VERIFIED", distance=0.1, root_cause="because")])
    assert "issue-1" in search_similar_prs(fake, "t") and "because" in search_similar_prs(fake, "t")
    empty = SimpleNamespace(query_similar=lambda t, b, top_k: [])
    assert "No similar" in search_similar_prs(empty, "t")


# ------------------------------------------------------------------ PR branch checkout
def test_pr_worktree_contains_pr_code_not_local_tree_and_is_cleaned_up(tmp_path):
    upstream = make_repo(tmp_path / "upstream")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(upstream), str(local)], check=True)
    git(local, "config", "user.email", "t@t.t"); git(local, "config", "user.name", "T")
    git(local, "remote", "rename", "origin", "upstream")

    # a PR branch exists on the upstream as refs/pull/7/head
    git(upstream, "checkout", "-q", "-b", "pr-branch")
    (upstream / "helper" / "core.py").write_text("PR_VERSION = True\n")
    git(upstream, "commit", "-q", "-am", "pr change")
    git(upstream, "update-ref", "refs/pull/7/head", "HEAD")
    git(upstream, "checkout", "-q", "main")

    path, note = create_pr_worktree(local, 7)
    assert path is not None and "PR #7" in note
    assert (path / "helper" / "core.py").read_text() == "PR_VERSION = True\n"          # PR code
    assert "class Copier" in (local / "helper" / "core.py").read_text()                 # local tree untouched

    remove_pr_worktree(local, path, 7)
    assert not path.exists()
    assert "refs/kensho/pr-7" not in git(local, "for-each-ref")


def test_pr_worktree_failure_falls_back_with_a_note(tmp_path):
    repo = make_repo(tmp_path / "r")
    path, note = create_pr_worktree(repo, 999)
    assert path is None and "using local working tree" in note


# ------------------------------------------------------------------ full graph, fake LLM + fake sandbox
def _fake_llm_factory(verdict_status):
    def fake(system_prompt, user_prompt, schema, timeout_sec=20):
        llm._record_call("gemini/test-model", 50, 10, 0.5, ok=True)
        if schema is TriageDecision:
            return TriageDecision(target_language="python", target_subdir="helper", suspect_files=["helper/core.py"],
                                  search_keywords=["copy_fields"], hypothesis="equality is too strict")
        if schema is ReproTestDraft:
            return ReproTestDraft(explanation="e", test_code="def test_repro_x():\n    assert False\n")
        return JudgeVerdict(status=verdict_status, root_cause_summary="rc", suggested_fix_direction="fix", github_markdown_report="md")
    return fake


@pytest.fixture
def fake_run(monkeypatch):
    seen = {}

    def fake_sandbox(self, language, test_code, target_subdir="", suspect_files=None):
        seen["repo_dir"] = Path(self.repo_dir)
        return SandboxResult(language=language, exit_code=1, stdout="1 failed", stderr="AssertionError", duration_sec=0.1)

    monkeypatch.setattr(graph_mod.PolyglotSandbox, "run_repro", fake_sandbox)
    monkeypatch.setattr(graph_mod, "call_structured_llm", _fake_llm_factory("VERIFIED"))
    monkeypatch.setattr(graph_mod, "_get_vector_memory",
                        lambda root: RepoVectorMemory(Path(root) / ".kensho", embedding_function=HashEmbedding()))
    return seen


def _initial(repo: Path, item_type="issue", number=3) -> dict:
    return {"repo_name": "o/r", "repo_root": str(repo), "item_type": item_type, "item_number": number,
            "title": "Copier.copy_fields compares too strictly", "body": "copy_fields breaks", "diff_summary": "",
            "repo_map_json": "{}", "retry_count": 0, "max_retries": 1}


def test_full_graph_run_populates_new_state_and_persists_checkpoints(tmp_path, fake_run):
    repo = make_repo(tmp_path / "repo")
    (repo / ".kensho").mkdir()
    (repo / ".kensho" / "profile.json").write_text(
        '{"github_username":"s","preferred_languages":[],"favorite_subdirs":[],"experience_level":"beginner","focus_areas":[],"summary_bio":"b"}')

    g = graph_mod.build_triage_graph(repo)
    final = {}
    nodes = []
    for update in g.stream(_initial(repo), stream_mode="updates"):
        for node, delta in update.items():
            nodes.append(node)
            final.update(delta or {})

    assert nodes == ["prepare_checkout", "triage", "explorer_coder", "sandbox", "judge", "memory_updater"]
    assert final["verdict"]["status"] == "VERIFIED"
    # AST symbols found deterministically from the issue text
    assert any("Copier.copy_fields" in h for h in final["ast_hits"])
    # git blame went into the coder context
    assert "git blame" in final["blame_context"]
    # per-node telemetry (rendered by app.py's existing "Models" line)
    assert set(final["model_telemetry"]) == {"triage", "explorer_coder", "judge"}
    assert "60 tok" in final["model_telemetry"]["triage"]
    # memory updater touched the profile
    import json
    profile = json.loads((repo / ".kensho" / "profile.json").read_text())
    assert profile["module_counts"] == {"helper": 1} and profile["language_counts"] == {"Python": 1}
    # SQLite checkpointer file exists
    assert (repo / ".kensho" / "checkpoints.sqlite").stat().st_size > 0


def test_graph_issue_run_uses_local_tree_and_pr_run_uses_worktree(tmp_path, fake_run):
    upstream = make_repo(tmp_path / "upstream")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(upstream), str(local)], check=True)
    git(local, "remote", "rename", "origin", "upstream")
    git(upstream, "checkout", "-q", "-b", "prb")
    (upstream / "helper" / "core.py").write_text("class Copier:\n    def copy_fields(self):\n        return 'PR'\n")
    git(upstream, "commit", "-q", "-am", "pr"); git(upstream, "update-ref", "refs/pull/9/head", "HEAD")

    g = graph_mod.build_triage_graph(local)
    final = {}
    for update in g.stream(_initial(local, "pr", 9), stream_mode="updates"):
        for _, delta in update.items():
            final.update(delta or {})
    assert "PR #9" in final["checkout_note"]
    assert ".kensho" in str(fake_run["repo_dir"]) and "pr-9" in str(fake_run["repo_dir"])   # sandbox saw the worktree
    assert not Path(final["work_dir"]).exists()                                             # cleaned up afterwards

    final = {}
    for update in g.stream(_initial(local, "issue", 4), stream_mode="updates"):
        for _, delta in update.items():
            final.update(delta or {})
    assert fake_run["repo_dir"].resolve() == local.resolve()


def test_memory_failure_does_not_break_a_finished_run(tmp_path, fake_run, monkeypatch):
    repo = make_repo(tmp_path / "repo")

    def boom(root):
        raise RuntimeError("offline: cannot download embedding model")

    monkeypatch.setattr(graph_mod, "_get_vector_memory", boom)
    g = graph_mod.build_triage_graph(repo)
    final = {}
    for update in g.stream(_initial(repo), stream_mode="updates"):
        for _, delta in update.items():
            final.update(delta or {})
    assert final["verdict"]["status"] == "VERIFIED" and final["similar_prs"] == []
