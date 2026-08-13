"""JavaScript symbols via tree-sitter (§S)."""

from __future__ import annotations

from typing import Any

from tracelayer.symbols.base import (
    SymbolRef,
    ast_normalized,
    collect_symbols,
    module_path,
    no_cyclic_gc,
)


class JavaScriptParser:
    language = "javascript"

    def __init__(self, parser: Any) -> None:
        self._parser = parser

    def parse(self, text: str, path: str) -> list[SymbolRef]:
        """Parse JavaScript definitions; malformed source returns symbols so far."""
        out: list[SymbolRef] = []
        try:
            with no_cyclic_gc():
                data = text.encode("utf-8")
                tree = self._parser.parse(data)
                out = collect_symbols(
                    tree.root_node,
                    data,
                    self.language,
                    module_path(path),
                    self._symbol_info,
                    self._scope_name,
                )
        except Exception:
            pass  # malformed source: return whatever was collected
        return out

    def ast_normalized(self, source: str) -> str:
        return ast_normalized(source, self._parser)

    def _name_of(self, node: Any) -> str:
        name = node.child_by_field_name("name")
        return name.text.decode("utf-8") if name is not None else "<anonymous>"

    def _scope_name(self, node: Any) -> str | None:
        return None

    def _symbol_info(self, node: Any, in_class: bool) -> tuple[str, str] | None:
        t = node.type
        if t in ("function_declaration", "generator_function_declaration"):
            return ("function", self._name_of(node))
        if t == "class_declaration":
            return ("class", self._name_of(node))
        # tree-sitter-javascript parses `export default class {}` as node type
        # "class" (anonymous) rather than class_declaration; class expressions
        # assigned to a declarator are not definitions, so require the export.
        if t == "class" and node.parent is not None and node.parent.type == "export_statement":
            return ("class", self._name_of(node))
        if t == "method_definition":
            return ("method", self._name_of(node))
        if t == "variable_declarator":
            parent = node.parent
            value = node.child_by_field_name("value")
            if (
                parent is not None
                and parent.type == "lexical_declaration"
                and value is not None
                and value.type == "arrow_function"
            ):
                return ("function", self._name_of(node))
        return None
