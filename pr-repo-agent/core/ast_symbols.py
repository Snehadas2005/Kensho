"""Deterministic Python / Go symbol extraction with tree-sitter (no LLM involved).

Falls back to a small regex scanner if the tree-sitter packages are not installed, so the
agent still works (just with less precise signatures).
"""
import json
import os
import re
from pathlib import Path

from core.schemas import ASTSymbol, ASTSymbolMap

IGNORED_DIRS = {".git", ".kensho", "node_modules", "__pycache__", ".venv", "venv", "vendor", "dist", "build"}
MAX_FILE_BYTES = 400_000
EXT_LANG = {".py": "python", ".go": "go"}

_PARSERS: dict[str, object] = {}


def _get_parser(lang: str):
    """Cached tree-sitter parser, or None if tree-sitter is unavailable."""
    if lang in _PARSERS:
        return _PARSERS[lang]
    parser = None
    try:
        from tree_sitter import Language, Parser

        if lang == "python":
            import tree_sitter_python as mod
        else:
            import tree_sitter_go as mod
        language = Language(mod.language())
        try:
            parser = Parser(language)            # tree-sitter >= 0.22
        except TypeError:
            parser = Parser()
            parser.set_language(language)        # older API
    except Exception:  # noqa: BLE001
        parser = None
    _PARSERS[lang] = parser
    return parser


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_line(text: str, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", text.split("{")[0].split(":\n")[0].strip())[:limit]


def _walk_python(node, src: bytes, out: list[ASTSymbol], parent: str = "") -> None:
    for child in node.children:
        kind = child.type
        if kind == "decorated_definition":
            _walk_python(child, src, out, parent)
            continue
        if kind in ("function_definition", "class_definition"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            header = _text(child, src).split("\n", 1)[0].rstrip(":").strip()
            sym_kind = "class" if kind == "class_definition" else ("method" if parent else "function")
            out.append(ASTSymbol(kind=sym_kind, name=name, signature=header[:160], line=child.start_point[0] + 1, parent=parent))
            body = child.child_by_field_name("body")
            if kind == "class_definition" and body is not None:
                _walk_python(body, src, out, name)


def _go_receiver_type(node, src: bytes) -> str:
    recv = node.child_by_field_name("receiver")
    if recv is None:
        return ""
    return re.sub(r"[^A-Za-z0-9_]", "", _text(recv, src).split()[-1]) if _text(recv, src).split() else ""


def _walk_go(node, src: bytes, out: list[ASTSymbol]) -> None:
    for child in node.children:
        kind = child.type
        if kind in ("function_declaration", "method_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            parent = _go_receiver_type(child, src) if kind == "method_declaration" else ""
            out.append(ASTSymbol(
                kind="method" if parent else "function",
                name=_text(name_node, src),
                signature=_first_line(_text(child, src)),
                line=child.start_point[0] + 1,
                parent=parent,
            ))
        elif kind == "type_declaration":
            for spec in child.children:
                if spec.type != "type_spec":
                    continue
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node is None:
                    continue
                tkind = type_node.type if type_node is not None else ""
                sym_kind = "struct" if tkind == "struct_type" else "interface" if tkind == "interface_type" else "type"
                out.append(ASTSymbol(
                    kind=sym_kind, name=_text(name_node, src),
                    signature=f"type {_text(name_node, src)} {sym_kind}", line=spec.start_point[0] + 1,
                ))


_FALLBACK_PY = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+(\w+)([^\n]*)", re.MULTILINE)
_FALLBACK_GO_FUNC = re.compile(r"^func\s+(?:\((?:\w+\s+)?\*?(\w+)\)\s*)?(\w+)\s*\(([^\n]*)", re.MULTILINE)
_FALLBACK_GO_TYPE = re.compile(r"^type\s+(\w+)\s+(struct|interface)?", re.MULTILINE)


def _fallback(lang: str, text: str) -> list[ASTSymbol]:
    out: list[ASTSymbol] = []
    if lang == "python":
        for m in _FALLBACK_PY.finditer(text):
            line = text[: m.start()].count("\n") + 1
            kind = "class" if m.group(2) == "class" else ("method" if m.group(1) else "function")
            out.append(ASTSymbol(kind=kind, name=m.group(3), signature=f"{m.group(2)} {m.group(3)}{m.group(4)}"[:160], line=line))
    else:
        for m in _FALLBACK_GO_FUNC.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(ASTSymbol(kind="method" if m.group(1) else "function", name=m.group(2),
                                 signature=f"func {m.group(2)}({m.group(3)}"[:160], line=line, parent=m.group(1) or ""))
        for m in _FALLBACK_GO_TYPE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(ASTSymbol(kind=m.group(2) or "type", name=m.group(1), signature=f"type {m.group(1)}", line=line))
    return out


def extract_file_symbols(path: Path, lang: str | None = None) -> list[ASTSymbol]:
    lang = lang or EXT_LANG.get(path.suffix)
    if lang not in ("python", "go"):
        return []
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        src = path.read_bytes()
    except OSError:
        return []

    parser = _get_parser(lang)
    if parser is None:
        return _fallback(lang, src.decode("utf-8", errors="replace"))
    tree = parser.parse(src)
    out: list[ASTSymbol] = []
    (_walk_python if lang == "python" else _walk_go)(tree.root_node, src, out)
    return out


def build_symbol_map(repo_dir: Path, root_sha: str = "", max_files: int = 4000) -> ASTSymbolMap:
    """Walks the repo and extracts symbols from every .py / .go file (skipping tests & junk)."""
    repo_dir = repo_dir.resolve()
    files: dict[str, list[ASTSymbol]] = {}
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in sorted(names):
            if name.endswith(("_test.go", "_pb2.py", ".pb.go")) or name.startswith("test_"):
                continue
            path = Path(root) / name
            if path.suffix not in EXT_LANG:
                continue
            symbols = extract_file_symbols(path)
            if symbols:
                files[path.relative_to(repo_dir).as_posix()] = symbols
            if len(files) >= max_files:
                return ASTSymbolMap(root_sha=root_sha, files=files)
    return ASTSymbolMap(root_sha=root_sha, files=files)


def symbols_path(kensho_dir: Path) -> Path:
    return kensho_dir / "ast_symbols.json"


def save_symbol_map(symbol_map: ASTSymbolMap, kensho_dir: Path) -> Path:
    target = symbols_path(kensho_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(symbol_map.model_dump_json(), encoding="utf-8")
    return target


def load_symbol_map(kensho_dir: Path) -> ASTSymbolMap | None:
    target = symbols_path(kensho_dir)
    if not target.is_file():
        return None
    try:
        return ASTSymbolMap.model_validate_json(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def symbol_digest(symbol_map: ASTSymbolMap, max_chars: int = 6000, per_file: int = 6) -> str:
    """Compact, deterministic 'path: Sym1, Sym2(...)' digest for LLM prompts."""
    lines: list[str] = []
    total = 0
    for path in sorted(symbol_map.files):
        syms = symbol_map.files[path]
        names = ", ".join(
            (f"{s.parent}.{s.name}" if s.parent else s.name) for s in syms[:per_file]
        )
        line = f"{path}: {names}" + (" …" if len(syms) > per_file else "")
        total += len(line) + 1
        if total > max_chars:
            lines.append("… (digest truncated)")
            break
        lines.append(line)
    return "\n".join(lines)


def format_hits(hits: list[tuple[str, ASTSymbol]]) -> list[str]:
    return [f"{path}:{sym.line} {sym.kind} {sym.parent + '.' if sym.parent else ''}{sym.name} :: {sym.signature}" for path, sym in hits]


def dumps(symbol_map: ASTSymbolMap) -> str:  # convenience for tests / debugging
    return json.dumps(symbol_map.model_dump(), indent=2)
