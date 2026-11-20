import os
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from core.llm import call_structured_llm
from core.ast_symbols import build_symbol_map, save_symbol_map, symbol_digest


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

def _get_tree_summary(repo_dir: Path, max_depth: int = 3, max_lines: int = 400) -> str:
    """Lists directories up to `max_depth`, shallowest first so truncation never drops top-level folders."""
    entries: list[tuple[int, str]] = []
    for root, dirs, _ in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        rel = Path(root).relative_to(repo_dir)
        depth = len(rel.parts)
        if depth >= max_depth:
            dirs[:] = []
        if depth > 0:
            entries.append((depth, f"{rel.as_posix()}/"))
    entries.sort()
    return "\n".join(line for _, line in entries[:max_lines])


def _read_key_docs(repo_dir: Path) -> str:
    chunks = []
    for filename in DOC_CANDIDATES:
        doc_path = repo_dir / filename
        if doc_path.exists():
            content = doc_path.read_text(encoding="utf-8", errors="ignore")[:4000]
            chunks.append(f"--- {filename} ---\n{content}")
    return "\n\n".join(chunks)


def _refresh_symbols(repo_dir: Path, map_path: Path, sha: str) -> str:
    """Rebuilds .kensho/ast_symbols.json (tree-sitter, no LLM) and returns a prompt-sized digest."""
    try:
        symbol_map = build_symbol_map(repo_dir, root_sha=sha)
        save_symbol_map(symbol_map, map_path.parent)
        return symbol_digest(symbol_map, max_chars=5000)
    except Exception:  # noqa: BLE001  (symbol index is an enhancement; never block indexing)
        return "(symbol extraction unavailable)"


def build_initial_map(repo_name: str, repo_dir: Path, output_path: Path) -> RepoArchitectureMap:
    """Builds the initial RepoArchitectureMap from markdown docs and folder layout."""
    head_sha = _run_git(repo_dir, ["rev-parse", "HEAD"])
    tree_str = _get_tree_summary(repo_dir, max_depth=3)
    docs_str = _read_key_docs(repo_dir)
    symbols = _refresh_symbols(repo_dir, output_path, head_sha)

    system_prompt = (
        "You are a Senior Staff Software Architect. Analyze the repository's documentation "
        "and directory structure to produce a clean, actionable architecture map for a bug-reproduction agent."
    )
    user_prompt = (
        f"Repository: {repo_name}\nHEAD SHA: {head_sha}\n\n"
        f"DIRECTORY STRUCTURE (depth 3):\n{tree_str}\n\n"
        f"KEY DOCUMENTATION & BUILD FILES:\n{docs_str}\n\n"
        f"DETERMINISTIC CODE SYMBOLS (tree-sitter, ground truth; file: classes/functions):\n{symbols}\n\n"
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
    # `fetch` alone never moves HEAD, so fast-forward to actually pick up new merges.
    _run_git(repo_dir, ["pull", "--ff-only"])
    new_sha = _run_git(repo_dir, ["rev-parse", "HEAD"])

    if new_sha == existing.last_indexed_sha:
        if not (map_path.parent / "ast_symbols.json").exists():
            _refresh_symbols(repo_dir, map_path, new_sha)
        return existing

    symbols = _refresh_symbols(repo_dir, map_path, new_sha)

    try:
        changed_files = _run_git(repo_dir, ["diff", "--name-only", existing.last_indexed_sha, new_sha])
    except RuntimeError:
        # Old SHA fell out of a shallow clone's history -> rebuild instead of failing.
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
        f"CURRENT CODE SYMBOLS (tree-sitter):\n{symbols[:3000]}\n\n"
        "Return the updated RepoArchitectureMap."
    )

    updated = call_structured_llm(system_prompt, user_prompt, RepoArchitectureMap)
    updated.repo_name = existing.repo_name
    updated.last_indexed_sha = new_sha
    map_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return updated