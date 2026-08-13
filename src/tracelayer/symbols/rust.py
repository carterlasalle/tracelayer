"""Rust structural symbol extraction via tree-sitter."""

from __future__ import annotations

import os

from tree_sitter_language_pack import get_parser

from tracelayer.symbols.base import SymbolRef


def _module_path(path: str) -> str:
    """Dotted module path from a file path (e.g. src/main.rs -> src.main)."""
    return os.path.splitext(path)[0].replace("/", ".")


def _type_name(node) -> str:
    """Unqualified type name from an impl target: first type_identifier (strips generics)."""
    pending = [node]
    while pending:
        child = pending.pop(0)
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
        pending.extend(child.children)
    return node.text.decode("utf-8")


_ITEM_KINDS = {"struct_item": "struct", "enum_item": "enum", "trait_item": "trait"}


class RustParser:
    """Tree-sitter parser for Rust structural symbols.

    Records function_item, impl_item, struct_item, enum_item, trait_item.
    function_item inside an impl/trait block is a "method" qualified by the
    enclosing type name; a top-level function_item is a "function". Trait
    signature members (function_signature_item, i.e. bodiless trait methods)
    are not recorded per contract §S. Impl names use the impl target type
    (the type after `for` when present); generic arguments are stripped.
    """

    language = "rust"

    def __init__(self, parser=None):
        self.parser = parser if parser is not None else get_parser("rust")

    def parse(self, text: str, path: str) -> list[SymbolRef]:
        out: list[SymbolRef] = []
        try:
            tree = self.parser.parse(text.encode("utf-8"))
            self._walk(tree.root_node, _module_path(path), text, out, ())
        except Exception:
            pass  # malformed source: return symbols parsed so far
        return out

    def ast_normalized(self, source: str) -> str:
        """Conservative AST normalization: str(root) includes source text."""
        return str(self.parser.parse(source.encode("utf-8")).root_node)

    def _walk(self, node, module: str, text: str, out: list[SymbolRef],
              enclosing: tuple[str, ...]) -> None:
        if node.type == "function_item":
            self._function(node, module, text, out, enclosing)
            return
        if node.type in _ITEM_KINDS:
            self._item(node, module, text, out)
            return
        if node.type == "impl_item":
            self._impl(node, module, text, out, enclosing)
            return
        for child in node.children:
            self._walk(child, module, text, out, enclosing)

    def _function(self, node, module: str, text: str, out: list[SymbolRef],
                  enclosing: tuple[str, ...]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = "method" if enclosing else "function"
        out.append(self._ref(node, text, kind, name, self._qname(module, enclosing, name)))

    def _item(self, node, module: str, text: str, out: list[SymbolRef]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = _ITEM_KINDS[node.type]
        out.append(self._ref(node, text, kind, name, self._qname(module, (), name)))
        if node.type == "trait_item":
            # default-bodied trait methods are function_item members
            for child in node.children:
                self._walk(child, module, text, out, (name,))

    def _impl(self, node, module: str, text: str, out: list[SymbolRef],
              enclosing: tuple[str, ...]) -> None:
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        name = _type_name(type_node)
        out.append(self._ref(node, text, "impl", name, self._qname(module, enclosing, name)))
        for child in node.children:
            self._walk(child, module, text, out, enclosing + (name,))

    @staticmethod
    def _qname(module: str, enclosing: tuple[str, ...], name: str) -> str:
        return ".".join((module, *enclosing, name))

    @staticmethod
    def _ref(node, text: str, kind: str, name: str, qname: str) -> SymbolRef:
        return SymbolRef(
            "rust", kind, name, qname,
            node.start_point.row + 1, node.end_point.row + 1,
            text[node.start_byte:node.end_byte],
        )
