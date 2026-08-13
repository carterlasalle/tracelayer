"""TypeScript symbols via tree-sitter (§S)."""

from __future__ import annotations

from typing import Any

from tracelayer.symbols.javascript import JavaScriptParser


class TypeScriptParser(JavaScriptParser):
    language = "typescript"

    def _scope_name(self, node: Any) -> str | None:
        # Namespaces/modules are scopes, not symbols: push the name onto the
        # qualified-name stack so nested symbols read mod.NS.name.
        if node.type in ("module", "internal_module"):
            name = node.child_by_field_name("name")
            if name is not None:
                return name.text.decode("utf-8")
        return None

    def _symbol_info(self, node: Any, in_class: bool) -> tuple[str, str] | None:
        t = node.type
        if t == "interface_declaration":
            return ("interface", self._name_of(node))
        if t == "type_alias_declaration":
            return ("type_alias", self._name_of(node))
        if t == "abstract_class_declaration":
            return ("class", self._name_of(node))
        if t == "enum_declaration":
            return ("enum", self._name_of(node))
        return super()._symbol_info(node, in_class)
