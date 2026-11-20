import shutil
import subprocess
import time
from pathlib import Path


def _git(repo_dir: Path, *args: str, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, text=True, timeout=timeout)


def pr_remote(repo_dir: Path) -> str:
    """`upstream` if configured (PRs live on the upstream repo), else `origin`."""
    remotes = _git(repo_dir, "remote").stdout.split()
    return "upstream" if "upstream" in remotes else "origin"


def create_pr_worktree(repo_dir: Path, pr_number: int) -> tuple[Path | None, str]:
    """Fetches `pull/<n>/head` and checks it out into a throw-away `git worktree`.

    Returns (worktree_path, note). On any failure returns (None, reason) so the caller can
    fall back to the local working tree instead of aborting the run.
    """
    repo_dir = repo_dir.resolve()
    ref = f"refs/kensho/pr-{pr_number}"
    remote = pr_remote(repo_dir)

    fetched = _git(repo_dir, "fetch", "--quiet", "--depth=1", remote, f"+pull/{pr_number}/head:{ref}", timeout=45)
    if fetched.returncode != 0:
        return None, f"Could not fetch PR #{pr_number} from '{remote}' ({fetched.stderr.strip()[:160]}); using local working tree."

    base = repo_dir / ".kensho" / "worktrees"
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"pr-{pr_number}-{int(time.time())}"
    added = _git(repo_dir, "worktree", "add", "--detach", str(target), ref)
    if added.returncode != 0:
        _git(repo_dir, "update-ref", "-d", ref)
        return None, f"Could not create worktree for PR #{pr_number} ({added.stderr.strip()[:160]}); using local working tree."

    sha = _git(target, "rev-parse", "--short", "HEAD").stdout.strip()
    return target, f"Sandbox runs PR #{pr_number} @ {sha} (checked out from '{remote}')."


def remove_pr_worktree(repo_dir: Path, work_dir: str | Path | None, pr_number: int | None = None) -> None:
    """Best-effort cleanup of the worktree and the temporary ref. Never raises."""
    try:
        if work_dir:
            path = Path(work_dir)
            if path.resolve() != Path(repo_dir).resolve():
                _git(Path(repo_dir), "worktree", "remove", "--force", str(path))
                shutil.rmtree(path, ignore_errors=True)
                _git(Path(repo_dir), "worktree", "prune")
        if pr_number is not None:
            _git(Path(repo_dir), "update-ref", "-d", f"refs/kensho/pr-{pr_number}")
    except Exception:  # noqa: BLE001
        pass
