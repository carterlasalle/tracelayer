"""Symbol model, parser protocol, and deterministic marker attachment (§S).

Shared foundation for the tree-sitter SymbolParsers: the SymbolRef dataclass,
the SymbolParser protocol, AST normalization, the generic depth-first symbol
walker, and the deterministic marker-attachment rule.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from tree_sitter_language_pack import get_parser as _pack_get_parser

from tracelayer.protocol import MarkerHit

SymbolInfo = tuple[str, str]  # (kind, name) for a definition node

_PARSER_CACHE: dict[str, Any] = {}


def _parser_for(language: str) -> Any:
    """Cached tree-sitter parser for a language (same names as the pack)."""
    if language not in _PARSER_CACHE:
        _PARSER_CACHE[language] = _pack_get_parser(language)
    return _PARSER_CACHE[language]


@dataclass
class SymbolRef:
    language: str
    kind: str  # function|method|class|module|struct|enum|interface|trait|impl|type_alias|declaration
    name: str  # unqualified
    qualified_name: str  # module path (dots) + class nesting + name
    start_line: int  # 1-based inclusive
    end_line: int
    source: str  # exact source bytes decoded

    def ast_hash(self) -> str:
        """sha256 of ast_normalized(source) — formatting-sensitive by design."""
        normalized = ast_normalized(self.source, _parser_for(self.language))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SymbolParser(Protocol):
    language: str

    def parse(self, text: str, path: str) -> list[SymbolRef]: ...

    def ast_normalized(self, source: str) -> str: ...


def ast_normalized(source: str, parser: Any) -> str:
    """Parse source and render the root node s-expression (formatting-sensitive)."""
    tree = parser.parse(source.encode("utf-8"))
    return str(tree.root_node)


def module_path(path: str) -> str:
    """Module path from a file path: extension stripped, '/' -> '.'."""
    return os.path.splitext(path)[0].replace("/", ".").replace("\\", ".")


def collect_symbols(
    root: Any,
    text: bytes,
    language: str,
    module: str,
    symbol_info: Callable[[Any, bool], SymbolInfo | None],
    scope_name: Callable[[Any], str | None] | None = None,
) -> list[SymbolRef]:
    """Depth-first symbol walker shared by the tree-sitter parsers.

    `symbol_info(node, in_class)` returns (kind, name) for definition nodes or
    None; the emitted SymbolRef uses the node's own byte range (so decorated
    definitions span their decorators). `scope_name(node)` optionally returns a
    non-symbol scope (e.g. TypeScript namespaces) whose name is pushed onto the
    qualified-name stack without emitting a symbol. Symbols are emitted in
    document order, so markers attach to the nearest following definition.
    """
    out: list[SymbolRef] = []

    def rec(node: Any, stack: list[str], in_class: bool) -> None:
        info = symbol_info(node, in_class)
        if info is not None:
            kind, name = info
            qualified = ".".join(p for p in (module, *stack, name) if p)
            out.append(
                SymbolRef(
                    language=language,
                    kind=kind,
                    name=name,
                    qualified_name=qualified,
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    source=text[node.start_byte : node.end_byte].decode("utf-8"),
                )
            )
            stack.append(name)
            for child in node.children:
                rec(child, stack, in_class or kind == "class")
            stack.pop()
            return
        scope = scope_name(node) if scope_name is not None else None
        if scope is not None:
            stack.append(scope)
            for child in node.children:
                rec(child, stack, in_class)
            stack.pop()
            return
        for child in node.children:
            rec(child, stack, in_class)

    rec(root, [], False)
    return out


@dataclass
class MarkerAttachment:
    hit: MarkerHit
    symbol: SymbolRef | None
    attachment_kind: str  # "symbol" | "file"
    ambiguity: bool


_GAP_MAX = 3


def attach_markers(
    symbols: list[SymbolRef],
    hits: list[MarkerHit],
    lines: list[str],
) -> list[MarkerAttachment]:
    """Attach each marker to the nearest following symbol (deterministic).

    Rule: the target is the first symbol whose start line is strictly after the
    marker line and for which every line in between is blank, a comment, or a
    decorator ('@...'); at most 3 such lines may separate marker and symbol,
    otherwise the marker is detached (symbol=None, attachment_kind="file").
    When several symbols qualify, the earliest (by start line, then qualified
    name) wins and ambiguity is True. Detached markers in a supported language
    produce TL003 from the caller, not from this function.

    Comment detection covers the supported languages: '#' (python),
    '//', '/*', '*/' and leading '*' (javascript/typescript/go/rust/java block
    comment interiors). Lines out of range of `lines` are treated as
    non-comment, i.e. they detach.
    """
    attachments: list[MarkerAttachment] = []
    for hit in hits:
        eligible = [
            s
            for s in symbols
            if s.start_line > hit.line and _gap_ok(hit.line, s.start_line, lines)
        ]
        if not eligible:
            attachments.append(
                MarkerAttachment(hit=hit, symbol=None, attachment_kind="file", ambiguity=False)
            )
            continue
        eligible.sort(key=lambda s: (s.start_line, s.qualified_name))
        attachments.append(
            MarkerAttachment(
                hit=hit,
                symbol=eligible[0],
                attachment_kind="symbol",
                ambiguity=len(eligible) > 1,
            )
        )
    return attachments


def _gap_ok(hit_line: int, start_line: int, lines: list[str]) -> bool:
    if start_line - hit_line - 1 > _GAP_MAX:
        return False
    for ln in range(hit_line + 1, start_line):
        if ln - 1 >= len(lines):
            return False
        stripped = lines[ln - 1].strip()
        if stripped and not _is_comment_or_decorator(stripped):
            return False
    return True


def _is_comment_or_decorator(line: str) -> bool:
    return line.startswith("@") or line.startswith(("#", "//", "/*", "*"))
