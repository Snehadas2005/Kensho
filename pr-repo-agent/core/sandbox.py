import io
import os
import shlex
import tarfile
import time
import uuid
from pathlib import Path

import docker
from pydantic import BaseModel

from core.go_module import (
    SKIP_DIRS,
    detect_go_package,
    find_all_go_modules,
    find_go_module_root,
    local_replace_dirs,
    module_relative_pkg,
    resolve_go_test_dir,
    rewrite_package_clause,
)

# Persistent Docker volumes so module downloads / compiled deps survive between runs.
GO_MOD_VOLUME = "kensho-go-mod"
GO_BUILD_VOLUME = "kensho-go-build"
GO_DEPS_TIMEOUT_SEC = 900      # first `go mod download` (incl. toolchain) can be slow
GO_TEST_MIN_TIMEOUT_SEC = 300  # first compile of a large dependency tree is slow
MAX_FILE_BYTES = 2_000_000
GO_FAST_GCFLAGS = "all=-N -l"
MAX_TAR_BYTES = 250_000_000


class SandboxResult(BaseModel):
    language: str
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False


class PolyglotSandbox:
    def __init__(self, repo_dir: Path, timeout_sec: int = 180, stack_spec=None):
        self.repo_dir = repo_dir.resolve()
        self.timeout_sec = timeout_sec
        self.stack_spec = stack_spec
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def run_repro(
        self,
        language: str,
        test_code: str,
        target_subdir: str = "",
        suspect_files: list[str] | None = None,
    ) -> SandboxResult:
        lang = language.lower().strip()
        if lang not in ("python", "go"):
            raise ValueError(f"Unsupported sandbox language: {language}")

        if lang == "go":
            image = self.stack_spec.image_tag if self.stack_spec is not None else "repro-go:latest"
            return self._run_go(image, test_code, target_subdir, suspect_files)

        if self.stack_spec is not None:
            return self._run_with_stack_spec(lang, test_code, target_subdir)
        return self._run_python(test_code, target_subdir)

    # ------------------------------------------------------------------ Python
    def _run_with_stack_spec(self, lang: str, test_code: str, target_subdir: str) -> SandboxResult:
        run_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        rel_dir = Path(target_subdir) if target_subdir else Path(".")
        rel_test_path = (rel_dir / f"test_repro_{run_id}.py").as_posix()
        container_test_path = f"/repo/{rel_test_path}"
        base_cmd = self.stack_spec.test_command_by_lang.get(lang, "pytest -v --tb=short")
        command = shlex.split(base_cmd) + ["-o", "cache_dir=/tmp/pytest_cache", container_test_path]
        return self._exec_in_container(
            image=self.stack_spec.image_tag,
            command=command,
            language=lang,
            start_time=start,
            tar_bytes=self._build_python_tar(rel_test_path, test_code),
            env=self._python_env(target_subdir),
        )

    def _run_python(self, test_code: str, target_subdir: str) -> SandboxResult:
        run_id = uuid.uuid4().hex[:8]
        rel_dir = Path(target_subdir) if target_subdir else Path(".")
        rel_test_path = (rel_dir / f"test_repro_{run_id}.py").as_posix()
        container_test_path = f"/repo/{rel_test_path}"
        start = time.perf_counter()
        return self._exec_in_container(
            image="repro-py:latest",
            command=[
                "pytest", "-v", "--tb=short",
                "-o", "cache_dir=/tmp/pytest_cache",
                "--noconftest",
                container_test_path,
            ],
            language="python",
            start_time=start,
            tar_bytes=self._build_python_tar(rel_test_path, test_code),
            env=self._python_env(target_subdir),
        )

    @staticmethod
    def _python_env(target_subdir: str) -> dict[str, str]:
        paths = ["/repo/sdk/python", "/repo"]
        if target_subdir and target_subdir != ".":
            paths.insert(0, f"/repo/{Path(target_subdir).as_posix()}")
        return {"PYTHONPATH": ":".join(paths), "PYTHONDONTWRITEBYTECODE": "1"}

    # ----------------------------------------------------------------------- Go
    def _run_go(
        self,
        image: str,
        test_code: str,
        target_subdir: str,
        suspect_files: list[str] | None,
    ) -> SandboxResult:
        start = time.perf_counter()
        run_id = uuid.uuid4().hex[:8]

        module_root = find_go_module_root(self.repo_dir, target_subdir, suspect_files)
        if module_root is None:
            return self._env_error(
                "go", start,
                "go: go.mod file not found in the repository (looked at the target dir, "
                "suspect files and the whole repo). Kensho cannot run Go tests without a Go module.",
            )

        test_dir = resolve_go_test_dir(self.repo_dir, module_root, target_subdir, suspect_files)
        pkg_name = detect_go_package(self.repo_dir, test_dir)
        test_code = rewrite_package_clause(test_code, pkg_name)

        rel_test_path = (Path(test_dir) / f"repro_{run_id}_test.go").as_posix()
        pkg_path = module_relative_pkg(module_root, test_dir)
        container_workdir = "/repo" if module_root == "." else f"/repo/{module_root}"

        extra_dirs = local_replace_dirs(self.repo_dir, module_root)
        base_tar = self._build_go_tar(module_root, extra_dirs, None, "")
        full_tar = self._build_go_tar(module_root, extra_dirs, rel_test_path, test_code)

        # Phase 1: networked, cached dependency download (the test run itself has no network).
        dep_error = self._prepare_go_deps(image, base_tar, container_workdir, pkg_path)
        if dep_error:
            return self._env_error("go", start, dep_error)

        # Phase 2: offline test run against the cached modules.
        return self._exec_in_container(
            image=image,
            command=["go", "test", "-v", "-count=1", "-vet=off", f"-gcflags={GO_FAST_GCFLAGS}", "-run", "TestRepro", pkg_path],
            language="go",
            start_time=start,
            tar_bytes=full_tar,
            env=self._go_env(offline=True),
            workdir=container_workdir,
            volumes=self._go_volumes(),
            mem_limit="4g",
            timeout_sec=max(self.timeout_sec, GO_TEST_MIN_TIMEOUT_SEC),
        )

    @staticmethod
    def _go_env(offline: bool) -> dict[str, str]:
        env = {
            "GOPATH": "/go",
            "GOMODCACHE": "/gomod",
            "GOCACHE": "/gocache",
            "GOTOOLCHAIN": "auto",
            "GOFLAGS": "-buildvcs=false",
            "CGO_ENABLED": "0",
        }
        if offline:
            env["GOPROXY"] = "off"
        return env

    @staticmethod
    def _go_volumes() -> dict[str, dict[str, str]]:
        return {
            GO_MOD_VOLUME: {"bind": "/gomod", "mode": "rw"},
            GO_BUILD_VOLUME: {"bind": "/gocache", "mode": "rw"},
        }

    def _prepare_go_deps(self, image: str, tar_bytes: bytes, workdir: str, pkg_path: str) -> str | None:
        """Downloads modules AND pre-compiles the target package's dependencies (network on).

        Everything lands in persistent volumes, so the offline, time-limited test run only
        has to compile the generated test. Returns an error message on failure, else None.
        """
        container = None
        try:
            container = self.client.containers.create(
                image=image,
                command=["sh", "-c", f"go mod download && (go build -gcflags='{GO_FAST_GCFLAGS}' {pkg_path} || true)"],
                working_dir=workdir,
                environment=self._go_env(offline=False),
                network_disabled=False,
                volumes=self._go_volumes(),
                nano_cpus=self._cpu_nanos(),
                mem_limit="4g",
                memswap_limit="4g",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            container.put_archive("/repo", tar_bytes)
            container.start()
            result = container.wait(timeout=GO_DEPS_TIMEOUT_SEC)
            if result.get("StatusCode", 1) != 0:
                logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
                return f"go mod download failed (exit {result.get('StatusCode')}):\n{logs[-3000:]}"
            return None
        except Exception as exc:  # noqa: BLE001
            return (
                f"Sandbox execution error while preparing Go dependencies: {exc}. "
                "Progress is cached in Docker volumes; run the command again to continue."
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _cpu_nanos(self) -> int:
        """Give Go as many CPUs as Docker actually has (capped at 6); too many makes Docker refuse."""
        try:
            ncpu = int(self.client.info().get("NCPU", 2))
        except Exception:  # noqa: BLE001
            ncpu = 2
        return max(2, min(ncpu, 6)) * 1_000_000_000

    def warm_go_cache(self, image: str) -> str | None:
        """One-time (cached) download + compile of every Go module in the repo.

        Called from `git kensho init` so later `issue`/`pr` runs only compile the generated test.
        Returns an error message, or None on success.
        """
        for mod in find_all_go_modules(self.repo_dir):
            workdir = "/repo" if mod == "." else f"/repo/{mod}"
            tar_bytes = self._build_go_tar(mod, local_replace_dirs(self.repo_dir, mod), None, "")
            err = self._prepare_go_deps(image, tar_bytes, workdir, "./...")
            if err:
                return f"[{mod}] {err}"
        return None

    # --------------------------------------------------------------------- tars
    @staticmethod
    def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mtime = int(time.time())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))

    def _add_tree(self, tar: tarfile.TarFile, rel_root: str, budget: list[int], keep_vendor: bool) -> None:
        base = self.repo_dir if rel_root in ("", ".") else self.repo_dir / rel_root
        if not base.exists():
            return
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (keep_vendor or d != "vendor")]
            for fname in files:
                full = Path(root) / fname
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                important = fname in ("go.mod", "go.sum") or fname.endswith(".go")
                if size > MAX_FILE_BYTES and not important:
                    continue
                if budget[0] + size > MAX_TAR_BYTES:
                    return
                budget[0] += size
                tar.add(full, arcname=full.relative_to(self.repo_dir).as_posix())

    def _build_go_tar(
        self,
        module_root: str,
        extra_dirs: list[str],
        test_rel_path: str | None,
        test_code: str,
    ) -> bytes:
        """Ships the WHOLE Go module (plus locally replaced modules) so imports resolve."""
        buf = io.BytesIO()
        budget = [0]
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel in dict.fromkeys([module_root, *extra_dirs]):
                self._add_tree(tar, rel, budget, keep_vendor=True)
            for root_file in ("go.work", "go.work.sum"):
                if (self.repo_dir / root_file).is_file():
                    tar.add(self.repo_dir / root_file, arcname=root_file)
            if test_rel_path:
                self._add_bytes(tar, test_rel_path, test_code.encode("utf-8"))
        return buf.getvalue()

    def _build_python_tar(self, test_rel_path: str, test_code: str) -> bytes:
        """Ships the repo (minus junk/large files) plus the generated test."""
        buf = io.BytesIO()
        budget = [0]
        with tarfile.open(fileobj=buf, mode="w") as tar:
            self._add_tree(tar, ".", budget, keep_vendor=False)
            self._add_bytes(tar, test_rel_path, test_code.encode("utf-8"))
        return buf.getvalue()

    # ---------------------------------------------------------------- execution
    def _env_error(self, language: str, start: float, message: str) -> SandboxResult:
        return SandboxResult(
            language=language,
            exit_code=1,
            stdout="",
            stderr=message if "Sandbox execution error" in message else f"Sandbox setup error: {message}",
            duration_sec=round(time.perf_counter() - start, 2),
        )

    def _exec_in_container(
        self,
        image: str,
        command: list[str],
        language: str,
        start_time: float,
        tar_bytes: bytes,
        env: dict[str, str] | None = None,
        workdir: str = "/repo",
        volumes: dict | None = None,
        mem_limit: str = "1g",
        timeout_sec: int | None = None,
    ) -> SandboxResult:
        container = None
        try:
            container = self.client.containers.create(
                image=image,
                command=command,
                working_dir=workdir,
                environment=env or {},
                network_disabled=True,
                volumes=volumes,
                mem_limit=mem_limit,
                memswap_limit=mem_limit,
                nano_cpus=self._cpu_nanos(),  # 2 CPUs
                pids_limit=1024,  # generous: Go builds spawn many threads
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            container.put_archive("/repo", tar_bytes)
            container.start()

            result = container.wait(timeout=timeout_sec or self.timeout_sec)
            exit_code = result.get("StatusCode", 1)
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            duration = round(time.perf_counter() - start_time, 2)

            return SandboxResult(
                language=language,
                exit_code=exit_code,
                stdout=stdout[-6000:],
                stderr=stderr[-6000:],
                duration_sec=duration,
            )
        except Exception as e:
            duration = round(time.perf_counter() - start_time, 2)
            is_timeout = "timed out" in str(e).lower() or "read timeout" in str(e).lower()
            return SandboxResult(
                language=language,
                exit_code=124 if is_timeout else 1,
                stdout="",
                stderr=f"Sandbox execution error: {e}",
                duration_sec=duration,
                timed_out=is_timeout,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass