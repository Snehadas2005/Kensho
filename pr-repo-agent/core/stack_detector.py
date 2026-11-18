import io
import re
import tarfile
from collections.abc import Callable
from pathlib import Path
import docker
import requests
import urllib3
from pydantic import BaseModel

class StackSpec(BaseModel):
    upstream_repo: str
    image_tag: str
    primary_language: str
    detected_languages: list[str]
    manifest_paths: list[str]
    test_command_by_lang: dict[str, str]
    dockerfile_content: str


def _slugify_image_tag(upstream_repo: str) -> str:
    clean = re.sub(r"[^a-z0-9_.-]+", "-", upstream_repo.lower()).strip("-")
    return f"kensho-sandbox-{clean}:latest"


def _find_manifests(repo_root: Path) -> dict[str, list[Path]]:
    patterns = {
        "python": ["pyproject.toml", "requirements.txt", "setup.py"],
        "go": ["go.mod"],
        "node": ["package.json"],
        "rust": ["Cargo.toml"],
    }
    found: dict[str, list[Path]] = {k: [] for k in patterns}
    skip = {".git", ".kensho", ".venv", "venv", "node_modules", "vendor", "__pycache__", "dist", "build"}

    for lang, names in patterns.items():
        for name in names:
            for match in repo_root.glob(f"**/{name}"):
                if any(part in skip for part in match.parts):
                    continue
                if len(match.relative_to(repo_root).parts) <= 3:
                    found[lang].append(match)
    return found


def detect_stack(repo_root: Path, upstream_repo: str) -> StackSpec:
    manifests = _find_manifests(repo_root)
    detected = [lang for lang, files in manifests.items() if files] or ["python"]
    primary = detected[0]
    image_tag = _slugify_image_tag(upstream_repo)

    rel_manifests = sorted({
        f.relative_to(repo_root).as_posix()
        for files in manifests.values()
        for f in files
    })

    test_cmds = {}
    if "python" in detected:
        test_cmds["python"] = "pytest -q --tb=short"
    if "go" in detected:
        test_cmds["go"] = "go test -v"
    if "node" in detected:
        test_cmds["node"] = "npm test"
    if "rust" in detected:
        test_cmds["rust"] = "cargo test"

    dockerfile = _render_fast_dockerfile(detected)
    return StackSpec(
        upstream_repo=upstream_repo,
        image_tag=image_tag,
        primary_language=primary,
        detected_languages=detected,
        manifest_paths=rel_manifests,
        test_command_by_lang=test_cmds,
        dockerfile_content=dockerfile,
    )


def _render_fast_dockerfile(detected: list[str]) -> str:
    """Slim, fast-building (<20s) multi-runtime sandbox image for any PC."""
    lines = ["FROM python:3.11-slim", "WORKDIR /repo"]
    lines.append("RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*")

    if "go" in detected:
        lines.extend([
            "COPY --from=golang:1.22-alpine /usr/local/go /usr/local/go",
            "ENV PATH=\"/usr/local/go/bin:${PATH}\" GOTOOLCHAIN=auto GOPATH=/go",
        ])

    if "node" in detected:
        lines.extend([
            "COPY --from=node:20-slim /usr/local/bin /usr/local/bin",
            "COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules",
        ])

    if "python" in detected:
        lines.append("RUN pip install --no-cache-dir pytest pytest-asyncio pydantic pyyaml requests")

    lines.append('CMD ["sleep", "infinity"]')
    return "\n".join(lines) + "\n"


# The first build downloads base images (python, plus go/node layers) and can sit silent for minutes.
# This is an *idle* timeout: it resets every time Docker sends a log line.
BUILD_IDLE_TIMEOUT_SEC = 900


def ensure_sandbox_image(
    repo_root: Path,
    spec: StackSpec,
    force_rebuild: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> str:
    client = docker.from_env(timeout=BUILD_IDLE_TIMEOUT_SEC)
    if not force_rebuild:
        try:
            client.images.get(spec.image_tag)
            return spec.image_tag
        except docker.errors.ImageNotFound:
            pass

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        df_bytes = spec.dockerfile_content.encode("utf-8")
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(df_bytes)
        tar.addfile(info, io.BytesIO(df_bytes))
    buf.seek(0)

    try:
        # Low-level API streams build output line by line (images.build() waits on one long request).
        for event in client.api.build(
            fileobj=buf, custom_context=True, tag=spec.image_tag, rm=True, decode=True
        ):
            if "error" in event:
                raise RuntimeError(f"Sandbox image build failed: {str(event['error']).strip()}")
            line = str(event.get("stream", "")).strip()
            if line and on_log:
                on_log(line)
    except (requests.exceptions.ReadTimeout, urllib3.exceptions.ReadTimeoutError) as exc:
        raise RuntimeError(
            f"Docker sent no build output for {BUILD_IDLE_TIMEOUT_SEC}s (slow network or stuck pull). "
            "Check your internet and Docker Desktop, then run `git kensho init` again; "
            "finished layers are cached so it resumes quickly."
        ) from exc
    return spec.image_tag
