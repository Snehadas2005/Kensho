import os
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from core.llm import call_structured_llm


class ModuleSummary(BaseModel):
    path: str = Field(description="Directory path, e.g., 'sdk/python/kfp/dsl'")
    language: str = Field(description="'python', 'go', 'typescript', or 'other'")
    purpose: str = Field(description="Concise summary of what this module does")
    test_command: str = Field(description="Command to run unit tests for this module")


class RepoArchitectureMap(BaseModel):
    repo_name: str
    last_indexed_sha: str
    overview: str = Field(description="High-level architecture summary of the repo")
    primary_languages: list[str]
    modules: list[ModuleSummary]


IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "vendor", "dist", "build"}
DOC_CANDIDATES = ["README.md", "CONTRIBUTING.md", "DEVELOPMENT.md", "pyproject.toml", "go.mod"]


def _run_git(repo_dir: Path, args: list[str]) -> str:
    res = subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Git error in {repo_dir}: {res.stderr.strip()}")
    return res.stdout.strip()

def _get_tree_summary(repo_dir: Path, max_depth: int = 3, max_lines: int = 300) -> str:
    """Breadth-first directory listing, so top-level modules are never cut off by the line limit."""
    dirs_found: list[tuple[int, str]] = []
    for root, dirs, _files in os.walk(repo_dir):
        rel_root = Path(root).relative_to(repo_dir)  # relative: a parent folder named "build" can't hide the repo
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        if len(rel_root.parts) >= max_depth:
            dirs[:] = []
            continue
        for d in dirs:
            rel = rel_root / d
            dirs_found.append((len(rel.parts), rel.as_posix() + "/"))
    dirs_found.sort()
    return "\n".join(p for _, p in dirs_found[:max_lines])


def _read_key_docs(repo_dir: Path) -> str:
    chunks = []
    for filename in DOC_CANDIDATES:
        doc_path = repo_dir / filename
        if doc_path.exists():
            content = doc_path.read_text(encoding="utf-8", errors="ignore")[:4000]
            chunks.append(f"--- {filename} ---\n{content}")
    return "\n\n".join(chunks)


def build_initial_map(repo_name: str, repo_dir: Path, output_path: Path) -> RepoArchitectureMap:
    """Builds the initial RepoArchitectureMap from markdown docs and folder layout."""
    head_sha = _run_git(repo_dir, ["rev-parse", "HEAD"])
    tree_str = _get_tree_summary(repo_dir, max_depth=3)
    docs_str = _read_key_docs(repo_dir)

    system_prompt = (
        "You are a Senior Staff Software Architect. Analyze the repository's documentation "
        "and directory structure to produce a clean, actionable architecture map for a bug-reproduction agent."
    )
    user_prompt = (
        f"Repository: {repo_name}\nHEAD SHA: {head_sha}\n\n"
        f"DIRECTORY STRUCTURE (depth 3):\n{tree_str}\n\n"
        f"KEY DOCUMENTATION & BUILD FILES:\n{docs_str}\n\n"
        "Extract the core modules (especially Python and Go source/test directories) and their exact test commands."
    )

    repo_map = call_structured_llm(system_prompt, user_prompt, RepoArchitectureMap)
    repo_map.repo_name = repo_name
    repo_map.last_indexed_sha = head_sha

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(repo_map.model_dump_json(indent=2), encoding="utf-8")
    return repo_map


def sync_repo_map(repo_dir: Path, map_path: Path) -> RepoArchitectureMap:
    """Incrementally updates the repo map using only diffs since last_indexed_sha."""
    existing = RepoArchitectureMap.model_validate_json(map_path.read_text(encoding="utf-8"))
    _run_git(repo_dir, ["pull", "--ff-only"])  # `fetch` alone never moves HEAD
    new_sha = _run_git(repo_dir, ["rev-parse", "HEAD"])

    if new_sha == existing.last_indexed_sha:
        return existing

    try:
        changed_files = _run_git(repo_dir, ["diff", "--name-only", existing.last_indexed_sha, new_sha])
    except RuntimeError:
        # Old SHA fell out of the shallow (--depth 50) history: rebuild from scratch instead of failing.
        return build_initial_map(existing.repo_name, repo_dir, map_path)
    if not changed_files.strip():
        existing.last_indexed_sha = new_sha
        map_path.write_text(existing.model_dump_json(indent=2), encoding="utf-8")
        return existing

    system_prompt = (
        "You maintain an architecture map of a codebase. Given the existing map and a list of files "
        "changed by recently merged PRs, update ONLY the affected module summaries and keep everything else intact."
    )
    user_prompt = (
        f"EXISTING MAP:\n{existing.model_dump_json(indent=2)}\n\n"
        f"FILES CHANGED BETWEEN {existing.last_indexed_sha[:7]} AND {new_sha[:7]}:\n{changed_files}\n\n"
        "Return the updated RepoArchitectureMap."
    )

    updated = call_structured_llm(system_prompt, user_prompt, RepoArchitectureMap)
    updated.repo_name = existing.repo_name
    updated.last_indexed_sha = new_sha
    map_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return updated