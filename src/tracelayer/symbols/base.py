"""Symbol model, parser protocol, and deterministic marker attachment (§S).

Shared foundation for the tree-sitter SymbolParsers: the SymbolRef dataclass,
the SymbolParser protocol, AST normalization, the generic depth-first symbol
walker, and the deterministic marker-attachment rule.
"""

from __future__ import annotations

import bisect
import gc
import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from tree_sitter_language_pack import get_parser as _pack_get_parser

from tracelayer.protocol import MarkerHit

SymbolInfo = tuple[str, str]  # (kind, name) for a definition node

_PARSER_CACHE: dict[str, Any] = {}


_GC_LOCKED_OFF = False


def line_starts(data: bytes) -> list[int]:
    """Offsets (0-based) of each line's first byte, plus a len(data) sentinel."""
    starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:
            starts.append(i + 1)
    starts.append(len(data))
    return starts


def line_at(starts: list[int], byte: int) -> int:
    """1-based line number containing the given byte offset (clamped)."""
    return bisect.bisect_right(starts, byte)


def symbol_lines(starts: list[int], start_byte: int, end_byte: int) -> tuple[int, int]:
    """1-based inclusive line range for a node's [start_byte, end_byte) span."""
    start_line = line_at(starts, start_byte)
    end_line = line_at(starts, max(end_byte - 1, start_byte))
    return start_line, end_line


def ensure_tree_sitter_gc_safety() -> None:
    """Disable the cyclic collector once, permanently, before tree-sitter use.

    The tree-sitter Python binding's finalizers are unsafe under the cyclic
    collector: an automatic collection (or even an explicit ``gc.collect()``)
    while tree-sitter trees/parsers exist segfaults or bus-errors
    nondeterministically (reproduced on macOS arm64 with tree-sitter 0.25 and
    0.26 while indexing a 1k-line module). The parser cache keeps trees alive
    across calls, so there is no safe point to re-enable. Reference counting
    still collects normal objects; the only cost is that true reference
    cycles are reclaimed at process exit instead of eagerly.
    """
    global _GC_LOCKED_OFF
    if not _GC_LOCKED_OFF:
        gc.disable()
        _GC_LOCKED_OFF = True


@contextmanager
def no_cyclic_gc() -> Iterator[None]:
    """Ensure the cyclic collector is off for a tree-sitter traversal.

    Calls :func:`ensure_tree_sitter_gc_safety` (idempotent) so direct parser
    use is protected too; the collector stays off for the process lifetime per
    the tree-sitter binding bug documented there.
    """
    ensure_tree_sitter_gc_safety()
    yield


def _parser_for(language: str) -> Any:
    """Cached tree-sitter parser for a language (same names as the pack)."""
    if language not in _PARSER_CACHE:
        _PARSER_CACHE[language] = _pack_get_parser(language)
    return _PARSER_CACHE[language]


@dataclass
class SymbolRef:
    language: str
    kind: (
        str  # function|method|class|module|struct|enum|interface|trait|impl|type_alias|declaration
    )
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
    """Canonical, deterministic AST serialization (text-sensitive).

    Renders an s-expression of node types with leaf-token text included, so
    literal changes (``return 1`` -> ``return 2``) change the fingerprint
    while inter-token whitespace does not. It deliberately does NOT use
    ``str(root_node)``: the tree-sitter repr embeds ``start_point`` /
    ``end_point`` positions, whose access across parses segfaults the process
    (see :func:`no_cyclic_gc`). The walk is iterative so deep trees cannot
    overflow the interpreter stack.
    """
    with no_cyclic_gc():
        data = source.encode("utf-8")
        tree = parser.parse(data)
        parts: list[str] = []
        stack: list[tuple[Any, bool]] = [(tree.root_node, False)]
        while stack:
            node, close = stack.pop()
            if close:
                parts.append(")")
                continue
            parts.append(f"({node.type}")
            stack.append((node, True))
            children = node.children
            if not children:
                parts.append(
                    f'"{data[node.start_byte : node.end_byte].decode("utf-8", "replace")}"'
                )
            else:
                stack.extend((c, False) for c in reversed(children))
        return " ".join(parts)


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

    The walk is iterative (explicit stack) so deeply nested source files can
    never overflow the interpreter stack: an unbounded recursive walker
    segfaulted on nested expressions in real repositories.
    """
    out: list[SymbolRef] = []
    # Line numbers come from newline offsets, never from node.start_point /
    # end_point: the tree-sitter binding segfaults nondeterministically when
    # point positions are read across parses (see no_cyclic_gc docstring).
    starts = line_starts(text)
    # (node, name_stack, in_class); name_stack is shared per branch so
    # qualified names follow document order exactly like the recursive walk.
    with no_cyclic_gc():
        pending: list[tuple[Any, list[str], bool]] = [(root, [], False)]
        while pending:
            node, stack, in_class = pending.pop()
            info = symbol_info(node, in_class)
            if info is not None:
                kind, name = info
                qualified = ".".join(p for p in (module, *stack, name) if p)
                start_line, end_line = symbol_lines(starts, node.start_byte, node.end_byte)
                out.append(
                    SymbolRef(
                        language=language,
                        kind=kind,
                        name=name,
                        qualified_name=qualified,
                        start_line=start_line,
                        end_line=end_line,
                        source=text[node.start_byte : node.end_byte].decode("utf-8"),
                    )
                )
                child_stack = [*stack, name]
                child_class = in_class or kind == "class"
                pending.extend((c, child_stack, child_class) for c in reversed(node.children))
                continue
            scope = scope_name(node) if scope_name is not None else None
            if scope is not None:
                child_stack = [*stack, scope]
                pending.extend((c, child_stack, in_class) for c in reversed(node.children))
                continue
            pending.extend((c, stack, in_class) for c in reversed(node.children))
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
            s for s in symbols if s.start_line > hit.line and _gap_ok(hit.line, s.start_line, lines)
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
