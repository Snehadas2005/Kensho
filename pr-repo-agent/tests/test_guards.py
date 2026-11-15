from core.graph import deterministic_verdict, is_infra_failure
from core.github_client import _is_noisy_file
from core.tools import read_file_snippet


def test_timeout_is_never_verified():
    v = deterministic_verdict(124, "")
    assert v is not None and v.status == "TEST_ERROR"


def test_sandbox_runtime_error_is_test_error():
    v = deterministic_verdict(1, "Sandbox execution error: docker down")
    assert v is not None and v.status == "TEST_ERROR"
    assert is_infra_failure(1, "Sandbox execution error: x")


def test_passing_run_is_not_reproduced():
    v = deterministic_verdict(0, "")
    assert v is not None and v.status == "NOT_REPRODUCED"


def test_failing_run_is_left_to_the_llm():
    assert deterministic_verdict(1, "AssertionError") is None


def test_noisy_file_filter():
    assert _is_noisy_file("api/v2alpha1/go/pipelinespec/pipeline_spec.pb.go")
    assert _is_noisy_file("vendor/github.com/x/y.go")
    assert not _is_noisy_file("backend/src/apiserver/main.go")


def test_read_file_snippet_blocks_path_traversal(tmp_path):
    repo = tmp_path / "pipelines"
    repo.mkdir()
    (tmp_path / "pipelines-evil").mkdir()
    (tmp_path / "pipelines-evil" / "secret.txt").write_text("nope")
    out = read_file_snippet(repo, "../pipelines-evil/secret.txt")
    assert "File not found" in out
