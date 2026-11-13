import os
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "vendor"}


def read_file_snippet(repo_dir: Path, rel_path: str, start_line: int = 1, max_lines: int = 180) -> str:
    """Safely reads a bounded slice of lines from a file inside the repository."""
    target = (repo_dir / rel_path).resolve()
    if not str(target).startswith(str(repo_dir.resolve())) or not target.is_file():
        return f"[File not found: {rel_path}]"

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start_idx = max(0, start_line - 1)
    end_idx = min(len(lines), start_idx + max_lines)
    numbered = [f"{i + 1}: {lines[i]}" for i in range(start_idx, end_idx)]
    return f"--- {rel_path} (lines {start_idx + 1}-{end_idx} of {len(lines)}) ---\n" + "\n".join(numbered)


def grep_codebase(repo_dir: Path, keyword: str, subdir: str = "", max_matches: int = 15) -> str:
    """Searches for a keyword across .py and .go files and returns matching file:line snippets."""
    base_repo = repo_dir.resolve()
    search_root = (base_repo / subdir).resolve() if subdir else base_repo
    if not search_root.exists():
        search_root = base_repo

    matches = []
    kw_lower = keyword.lower()

    for root, dirs, files in os.walk(search_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith((".py", ".go")):
                continue
            fpath = Path(root) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue

            for idx, line in enumerate(content, start=1):
                if kw_lower in line.lower():
                    rel = fpath.relative_to(base_repo).as_posix()
                    matches.append(f"{rel}:{idx}: {line.strip()[:140]}")
                    if len(matches) >= max_matches:
                        return "\n".join(matches)

    return "\n".join(matches) if matches else f"[No matches found for '{keyword}']"