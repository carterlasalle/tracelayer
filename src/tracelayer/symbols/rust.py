"""Rust structural symbol extraction via tree-sitter."""

from __future__ import annotations

import os
from typing import Any

from tree_sitter_language_pack import get_parser

from tracelayer.symbols.base import (
    SymbolRef,
    line_starts,
    no_cyclic_gc,
    symbol_lines,
)
from tracelayer.symbols.base import (
    ast_normalized as _ast_normalized,
)


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
            with no_cyclic_gc():
                data = text.encode("utf-8")
                tree = self.parser.parse(data)
                self._walk(tree.root_node, _module_path(path), data, out, ())
        except Exception:
            pass  # malformed source: return symbols parsed so far
        return out

    def ast_normalized(self, source: str) -> str:
        return _ast_normalized(source, self.parser)

    def _walk(self, root, module: str, data: bytes, out: list[SymbolRef],
              enclosing: tuple[str, ...]) -> None:
        """Iterative DFS: deep source files cannot overflow the interpreter stack."""
        pending: list[tuple[Any, tuple[str, ...]]] = [(root, enclosing)]
        while pending:
            node, enc = pending.pop()
            ntype = node.type
            if ntype == "function_item":
                self._function(node, module, data, out, enc)
                continue
            if ntype == "impl_item":
                type_node = node.child_by_field_name("type")
                if type_node is not None:
                    name = _type_name(type_node)
                    out.append(self._ref(node, data, "impl", name, self._qname(module, enc, name)))
                    pending.extend((c, enc + (name,)) for c in reversed(node.children))
                continue
            if ntype in _ITEM_KINDS:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = name_node.text.decode("utf-8")
                    kind = _ITEM_KINDS[ntype]
                    out.append(self._ref(node, data, kind, name, self._qname(module, enc, name)))
                    if ntype == "trait_item":
                        # default-bodied trait methods are function_item members
                        pending.extend((c, (name,)) for c in reversed(node.children))
                continue
            pending.extend((c, enc) for c in reversed(node.children))

    def _function(self, node, module: str, data: bytes, out: list[SymbolRef],
                  enclosing: tuple[str, ...]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = "method" if enclosing else "function"
        out.append(self._ref(node, data, kind, name, self._qname(module, enclosing, name)))

    @staticmethod
    def _qname(module: str, enclosing: tuple[str, ...], name: str) -> str:
        return ".".join((module, *enclosing, name))

    @staticmethod
    def _ref(node, data: bytes, kind: str, name: str, qname: str) -> SymbolRef:
        return SymbolRef(
            "rust", kind, name, qname,
            *symbol_lines(line_starts(data), node.start_byte, node.end_byte),
            data[node.start_byte:node.end_byte].decode("utf-8", "replace"),
        )
