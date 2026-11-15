import io
import re
import tarfile
from pathlib import Path
import docker
from pydantic import BaseModel


class StackSpec(BaseModel):
    upstream_repo: str
    image_tag: str
    primary_language: str                   # "python" | "go" | "node" | "rust"
    detected_languages: list[str]
    manifest_paths: list[str]
    test_command_by_lang: dict[str, str]    # e.g. {"python": "pytest -q", "go": "go test -v"}
    dockerfile_content: str


def _slugify_image_tag(upstream_repo: str) -> str:
    clean = re.sub(r"[^a-z0-9_.-]+", "-", upstream_repo.lower()).strip("-")
    return f"kensho-sandbox-{clean}:latest"


def _find_manifests(repo_root: Path) -> dict[str, list[Path]]:
    """Scans root + depth-2 subdirectories for language dependency manifests."""
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
    """Detects repo languages/manifests and synthesizes a cached Docker environment."""
    manifests = _find_manifests(repo_root)
    detected = [lang for lang, files in manifests.items() if files]
    if not detected:
        detected = ["python"]

    primary = detected[0]
    image_tag = _slugify_image_tag(upstream_repo)
    rel_manifests: list[str] = []
    for files in manifests.values():
        rel_manifests.extend(f.relative_to(repo_root).as_posix() for f in files)

    test_cmds: dict[str, str] = {}
    if "python" in detected:
        test_cmds["python"] = "pytest -q --tb=short"
    if "go" in detected:
        test_cmds["go"] = "go test -v"
    if "node" in detected:
        test_cmds["node"] = "npm test"
    if "rust" in detected:
        test_cmds["rust"] = "cargo test"

    dockerfile = _render_dockerfile(repo_root, manifests, detected)
    return StackSpec(
        upstream_repo=upstream_repo,
        image_tag=image_tag,
        primary_language=primary,
        detected_languages=detected,
        manifest_paths=sorted(set(rel_manifests)),
        test_command_by_lang=test_cmds,
        dockerfile_content=dockerfile,
    )


def _render_dockerfile(repo_root: Path, manifests: dict[str, list[Path]], detected: list[str]) -> str:
    """Builds a multi-runtime Dockerfile pre-caching repo dependencies."""
    lines = ["FROM python:3.11-slim", "WORKDIR /repo"]
    lines.append("RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*")

    if "go" in detected:
        lines.extend([
            "COPY --from=golang:alpine /usr/local/go /usr/local/go",
            "ENV PATH=\"/usr/local/go/bin:${PATH}\" GOTOOLCHAIN=auto GOPATH=/go",
        ])

    if "node" in detected:
        lines.append("RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*")

    if "python" in detected:
        lines.append("RUN pip install --no-cache-dir pytest pytest-asyncio pydantic")
        for req in manifests["python"]:
            rel = req.relative_to(repo_root).as_posix()
            if req.name == "requirements.txt":
                lines.append(f"COPY {rel} /tmp/reqs/{rel}")
                lines.append(f"RUN pip install --no-cache-dir -r /tmp/reqs/{rel} || true")
            elif req.name == "pyproject.toml":
                parent_rel = req.parent.relative_to(repo_root).as_posix()
                dest = "/tmp/pkg/" if parent_rel == "." else f"/tmp/pkg/{parent_rel}/"
                lines.append(f"COPY {rel} {dest}")

    if "go" in detected:
        for gm in manifests["go"]:
            rel_dir = gm.parent.relative_to(repo_root).as_posix()
            prefix = "" if rel_dir == "." else f"{rel_dir}/"
            lines.append(f"COPY {prefix}go.mod {prefix}go.sum* ./{prefix}")
        lines.append("RUN go mod download || true")

    if "node" in detected:
        for pj in manifests["node"]:
            rel_dir = pj.parent.relative_to(repo_root).as_posix()
            prefix = "" if rel_dir == "." else f"{rel_dir}/"
            lines.append(f"COPY {prefix}package.json {prefix}package-lock.json* ./{prefix}")
            lines.append(f"RUN cd ./{prefix} && (npm ci || npm install) || true")

    lines.append('CMD ["sleep", "infinity"]')
    return "\n".join(lines) + "\n"


def ensure_sandbox_image(repo_root: Path, spec: StackSpec, force_rebuild: bool = False) -> str:
    """Builds and caches the per-repo Docker image using an in-memory tar build context."""
    client = docker.from_env()
    if not force_rebuild:
        try:
            client.images.get(spec.image_tag)
            return spec.image_tag
        except docker.errors.ImageNotFound:
            pass

    # Build a minimal tar context containing only Dockerfile + manifest files (fast even on huge repos)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        df_bytes = spec.dockerfile_content.encode("utf-8")
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(df_bytes)
        tar.addfile(info, io.BytesIO(df_bytes))

        for rel_manifest in spec.manifest_paths:
            full = repo_root / rel_manifest
            if full.is_file():
                tar.add(full, arcname=rel_manifest)
            # Include sibling lockfiles (go.sum / package-lock.json) if present
            for lock_name in ("go.sum", "package-lock.json"):
                sibling = full.parent / lock_name
                if sibling.is_file():
                    tar.add(sibling, arcname=sibling.relative_to(repo_root).as_posix())

    buf.seek(0)
    client.images.build(fileobj=buf, custom_context=True, tag=spec.image_tag, rm=True)
    return spec.image_tag