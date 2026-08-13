"""Java structural symbol extraction via tree-sitter."""

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
    """Dotted module path from a file path (e.g. src/com/example/Foo.java ->
    src.com.example.Foo)."""
    return os.path.splitext(path)[0].replace("/", ".")


_KIND_BY_TYPE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}


class JavaParser:
    """Tree-sitter parser for Java structural symbols.

    Records class/interface/enum declarations, methods, and constructors with
    qualified names built from the module path plus enclosing type nesting.
    Constructor kind is "constructor" (acceptance names it; base.py's kind
    vocabulary has no exact entry). Methods declared inside interfaces are
    recorded like any other method.
    """

    language = "java"

    def __init__(self, parser=None):
        self.parser = parser if parser is not None else get_parser("java")

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

    def _walk(
        self, root, module: str, data: bytes, out: list[SymbolRef], enclosing: tuple[str, ...]
    ) -> None:
        """Iterative DFS: deep source files cannot overflow the interpreter stack."""
        pending: list[tuple[Any, tuple[str, ...]]] = [(root, enclosing)]
        while pending:
            node, enc = pending.pop()
            ntype = node.type
            if ntype in _KIND_BY_TYPE:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = name_node.text.decode("utf-8")
                    out.append(
                        self._ref(
                            node, data, _KIND_BY_TYPE[ntype], name, self._qname(module, enc, name)
                        )
                    )
                    pending.extend((c, enc + (name,)) for c in reversed(node.children))
                continue
            if ntype in ("method_declaration", "constructor_declaration"):
                self._member(node, module, data, out, enc, ntype)
                continue
            pending.extend((c, enc) for c in reversed(node.children))

    def _member(
        self,
        node,
        module: str,
        data: bytes,
        out: list[SymbolRef],
        enclosing: tuple[str, ...],
        ntype: str,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = "method" if ntype == "method_declaration" else "constructor"
        out.append(self._ref(node, data, kind, name, self._qname(module, enclosing, name)))

    @staticmethod
    def _qname(module: str, enclosing: tuple[str, ...], name: str) -> str:
        return ".".join((module, *enclosing, name))

    @staticmethod
    def _ref(node, data: bytes, kind: str, name: str, qname: str) -> SymbolRef:
        return SymbolRef(
            "java",
            kind,
            name,
            qname,
            *symbol_lines(line_starts(data), node.start_byte, node.end_byte),
            data[node.start_byte : node.end_byte].decode("utf-8", "replace"),
        )
