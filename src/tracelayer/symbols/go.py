"""Go structural symbol extraction via tree-sitter."""

from __future__ import annotations

import os

from tree_sitter_language_pack import get_parser

from tracelayer.symbols.base import SymbolRef, line_starts, no_cyclic_gc, symbol_lines


def _module_path(path: str) -> str:
    """Dotted module path from a file path (e.g. src/auth/tokens.go -> src.auth.tokens)."""
    return os.path.splitext(path)[0].replace("/", ".")


def _receiver_type(node) -> str:
    """Unqualified receiver type name from a Go method's receiver parameter list."""
    pending = list(node.children)
    while pending:
        child = pending.pop(0)
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
        pending.extend(child.children)
    return ""


class GoParser:
    """Tree-sitter parser for Go structural symbols.

    Records function_declaration, method_declaration, and type_spec nodes whose
    type is a struct or interface (other type specs — aliases, named primitive
    types — are skipped per contract §S). Method qualified names include the
    receiver type; struct/interface names are module-qualified only.
    """

    language = "go"

    def __init__(self, parser=None):
        self.parser = parser if parser is not None else get_parser("go")

    def parse(self, text: str, path: str) -> list[SymbolRef]:
        module = _module_path(path)
        out: list[SymbolRef] = []
        try:
            with no_cyclic_gc():
                data = text.encode("utf-8")
                tree = self.parser.parse(data)
                for node in tree.root_node.children:
                    if node.type == "function_declaration":
                        self._function(node, module, data, out)
                    elif node.type == "method_declaration":
                        self._method(node, module, data, out)
                    elif node.type == "type_declaration":
                        self._type_decl(node, module, data, out)
        except Exception:
            pass  # malformed source: return symbols parsed so far
        return out

    def ast_normalized(self, source: str) -> str:
        """Conservative AST normalization: str(root) includes source text."""
        with no_cyclic_gc():
            return str(self.parser.parse(source.encode("utf-8")).root_node)

    def _function(self, node, module: str, data: bytes, out: list[SymbolRef]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode("utf-8")
        out.append(self._ref(node, data, "function", name, f"{module}.{name}"))

    def _method(self, node, module: str, data: bytes, out: list[SymbolRef]) -> None:
        name_node = node.child_by_field_name("name")
        receiver = node.child_by_field_name("receiver")
        if name_node is None or receiver is None:
            return
        name = name_node.text.decode("utf-8")
        recv = _receiver_type(receiver)
        qname = f"{module}.{recv}.{name}" if recv else f"{module}.{name}"
        out.append(self._ref(node, data, "method", name, qname))

    def _type_decl(self, node, module: str, data: bytes, out: list[SymbolRef]) -> None:
        for spec in node.children:
            if spec.type != "type_spec":
                continue
            spec_type = spec.child_by_field_name("type")
            if spec_type is None:
                continue
            if spec_type.type == "struct_type":
                kind = "struct"
            elif spec_type.type == "interface_type":
                kind = "interface"
            else:
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue
            name = name_node.text.decode("utf-8")
            out.append(self._ref(spec, data, kind, name, f"{module}.{name}"))

    @staticmethod
    def _ref(node, data: bytes, kind: str, name: str, qname: str) -> SymbolRef:
        starts = line_starts(data)
        start_line, end_line = symbol_lines(starts, node.start_byte, node.end_byte)
        return SymbolRef(
            "go", kind, name, qname, start_line, end_line,
            data[node.start_byte:node.end_byte].decode("utf-8", "replace"),
        )
