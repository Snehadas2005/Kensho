import re
from pathlib import Path, PurePosixPath

SKIP_DIRS = {
    ".git", ".kensho", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".idea", ".vscode",
}

_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_REPLACE_LOCAL_RE = re.compile(r"=>\s*(\.{1,2}/[^\s]*|\.{1,2})\s*$", re.MULTILINE)


def _posix(p: Path | str) -> str:
    return PurePosixPath(str(p).replace("\\", "/")).as_posix()


def find_all_go_modules(repo_dir: Path, max_depth: int = 5) -> list[str]:
    """Relative posix paths of every directory holding a go.mod ('.' for the repo root)."""
    repo_dir = repo_dir.resolve()
    found: list[str] = []
    stack: list[tuple[Path, int]] = [(repo_dir, 0)]
    while stack:
        current, depth = stack.pop()
        if (current / "go.mod").is_file():
            rel = current.relative_to(repo_dir).as_posix()
            found.append(rel if rel != "" else ".")
        if depth >= max_depth:
            continue
        try:
            children = sorted(c for c in current.iterdir() if c.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name in SKIP_DIRS or child.name == "vendor":
                continue
            stack.append((child, depth + 1))
    return sorted(found, key=lambda p: (0 if p == "." else p.count("/") + 1, p))


def _walk_up_to_module(repo_dir: Path, rel_path: str) -> str | None:
    """Nearest ancestor (including itself) of rel_path that contains a go.mod."""
    candidate = (repo_dir / rel_path).resolve()
    if not candidate.is_relative_to(repo_dir):
        return None
    if candidate.is_file() or not candidate.exists():
        candidate = candidate.parent
    while True:
        if (candidate / "go.mod").is_file():
            rel = candidate.relative_to(repo_dir).as_posix()
            return rel if rel != "" else "."
        if candidate == repo_dir or candidate.parent == candidate:
            return None
        candidate = candidate.parent


def find_go_module_root(
    repo_dir: Path,
    target_subdir: str = "",
    suspect_files: list[str] | None = None,
) -> str | None:
    """Returns the module directory (relative to repo_dir, '.' for root) to run `go test` in.

    Priority: the triage target dir, then the suspect files, then the only/shallowest
    go.mod found anywhere in the repo.
    """
    repo_dir = repo_dir.resolve()
    candidates = [c for c in [target_subdir, *(suspect_files or [])] if c and c != "."]
    for cand in candidates:
        hit = _walk_up_to_module(repo_dir, _posix(cand))
        if hit is not None:
            return hit
    if (repo_dir / "go.mod").is_file():
        return "."
    all_mods = find_all_go_modules(repo_dir)
    return all_mods[0] if all_mods else None


def resolve_go_test_dir(
    repo_dir: Path,
    module_root: str,
    target_subdir: str = "",
    suspect_files: list[str] | None = None,
) -> str:
    """Directory (relative to repo_dir) that should receive the generated *_test.go file.

    It must exist inside the module so the test can live in the package under test.
    Falls back to the first suspect file's directory, then the module root itself.
    """
    repo_dir = repo_dir.resolve()
    mod_abs = (repo_dir / module_root).resolve()

    def _ok(rel: str) -> str | None:
        p = (repo_dir / rel).resolve()
        if p.is_file():
            p = p.parent
        if p.is_dir() and p.is_relative_to(mod_abs):
            rel_dir = p.relative_to(repo_dir).as_posix()
            return rel_dir if rel_dir else "."
        return None

    # 1) The package that actually contains the suspect source file wins: the test must live
    #    next to the code under test, otherwise its symbols are undefined.
    for cand in suspect_files or []:
        if cand.endswith(".go") and not cand.endswith("_test.go"):
            hit = _ok(_posix(cand))
            if hit is not None:
                return hit

    # 2) Otherwise a candidate dir that really holds Go source files.
    for cand in [target_subdir, *(suspect_files or [])]:
        if cand:
            hit = _ok(_posix(cand))
            if hit is not None and detect_go_package(repo_dir, hit):
                return hit
    return module_root


def detect_go_package(repo_dir: Path, pkg_dir: str) -> str | None:
    """Package name declared by the non-test .go files in pkg_dir (e.g. 'handler')."""
    directory = (repo_dir / pkg_dir).resolve()
    if not directory.is_dir():
        return None
    for go_file in sorted(directory.glob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        try:
            head = go_file.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            continue
        match = _PACKAGE_RE.search(head)
        if match:
            return match.group(1)
    return None


def rewrite_package_clause(test_code: str, package_name: str | None) -> str:
    """Forces the generated test into the package under test.

    A `package main` test dropped into `package handler` makes `go test` fail with
    "found packages handler and main", which looks like a bad test but is really setup.
    """
    if not package_name:
        return test_code
    if _PACKAGE_RE.search(test_code):
        return _PACKAGE_RE.sub(f"package {package_name}", test_code, count=1)
    return f"package {package_name}\n\n{test_code}"


def local_replace_dirs(repo_dir: Path, module_root: str) -> list[str]:
    """Directories pulled in by local `replace x => ../y` lines in go.mod (relative to repo_dir)."""
    repo_dir = repo_dir.resolve()
    mod_abs = (repo_dir / module_root).resolve()
    go_mod = mod_abs / "go.mod"
    if not go_mod.is_file():
        return []
    text = go_mod.read_text(encoding="utf-8", errors="ignore")
    out: list[str] = []
    for m in _REPLACE_LOCAL_RE.finditer(text):
        target = (mod_abs / m.group(1)).resolve()
        if target.is_dir() and target.is_relative_to(repo_dir):
            rel = target.relative_to(repo_dir).as_posix() or "."
            if rel not in out:
                out.append(rel)
    return out


def module_relative_pkg(module_root: str, test_dir: str) -> str:
    """`./sub/pkg` path of test_dir relative to the module root, for `go test`."""
    if test_dir in ("", "."):
        return "./"
    if module_root in ("", "."):
        return f"./{test_dir}"
    rel = PurePosixPath(test_dir).relative_to(PurePosixPath(module_root)).as_posix()
    return "./" if rel in ("", ".") else f"./{rel}"


def _go_pkg_dirs(repo_dir: Path, module_root: str) -> dict[str, list[str]]:
    """{package dir (relative to repo_dir): [non-test .go file names]} for one module."""
    base = repo_dir if module_root == "." else repo_dir / module_root
    out: dict[str, list[str]] = {}
    for go_file in sorted(base.rglob("*.go")):
        parts = go_file.relative_to(base).parts
        if any(part in SKIP_DIRS or part == "vendor" for part in parts):
            continue
        if go_file.name.endswith("_test.go"):
            continue
        d = go_file.parent.relative_to(repo_dir).as_posix() or "."
        out.setdefault(d, []).append(go_file.name)
    return out


def layout_hint(repo_dir: Path, max_lines: int = 120) -> str:
    """Real, on-disk Go layout (dirs + file names) for the triage prompt so the LLM stops inventing paths."""
    repo_dir = repo_dir.resolve()
    lines: list[str] = []
    for mod in find_all_go_modules(repo_dir):
        lines.append(f"GO MODULE ROOT: {mod}/  (go.mod present)")
        for d, names in list(_go_pkg_dirs(repo_dir, mod).items())[:60]:
            lines.append(f"    {d}/: {', '.join(names[:8])}")
        if len(lines) >= max_lines:
            break
    return "\n".join(lines[:max_lines])


_EXPORTED_RE = re.compile(
    r"^(?:func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)|type\s+([A-Z]\w*)|(?:var|const)\s+([A-Z]\w*))",
    re.MULTILINE,
)


def go_module_summary(
    repo_dir: Path,
    module_root: str,
    priority_dirs: list[str] | None = None,
    max_chars: int = 9000,
) -> str:
    """Ground truth for the coder: module path, REAL importable packages and their exported names.

    Without this the model invents imports (e.g. `internal/models` when only
    `internal/models/workspacekinds` exists) and the test can never compile.
    """
    repo_dir = repo_dir.resolve()
    go_mod = (repo_dir / module_root / "go.mod")
    if not go_mod.is_file():
        return ""
    mod_text = go_mod.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^module\s+(\S+)", mod_text, re.MULTILINE)
    mod_path = m.group(1) if m else ""
    requires = re.findall(r"^\s+([\w./~-]+)\s+v[\w.+-]+", mod_text, re.MULTILINE)[:60]

    pkgs = _go_pkg_dirs(repo_dir, module_root)
    prio = [_posix(d) for d in (priority_dirs or [])]
    ordered = sorted(pkgs, key=lambda d: (0 if d in prio else 1, d))

    lines = [
        "=== GO MODULE FACTS (authoritative: ONLY import these packages or the standard library / required modules) ===",
        f"module path: {mod_path}",
        "required modules: " + ", ".join(requires),
        "packages (import path -> package name: exported identifiers):",
    ]
    for d in ordered:
        rel = d if module_root == "." else PurePosixPath(d).relative_to(PurePosixPath(module_root)).as_posix()
        import_path = mod_path if rel in ("", ".") else f"{mod_path}/{rel}"
        names: list[str] = []
        pkg_name = ""
        for fname in pkgs[d]:
            try:
                text = (repo_dir / d / fname).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not pkg_name:
                pm = _PACKAGE_RE.search(text[:4000])
                pkg_name = pm.group(1) if pm else ""
            for g in _EXPORTED_RE.findall(text):
                name = next((x for x in g if x), "")
                if name and name not in names:
                    names.append(name)
        lines.append(f"  {import_path} -> {pkg_name}: {', '.join(names[:40])}")
        if sum(len(x) + 1 for x in lines) > max_chars:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines)


def find_candidate_files(
    repo_dir: Path,
    module_root: str,
    keywords: list[str],
    limit: int = 3,
) -> list[str]:
    """Real Go source files ranked by how often issue keywords appear (file name hits count triple)."""
    repo_dir = repo_dir.resolve()
    tokens: list[str] = []
    for kw in keywords:
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", kw):
            low = tok.lower()
            if low not in tokens:
                tokens.append(low)
    if not tokens:
        return []
    scored: list[tuple[int, str]] = []
    for d, names in _go_pkg_dirs(repo_dir, module_root).items():
        for fname in names:
            rel = f"{d}/{fname}" if d != "." else fname
            try:
                text = (repo_dir / rel).read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            score = sum(text.count(t) for t in tokens) + 3 * sum(1 for t in tokens if t in fname.lower())
            if score:
                scored.append((score, rel))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [rel for _, rel in scored[:limit]]

def resolve_existing_path(repo_dir: Path, rel_path: str) -> str | None:
    """Returns rel_path if it exists, else the unique repo file with the same basename, else None."""
    repo_dir = repo_dir.resolve()
    clean = _posix(rel_path).lstrip("/")
    target = (repo_dir / clean).resolve()
    if target.is_relative_to(repo_dir) and target.exists():
        return clean
    base = PurePosixPath(clean).name
    if not base:
        return None
    hits: list[str] = []
    stack = [repo_dir]
    while stack and len(hits) < 3:
        current = stack.pop()
        try:
            for child in current.iterdir():
                if child.is_dir():
                    if child.name not in SKIP_DIRS and child.name != "vendor":
                        stack.append(child)
                elif child.name == base:
                    hits.append(child.relative_to(repo_dir).as_posix())
        except OSError:
            continue
    return hits[0] if len(hits) == 1 else None

_TESTING_ALIAS_RE = re.compile(r'(?m)^(\s*(?:import\s+)?)(?!(?:_|import)\b)(\w+)\s+"testing"')
_TEST_FUNC_RE = re.compile(r"(?m)^func\s+(Test\w*)\s*\(\s*\w+\s+\*testing\.T\s*\)")


def normalize_go_test(test_code: str) -> str:
    """Fixes two mistakes LLMs make that make a Go repro test useless:

    1. aliasing the testing package (`test "testing"`) while still writing `*testing.T`
       -> `undefined: testing` compile error;
    2. naming the function something other than TestRepro, which the sandbox runs via `-run TestRepro`
       -> "no tests to run" and a fake pass.
    """
    code = _TESTING_ALIAS_RE.sub(lambda m: f'{m.group(1)}"testing"', test_code)
    if re.search(r"(?m)^func\s+TestRepro\s*\(", code):
        return code
    match = _TEST_FUNC_RE.search(code)
    if match:
        code = code[: match.start(1)] + "TestRepro" + code[match.end(1):]
    return code