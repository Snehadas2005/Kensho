import io
import tarfile
from pathlib import Path

from core.go_module import (
    detect_go_package,
    find_all_go_modules,
    find_go_module_root,
    local_replace_dirs,
    module_relative_pkg,
    resolve_existing_path,
    resolve_go_test_dir,
    rewrite_package_clause,
)
from core.guards import deterministic_verdict, is_environment_error, should_retry
from core.graph import deterministic_verdict as graph_verdict
from core.sandbox import PolyglotSandbox


def _make_notebooks_like(tmp_path: Path) -> Path:
    """Mirrors kubeflow/notebooks: no root go.mod, module lives in workspaces/backend."""
    mod = tmp_path / "workspaces" / "backend"
    (mod / "internal" / "api").mkdir(parents=True)
    (mod / "go.mod").write_text("module github.com/kubeflow/notebooks/workspaces/backend\n\ngo 1.24\n")
    (mod / "go.sum").write_text("")
    (mod / "internal" / "api" / "workspaces.go").write_text("package api\n\nfunc X() {}\n")
    (tmp_path / "README.md").write_text("hi")
    return tmp_path


def test_finds_nested_module_from_suspect_file(tmp_path):
    repo = _make_notebooks_like(tmp_path)
    root = find_go_module_root(repo, "", ["workspaces/backend/internal/api/workspaces.go"])
    assert root == "workspaces/backend"


def test_falls_back_to_only_module_in_repo(tmp_path):
    repo = _make_notebooks_like(tmp_path)
    assert find_go_module_root(repo, "does/not/exist", ["nope.go"]) == "workspaces/backend"
    assert find_all_go_modules(repo) == ["workspaces/backend"]


def test_no_go_module_returns_none(tmp_path):
    (tmp_path / "a.py").write_text("x=1")
    assert find_go_module_root(tmp_path, "", []) is None


def test_test_dir_package_and_relative_pkg(tmp_path):
    repo = _make_notebooks_like(tmp_path)
    mod = "workspaces/backend"
    d = resolve_go_test_dir(repo, mod, "workspaces/backend/internal/api", [])
    assert d == "workspaces/backend/internal/api"
    assert detect_go_package(repo, d) == "api"
    assert module_relative_pkg(mod, d) == "./internal/api"
    # invented dir falls back to suspect file dir, then module root
    assert resolve_go_test_dir(repo, mod, "workspaces/backend/pkg/handler", []) == mod


def test_package_clause_is_rewritten():
    code = 'package main\n\nimport "testing"\n\nfunc TestRepro(t *testing.T) {}\n'
    assert rewrite_package_clause(code, "api").startswith("package api")
    assert rewrite_package_clause("func TestRepro() {}", "api").startswith("package api")
    assert rewrite_package_clause(code, None) == code


def test_local_replace_dirs(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "go.mod").write_text("module api\n")
    (tmp_path / "go.mod").write_text("module root\n\nreplace example.com/api => ./api\n")
    assert local_replace_dirs(tmp_path, ".") == ["api"]


def test_resolve_existing_path_by_basename(tmp_path):
    repo = _make_notebooks_like(tmp_path)
    assert resolve_existing_path(repo, "workspaces/backend/pkg/api/workspaces.go") == \
        "workspaces/backend/internal/api/workspaces.go"
    assert resolve_existing_path(repo, "totally/made/up.go") is None


def test_go_tar_ships_whole_module_and_test_file(tmp_path):
    repo = _make_notebooks_like(tmp_path)
    sb = PolyglotSandbox(repo)
    data = sb._build_go_tar("workspaces/backend", [], "workspaces/backend/internal/api/repro_x_test.go", "package api\n")
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert "workspaces/backend/go.mod" in names
    assert "workspaces/backend/internal/api/workspaces.go" in names
    assert "workspaces/backend/internal/api/repro_x_test.go" in names


def test_missing_gomod_is_an_environment_error_not_retried():
    stderr = "go: go.mod file not found in current directory or any parent directory"
    assert is_environment_error(stderr)
    v = graph_verdict(1, stderr)
    assert v is not None and v.status == "TEST_ERROR" and "go.mod" in v.root_cause_summary
    state = {"exit_code": 1, "stderr": stderr, "stdout": "", "retry_count": 0, "max_retries": 2,
             "verdict": {"status": "TEST_ERROR"}}
    assert should_retry(state) is False
    assert deterministic_verdict(state).status == "TEST_ERROR"


def test_compile_error_in_generated_test_still_retries():
    state = {"exit_code": 1, "stderr": "", "stdout": "FAIL pkg [build failed]", "target_language": "go",
             "retry_count": 0, "max_retries": 2, "verdict": {"status": "TEST_ERROR"}}
    assert should_retry(state) is True


def test_test_lands_next_to_suspect_file_not_in_broad_target_dir(tmp_path):
    mod = tmp_path / "workspaces" / "controller"
    (mod / "internal" / "helper").mkdir(parents=True)
    (mod / "go.mod").write_text("module example.com/controller\n\ngo 1.24\n")
    (mod / "main.go").write_text("package main\n")
    (mod / "internal" / "helper" / "helper.go").write_text("package helper\n")
    d = resolve_go_test_dir(
        tmp_path, "workspaces/controller", "workspaces/controller",
        ["workspaces/controller/internal/helper/helper.go"],
    )
    assert d == "workspaces/controller/internal/helper"
    assert detect_go_package(tmp_path, d) == "helper"
    assert module_relative_pkg("workspaces/controller", d) == "./internal/helper"

def test_hallucinated_import_is_not_an_environment_error():
    stderr = "repro_x_test.go:12:2: no required module provides package example.com/m/internal/models; to add it:"
    assert not is_environment_error(stderr)
    assert graph_verdict(1, stderr) is None  # goes to the LLM judge / retry, not a fake env verdict


def test_module_summary_lists_only_real_packages_and_exports(tmp_path):
    from core.go_module import find_candidate_files, go_module_summary
    mod = tmp_path / "workspaces" / "backend"
    (mod / "internal" / "models" / "workspacekinds").mkdir(parents=True)
    (mod / "go.mod").write_text("module example.com/backend\n\ngo 1.24\n\nrequire (\n\tgithub.com/stretchr/testify v1.9.0\n)\n")
    (mod / "internal" / "models" / "workspacekinds" / "wk.go").write_text(
        "package workspacekinds\n\ntype WorkspaceKind struct{}\n\nfunc NewWorkspaceKind() {}\n")
    summary = go_module_summary(tmp_path, "workspaces/backend")
    assert "example.com/backend/internal/models/workspacekinds -> workspacekinds" in summary
    assert "NewWorkspaceKind" in summary and "WorkspaceKind" in summary
    assert "example.com/backend/internal/models ->" not in summary
    hits = find_candidate_files(tmp_path, "workspaces/backend", ["WorkspaceKind filter rules"])
    assert hits == ["workspaces/backend/internal/models/workspacekinds/wk.go"]

def test_normalize_go_test_fixes_alias_and_function_name():
    from core.go_module import normalize_go_test
    bad = (
        'package helper\n\nimport (\n    test "testing"\n\n    appsv1 "k8s.io/api/apps/v1"\n)\n\n'
        'func TestCopyStatefulSetFields_TemplateInjection(t *testing.T) {}\n'
    )
    fixed = normalize_go_test(bad)
    assert '    "testing"' in fixed and 'test "testing"' not in fixed
    assert 'appsv1 "k8s.io/api/apps/v1"' in fixed  # other aliases untouched
    assert "func TestRepro(t *testing.T)" in fixed
    already_ok = 'import "testing"\n\nfunc TestRepro(t *testing.T) {}\n'
    assert normalize_go_test(already_ok) == already_ok