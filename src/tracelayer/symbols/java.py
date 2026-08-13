"""Java structural symbol extraction via tree-sitter."""

from __future__ import annotations

import os

from tree_sitter_language_pack import get_parser

from tracelayer.symbols.base import SymbolRef


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
        ntype = node.type
        if ntype in _KIND_BY_TYPE:
            self._type(node, module, text, out, enclosing)
            return
        if ntype in ("method_declaration", "constructor_declaration"):
            self._member(node, module, text, out, enclosing, ntype)
            return
        for child in node.children:
            self._walk(child, module, text, out, enclosing)

    def _type(self, node, module: str, text: str, out: list[SymbolRef],
              enclosing: tuple[str, ...]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = _KIND_BY_TYPE[node.type]
        out.append(self._ref(node, text, kind, name, self._qname(module, enclosing, name)))
        for child in node.children:
            self._walk(child, module, text, out, enclosing + (name,))

    def _member(self, node, module: str, text: str, out: list[SymbolRef],
                enclosing: tuple[str, ...], ntype: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        kind = "method" if ntype == "method_declaration" else "constructor"
        out.append(self._ref(node, text, kind, name, self._qname(module, enclosing, name)))

    @staticmethod
    def _qname(module: str, enclosing: tuple[str, ...], name: str) -> str:
        return ".".join((module, *enclosing, name))

    @staticmethod
    def _ref(node, text: str, kind: str, name: str, qname: str) -> SymbolRef:
        return SymbolRef(
            "java", kind, name, qname,
            node.start_point.row + 1, node.end_point.row + 1,
            text[node.start_byte:node.end_byte],
        )
