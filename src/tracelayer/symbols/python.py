"""Python symbols via tree-sitter (§S)."""

from __future__ import annotations

from typing import Any

from tracelayer.symbols.base import (
    SymbolRef,
    ast_normalized,
    collect_symbols,
    module_path,
)


class PythonParser:
    language = "python"

    def __init__(self, parser: Any) -> None:
        self._parser = parser

    def parse(self, text: str, path: str) -> list[SymbolRef]:
        """Parse Python definitions; malformed source returns symbols so far."""
        out: list[SymbolRef] = []
        try:
            data = text.encode("utf-8")
            tree = self._parser.parse(data)
            out = collect_symbols(
                tree.root_node, data, self.language, module_path(path), self._symbol_info
            )
        except Exception:
            pass  # malformed source: return whatever was collected
        return out

    def ast_normalized(self, source: str) -> str:
        return ast_normalized(source, self._parser)

    def _name_of(self, node: Any) -> str:
        name = node.child_by_field_name("name")
        return name.text.decode("utf-8") if name is not None else "<anonymous>"

    def _symbol_info(self, node: Any, in_class: bool) -> tuple[str, str] | None:
        t = node.type
        if t == "decorated_definition":
            # Unwrap to the inner definition; the SymbolRef keeps the
            # decorated_definition range (decorators included), so markers above
            # decorators attach without counting decorator lines as a gap.
            inner = node.child_by_field_name("definition")
            if inner is not None and inner.type in ("function_definition", "class_definition"):
                if inner.type == "function_definition":
                    return ("method" if in_class else "function", self._name_of(inner))
                return ("class", self._name_of(inner))
            return None
        if t in ("function_definition", "class_definition"):
            if node.parent is not None and node.parent.type == "decorated_definition":
                return None  # already emitted via the decorated_definition
            if t == "function_definition":
                return ("method" if in_class else "function", self._name_of(node))
            return ("class", self._name_of(node))
        return None
