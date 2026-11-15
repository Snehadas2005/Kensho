import re
import subprocess
from pathlib import Path
from pydantic import BaseModel


class RepoContext(BaseModel):
    repo_root: Path
    upstream_repo: str          # Source org repo where issues/PRs live
    origin_repo: str            # Contributor fork
    github_username: str        # Extracted contributor handle
    is_fork: bool
    kensho_dir: Path            # <repo_root>/.kensho
    map_path: Path              # <repo_root>/.kensho/repo_map.json
    stack_path: Path            # <repo_root>/.kensho/stack_spec.json
    last_run_path: Path         # <repo_root>/.kensho/last_run.json


def _parse_github_slug(remote_url: str) -> str | None:
    """Extracts 'owner/repo' from HTTPS or SSH GitHub remote URLs."""
    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/.]+?)(?:\.git)?$", remote_url.strip())
    return match.group("slug") if match else None


def _run_git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def detect_repo_context(start_dir: Path | None = None) -> RepoContext:
    """Detects repo root, upstream source repo, and contributor fork from git remotes."""
    cwd = (start_dir or Path.cwd()).resolve()
    top = _run_git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        raise RuntimeError(f"Not inside a Git repository: {cwd}. `cd` into a cloned repo first.")

    repo_root = Path(top).resolve()
    origin_url = _run_git(repo_root, "remote", "get-url", "origin")
    upstream_url = _run_git(repo_root, "remote", "get-url", "upstream")

    origin_slug = _parse_github_slug(origin_url)
    upstream_slug = _parse_github_slug(upstream_url) or origin_slug

    if not upstream_slug:
        raise RuntimeError("Could not detect a GitHub remote (`origin` or `upstream`).")

    github_username = (origin_slug or upstream_slug).split("/")[0]
    kensho_dir = repo_root / ".kensho"
    kensho_dir.mkdir(parents=True, exist_ok=True)

    # Keep .kensho/ invisible to `git status` without touching the repo's tracked .gitignore
    exclude_file = repo_root / ".git" / "info" / "exclude"
    if exclude_file.exists():
        content = exclude_file.read_text(encoding="utf-8")
        if ".kensho/" not in content:
            exclude_file.write_text(content.rstrip() + "\n.kensho/\n", encoding="utf-8")

    return RepoContext(
        repo_root=repo_root,
        upstream_repo=upstream_slug,
        origin_repo=origin_slug or upstream_slug,
        github_username=github_username,
        is_fork=bool(origin_slug and upstream_slug and origin_slug.lower() != upstream_slug.lower()),
        kensho_dir=kensho_dir,
        map_path=kensho_dir / "repo_map.json",
        stack_path=kensho_dir / "stack_spec.json",
        last_run_path=kensho_dir / "last_run.json",
    )