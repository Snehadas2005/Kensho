"""Memory subsystem: ContributorProfile, ChromaDB retrieval, profile updater. No network / API keys."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import HashEmbedding

from core.schemas import ContributorProfile, SimilarPRMatch
from memory.updater import module_key, sync_profile_from_merged_prs, update_profile_from_run
from memory.vector_store import RepoVectorMemory


def _profile(**kw) -> ContributorProfile:
    base = dict(github_username="sneha", preferred_languages=["Go"], favorite_subdirs=["workspaces"],
                experience_level="intermediate", focus_areas=["backend"], summary_bio="bio")
    base.update(kw)
    return ContributorProfile(**base)


def _write_profile(kensho: Path, profile: ContributorProfile) -> None:
    kensho.mkdir(parents=True, exist_ok=True)
    (kensho / "profile.json").write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def _state(status="VERIFIED", subdir="workspaces/controller/internal/helper", lang="go") -> dict:
    return {"verdict": {"status": status}, "target_subdir": subdir, "target_language": lang, "suspect_files": []}


# ------------------------------------------------------------------ profile schema
def test_old_profile_json_without_new_fields_still_loads():
    old = {"github_username": "a", "preferred_languages": ["Go"], "favorite_subdirs": [], "experience_level": "beginner",
           "focus_areas": [], "summary_bio": "x"}
    p = ContributorProfile.model_validate_json(json.dumps(old))
    assert p.focus_modules == [] and p.last_synced_pr == 0 and p.module_counts == {}


# ------------------------------------------------------------------ updater: after each run
def test_run_updates_focus_modules_and_languages(tmp_path):
    kensho = tmp_path / ".kensho"
    _write_profile(kensho, _profile(preferred_languages=["Python"]))
    update_profile_from_run(kensho, _state())
    update_profile_from_run(kensho, _state())
    p = ContributorProfile.model_validate_json((kensho / "profile.json").read_text())
    assert p.module_counts == {"workspaces/controller": 2}
    assert p.focus_modules == ["workspaces/controller"]
    assert "Go" in p.preferred_languages and p.language_counts["Go"] == 2 and p.updated_at


def test_test_error_runs_do_not_change_the_profile(tmp_path):
    kensho = tmp_path / ".kensho"
    _write_profile(kensho, _profile())
    update_profile_from_run(kensho, _state(status="TEST_ERROR"))
    p = ContributorProfile.model_validate_json((kensho / "profile.json").read_text())
    assert p.module_counts == {}


def test_no_profile_means_noop(tmp_path):
    assert update_profile_from_run(tmp_path / ".kensho", _state()) is None


# ------------------------------------------------------------------ updater: merged PRs
def test_merged_pr_sync_counts_files_and_remembers_last_pr(tmp_path):
    kensho = tmp_path / ".kensho"
    _write_profile(kensho, _profile(last_synced_pr=10))
    ctx = SimpleNamespace(kensho_dir=kensho, upstream_repo="o/r", github_username="sneha")
    calls = []

    def fake_fetch(repo, user, limit, since_pr):
        calls.append(since_pr)
        return [{"number": 12, "title": "t", "files": ["workspaces/backend/a.go", "workspaces/backend/b.go", "docs/x.md"],
                 "review_comments": [], "commit_messages": []}]

    profile, n = sync_profile_from_merged_prs(ctx, fetcher=fake_fetch, use_llm=False)
    assert n == 1 and calls == [10]                      # only PRs newer than the last synced one are requested
    assert profile.last_synced_pr == 12
    assert profile.module_counts["workspaces/backend"] == 2
    assert profile.language_counts["Go"] == 2


def test_merged_pr_sync_without_new_prs_changes_nothing(tmp_path):
    kensho = tmp_path / ".kensho"
    _write_profile(kensho, _profile())
    ctx = SimpleNamespace(kensho_dir=kensho, upstream_repo="o/r", github_username="sneha")
    profile, n = sync_profile_from_merged_prs(ctx, fetcher=lambda *a, **k: [], use_llm=False)
    assert n == 0 and profile.last_synced_pr == 0


def test_module_key():
    assert module_key("workspaces/controller/internal/x.go") == "workspaces/controller"
    assert module_key("sdk/python") == "sdk/python" or module_key("sdk/python") == "sdk"
    assert module_key("README.md") == ""


# ------------------------------------------------------------------ ChromaDB retrieval
def _run_state(item_no: int, title: str, status="VERIFIED", root="root cause") -> dict:
    return {"item_type": "issue", "item_number": item_no, "title": title, "target_subdir": "x", "hypothesis": "h",
            "test_code": "func TestRepro() {}", "verdict": {"status": status, "root_cause_summary": root}}


def test_vector_store_returns_most_similar_first(tmp_path):
    mem = RepoVectorMemory(tmp_path / ".kensho", embedding_function=HashEmbedding())
    mem.save_run(_run_state(1, "statefulset template rolling restart loop", root="DeepEqual on template"))
    mem.save_run(_run_state(2, "login page css broken on safari", root="css bug"))
    matches = mem.query_similar("statefulset rolling restart", "template changes", top_k=2)
    assert isinstance(matches[0], SimilarPRMatch)
    assert matches[0].item_id == "issue-1" and matches[0].root_cause == "DeepEqual on template"
    assert matches[0].distance <= matches[1].distance


def test_vector_store_skips_test_errors_and_can_exclude_current_item(tmp_path):
    mem = RepoVectorMemory(tmp_path / ".kensho", embedding_function=HashEmbedding())
    mem.save_run(_run_state(5, "broken test", status="TEST_ERROR"))
    assert mem.collection.count() == 0
    mem.save_run(_run_state(6, "same bug"))
    assert mem.query_similar("same bug", "", top_k=3, exclude_id="issue-6") == []


def test_vector_store_empty_returns_empty(tmp_path):
    mem = RepoVectorMemory(tmp_path / ".kensho", embedding_function=HashEmbedding())
    assert mem.query_similar("anything", "") == []


# ------------------------------------------------------------------ `kensho profile` command
def _cli_repo(tmp_path: Path, monkeypatch) -> Path:
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/sneha/notebooks.git"], check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path / ".kensho"


def test_profile_command_shows_json_and_resets(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import main

    kensho = _cli_repo(tmp_path, monkeypatch)
    _write_profile(kensho, _profile(focus_modules=["workspaces/controller"], coding_style=["small PRs"]))
    runner = CliRunner()

    shown = runner.invoke(main.app, ["profile"])
    assert shown.exit_code == 0 and "workspaces/controller" in shown.output and "small PRs" in shown.output

    as_json = runner.invoke(main.app, ["profile", "--json"])
    assert as_json.exit_code == 0 and '"focus_modules"' in as_json.output

    kept = runner.invoke(main.app, ["profile", "--reset"], input="n\n")
    assert (kensho / "profile.json").exists() and kept.exit_code == 0
    gone = runner.invoke(main.app, ["profile", "--reset"], input="y\n")
    assert not (kensho / "profile.json").exists() and gone.exit_code == 0


def test_profile_command_without_profile_points_to_onboard(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import main

    _cli_repo(tmp_path, monkeypatch)
    res = CliRunner().invoke(main.app, ["profile"])
    assert res.exit_code == 1 and "onboard" in res.output
