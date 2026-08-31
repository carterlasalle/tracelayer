"""Language -> SymbolParser registry (§S)."""

from __future__ import annotations

import importlib

from tree_sitter_language_pack import get_parser as _pack_get_parser

from tracelayer.symbols.base import SymbolParser, ensure_tree_sitter_gc_safety

# Language name -> parser class name, imported lazily on first use.
_CLASSES: dict[str, str] = {
    "go": "GoParser",
    "java": "JavaParser",
    "javascript": "JavaScriptParser",
    "python": "PythonParser",
    "rust": "RustParser",
    "typescript": "TypeScriptParser",
}

_instances: dict[str, SymbolParser] = {}


# trace:exempt reason=internal-helper
def supported_languages() -> list[str]:
    """All languages with a registered SymbolParser (sorted, deterministic)."""
    return sorted(_CLASSES)


# trace:exempt reason=internal-helper
def get_parser(language: str) -> SymbolParser:
    """Return the cached SymbolParser for a language; ValueError if unknown.

    Parser modules are imported lazily on first use; each parser receives a
    tree-sitter parser for its language from tree_sitter_language_pack.
    """
    if language not in _CLASSES:
        raise ValueError(f"unsupported language: {language!r}")
    if language not in _instances:
        ensure_tree_sitter_gc_safety()
        module = importlib.import_module(f"tracelayer.symbols.{language}")
        cls = getattr(module, _CLASSES[language])
        # typescript boundaries are parsed with the tsx grammar: it is a
        # strict superset of typescript that also parses JSX, which the
        # plain typescript grammar mangles in one-liners (e.g. `return
        # <div/>` swallows the enclosing function declaration), losing
        # symbols in real React code (F7).
        grammar = "tsx" if language == "typescript" else language
        _instances[language] = cls(_pack_get_parser(grammar))
    return _instances[language]
