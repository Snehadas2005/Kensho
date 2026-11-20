import os
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "vendor"}


def read_file_snippet(repo_dir: Path, rel_path: str, start_line: int = 1, max_lines: int = 180) -> str:
    """Safely reads a bounded slice of lines from a file inside the repository."""
    base = repo_dir.resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
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


def get_git_blame(repo_dir: Path, rel_path: str, start_line: int = 1, end_line: int = 60) -> str:
    """`git blame` for a line range: who last changed each line, when, and in which commit.

    Helps the agent see which lines changed recently (often where the bug was introduced).
    """
    base = repo_dir.resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return f"[File not found: {rel_path}]"
    start = max(1, int(start_line))
    end = max(start, int(end_line))
    res = subprocess.run(
        ["git", "-C", str(base), "blame", "--date=short", "-L", f"{start},{end}", "--", rel_path],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return f"[git blame unavailable for {rel_path}: {res.stderr.strip()[:200]}]"
    lines = []
    for raw in res.stdout.splitlines():
        # "<sha> (<author> <date> <lineno>) <code>"
        head, _, code = raw.partition(") ")
        lines.append(f"{head})  {code[:110]}")
    return f"--- git blame {rel_path} (lines {start}-{end}) ---\n" + "\n".join(lines[:80])


def search_similar_prs(memory, title: str, body: str = "", top_k: int = 3) -> str:
    """Agent-tool wrapper over the ChromaDB memory: formatted text of similar past runs."""
    matches = memory.query_similar(title, body, top_k=top_k)
    if not matches:
        return "[No similar past bugs/PRs in memory yet]"
    chunks = []
    for m in matches:
        chunks.append(
            f"[{m.item_id}] {m.title} ({m.status}, distance {m.distance})\n"
            f"  Root cause: {m.root_cause[:300]}"
        )
    return "\n".join(chunks)
