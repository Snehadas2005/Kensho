"""Sandbox tests that need NO Docker daemon: a fake docker client records how containers are created."""
import io
import tarfile
from pathlib import Path

import pytest

from core.sandbox import PolyglotSandbox


class FakeContainer:
    def __init__(self, status=0, stdout=b"", stderr=b"", wait_error=None):
        self.status, self._out, self._err, self.wait_error = status, stdout, stderr, wait_error
        self.archives: list[tuple[str, bytes]] = []
        self.started = False
        self.removed = False

    def put_archive(self, path, data):
        self.archives.append((path, data))

    def start(self):
        self.started = True

    def wait(self, timeout=None):
        self.timeout = timeout
        if self.wait_error:
            raise self.wait_error
        return {"StatusCode": self.status}

    def logs(self, stdout=True, stderr=True):
        if stdout and not stderr:
            return self._out
        if stderr and not stdout:
            return self._err
        return self._out + self._err

    def remove(self, force=False):
        self.removed = True


class FakeClient:
    def __init__(self, containers: list[FakeContainer]):
        self._queue = list(containers)
        self.created: list[dict] = []
        outer = self

        class _Containers:
            def create(self, **kwargs):
                outer.created.append(kwargs)
                return outer._queue.pop(0)

        self.containers = _Containers()

    def info(self):
        return {"NCPU": 4}


def _py_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    return tmp_path


def _sandbox(repo: Path, client) -> PolyglotSandbox:
    sb = PolyglotSandbox(repo, timeout_sec=7)
    sb._client = client
    return sb


def test_exit_code_stdout_stderr_are_returned(tmp_path):
    c = FakeContainer(status=1, stdout=b"1 failed", stderr=b"AssertionError")
    res = _sandbox(_py_repo(tmp_path), FakeClient([c])).run_repro("python", "def test_repro_x():\n    assert False\n", "pkg")
    assert (res.exit_code, res.stdout, res.stderr, res.timed_out) == (1, "1 failed", "AssertionError", False)


def test_container_is_isolated_and_always_removed(tmp_path):
    c = FakeContainer(status=0)
    client = FakeClient([c])
    _sandbox(_py_repo(tmp_path), client).run_repro("python", "def test_repro_x():\n    pass\n", "pkg")
    cfg = client.created[0]
    assert cfg["network_disabled"] is True               # no network for generated code
    assert cfg["cap_drop"] == ["ALL"]                    # no Linux capabilities
    assert "no-new-privileges" in cfg["security_opt"]
    assert cfg["mem_limit"] and cfg["pids_limit"]        # resource limits are set
    assert c.started and c.removed


def test_timeout_maps_to_exit_124_and_container_is_still_removed(tmp_path):
    c = FakeContainer(wait_error=Exception("Read timed out"))
    res = _sandbox(_py_repo(tmp_path), FakeClient([c])).run_repro("python", "def test_repro_x():\n    pass\n", "pkg")
    assert res.exit_code == 124 and res.timed_out is True
    assert "Sandbox execution error" in res.stderr
    assert c.removed and c.timeout == 7                  # configured timeout was honoured


def test_docker_failure_is_reported_not_raised(tmp_path):
    class Boom:
        class containers:
            @staticmethod
            def create(**_):
                raise RuntimeError("docker daemon not running")

    res = _sandbox(_py_repo(tmp_path), Boom()).run_repro("python", "x", "pkg")
    assert res.exit_code == 1 and "docker daemon not running" in res.stderr and not res.timed_out


def test_python_tar_ships_code_and_test_but_not_git_or_node_modules(tmp_path):
    c = FakeContainer()
    client = FakeClient([c])
    _sandbox(_py_repo(tmp_path), client).run_repro("python", "def test_repro_x():\n    pass\n", "pkg")
    path, data = c.archives[0]
    names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
    assert path == "/repo"
    assert "pkg/mod.py" in names
    assert any(n.startswith("pkg/test_repro_") for n in names)
    assert not any(n.startswith((".git", "node_modules")) for n in names)


def test_unsupported_language_raises(tmp_path):
    with pytest.raises(ValueError):
        _sandbox(tmp_path, FakeClient([])).run_repro("rust", "x")


def test_go_without_module_is_a_setup_error_with_no_container(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    client = FakeClient([])
    res = _sandbox(tmp_path, client).run_repro("go", "package x", "")
    assert res.exit_code == 1 and "go.mod file not found" in res.stderr
    assert client.created == []                          # never reached Docker


def test_go_runs_deps_phase_online_then_test_phase_offline(tmp_path):
    mod = tmp_path / "svc"
    (mod / "internal" / "h").mkdir(parents=True)
    (mod / "go.mod").write_text("module example.com/svc\n\ngo 1.24\n")
    (mod / "internal" / "h" / "h.go").write_text("package h\n\nfunc F() int { return 1 }\n")
    deps, test = FakeContainer(status=0), FakeContainer(status=1, stderr=b"--- FAIL: TestRepro")
    client = FakeClient([deps, test])
    res = _sandbox(tmp_path, client).run_repro("go", "package main\n\nfunc TestRepro() {}\n", "svc/internal/h", ["svc/internal/h/h.go"])
    assert client.created[0]["network_disabled"] is False   # phase 1: download/compile deps
    assert client.created[1]["network_disabled"] is True    # phase 2: run the generated test offline
    assert client.created[1]["working_dir"] == "/repo/svc"
    assert client.created[1]["command"][-1] == "./internal/h"
    assert res.exit_code == 1
