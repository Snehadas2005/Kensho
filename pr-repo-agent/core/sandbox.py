import io
import os
import tarfile
import time
import uuid
from pathlib import Path
import docker
from pydantic import BaseModel


class SandboxResult(BaseModel):
    language: str
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False


class PolyglotSandbox:
    def __init__(self, repo_dir: Path, timeout_sec: int = 60):
        self.repo_dir = repo_dir.resolve()
        self.timeout_sec = timeout_sec
        self.client = docker.from_env()

    def run_repro(
        self,
        language: str,
        test_code: str,
        target_subdir: str = "",
    ) -> SandboxResult:
        lang = language.lower().strip()
        if lang == "python":
            return self._run_python(test_code, target_subdir)
        elif lang == "go":
            return self._run_go(test_code, target_subdir)
        else:
            raise ValueError(f"Unsupported sandbox language: {language}")

    def _run_python(self, test_code: str, target_subdir: str) -> SandboxResult:
        run_id = uuid.uuid4().hex[:8]
        rel_dir = Path(target_subdir) if target_subdir else Path(".")
        rel_test_path = (rel_dir / f"test_repro_{run_id}.py").as_posix()
        container_test_path = f"/repo/{rel_test_path}"

        start = time.perf_counter()
        return self._exec_in_container(
            image="repro-py:latest",
            command=[
                "pytest",
                "-v",
                "--tb=short",
                "-o",
                "cache_dir=/tmp/pytest_cache",
                "--noconftest",
                container_test_path,
            ],
            language="python",
            start_time=start,
            test_rel_path=rel_test_path,
            test_code=test_code,
            env={
                "PYTHONPATH": "/repo/sdk/python:/repo",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def _run_go(self, test_code: str, target_subdir: str) -> SandboxResult:
        run_id = uuid.uuid4().hex[:8]
        rel_dir = Path(target_subdir) if target_subdir else Path("backend/src")
        rel_test_path = (rel_dir / f"repro_{run_id}_test.go").as_posix()
        pkg_path = f"./{rel_dir.as_posix()}"

        start = time.perf_counter()
        return self._exec_in_container(
            image="repro-go:latest",
            command=["go", "test", "-v", "-run", "TestRepro", pkg_path],
            language="go",
            start_time=start,
            test_rel_path=rel_test_path,
            test_code=test_code,
        )

    def _build_repo_tar(self, test_rel_path: str, test_code: str) -> bytes:
        """Packages only the relevant language tree + root manifests into an in-memory tar stream."""
        buf = io.BytesIO()
        skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", "vendor", "test", "tests"}
        include_roots = ["sdk/python", "backend/src", "api", "kubernetes_platform"]

        with tarfile.open(fileobj=buf, mode="w") as tar:
            # Include root go.mod / go.sum / pyproject.toml if present
            for root_file in ("go.mod", "go.sum", "pyproject.toml"):
                rf_path = self.repo_dir / root_file
                if rf_path.is_file():
                    tar.add(rf_path, arcname=root_file)

            for sub in include_roots:
                sub_path = self.repo_dir / sub
                if not sub_path.exists():
                    continue
                for root, dirs, files in os.walk(sub_path):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fname in files:
                        full_path = Path(root) / fname
                        rel_path = full_path.relative_to(self.repo_dir).as_posix()
                        tar.add(full_path, arcname=rel_path)

            encoded = test_code.encode("utf-8")
            info = tarfile.TarInfo(name=test_rel_path)
            info.size = len(encoded)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(encoded))

        buf.seek(0)
        return buf.read()

    def _exec_in_container(
        self,
        image: str,
        command: list[str],
        language: str,
        start_time: float,
        test_rel_path: str,
        test_code: str,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        container = None
        try:
            container = self.client.containers.create(
                image=image,
                command=command,
                working_dir="/repo",
                environment=env or {},
                network_disabled=True,
                mem_limit="1g",
                memswap_limit="1g",
                nano_cpus=2_000_000_000,  # 2 CPUs
                pids_limit=1024,  # generous: Go builds spawn many threads
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            tar_bytes = self._build_repo_tar(test_rel_path, test_code)
            container.put_archive("/repo", tar_bytes)
            container.start()

            result = container.wait(timeout=self.timeout_sec)
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