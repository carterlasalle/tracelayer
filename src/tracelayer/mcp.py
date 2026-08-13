"""Minimal MCP (Model Context Protocol) stdio server — optional adapter.

The spec explicitly marks MCP as optional and never required (Phase 10); the
skill + CLI + hooks remain the canonical interface. This adapter lets any
MCP-capable agent (Claude Code, etc.) call the read-only query surface and
verify gate directly: ``trace mcp`` speaks newline-delimited JSON-RPC 2.0
over stdin/stdout (the MCP stdio transport), with zero dependencies beyond
the stdlib.

Tools exposed: status, search, context, why, impact, verify, index. All are
deterministic and local; nothing leaves the machine.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from tracelayer.engine import Engine

PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05", "2024-10-07")

ToolHandler = Callable[[dict[str, Any]], tuple[str, bool]]  # (text, is_error)


def _node_json(node: Any) -> dict[str, Any]:
    return {
        "trace_id": node.trace_id,
        "type": node.node_type,
        "title": node.title,
        "path": node.canonical_path,
        "status": node.status(),
        "symbol": node.symbol_qualified_name,
    }


def _edge_json(edge: Any, node: Any) -> dict[str, Any]:
    return {
        "predicate": edge.predicate,
        "to": node.trace_id,
        "source_kind": edge.source_kind,
        "status": edge.status,
    }


def _tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": schema}


TOOLS: list[dict[str, Any]] = [
    _tool(
        "status",
        "Trace repository health summary (nodes, edges, broken refs, stale, policy).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    _tool(
        "search",
        "Full-text search over trace IDs, titles, symbols, and work labels.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "context",
        "Bounded trace context for one artifact: upstream work/requirement/"
        "decision/plan, downstream tests/docs, verification status, staleness.",
        {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "why",
        "Causal path from a trace root back to the queried artifact.",
        {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "impact",
        "What depends on an artifact: declared semantic, structural, and "
        "stale verification impact.",
        {
            "type": "object",
            "properties": {
                "trace_id": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["trace_id"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "verify",
        "Run the trace policy gate for the repository (exit-status semantics in the result).",
        {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["changed", "all"]},
                "lifecycle": {
                    "type": "string",
                    "enum": ["draft", "wip", "review", "merge", "release"],
                },
            },
            "additionalProperties": False,
        },
    ),
    _tool(
        "index",
        "Refresh the materialized trace graph from the repository (changed scope by default).",
        {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["changed", "all"]},
            },
            "additionalProperties": False,
        },
    ),
]


def _handlers(engine: Engine) -> dict[str, ToolHandler]:
    def status(_: dict[str, Any]) -> tuple[str, bool]:
        s = engine.status()
        return json.dumps(vars(s), indent=2, default=str), False

    def search(args: dict[str, Any]) -> tuple[str, bool]:
        nodes = engine.search(args["query"], args.get("limit", 20))
        return json.dumps([_node_json(n) for n in nodes], indent=2), False

    def context(args: dict[str, Any]) -> tuple[str, bool]:
        ctx = engine.context(args["trace_id"])
        if ctx is None:
            return json.dumps({"error": f"unknown trace id: {args['trace_id']}"}), True
        result = {
            "trace_id": ctx.node.trace_id,
            "node_type": ctx.node.node_type,
            "title": ctx.node.title,
            "path": ctx.node.canonical_path,
            "symbol": ctx.node.symbol_qualified_name,
            "status": ctx.staleness,
            "upstream": [_edge_json(e, n) for e, n in ctx.upstream],
            "downstream": [_edge_json(e, n) for e, n in ctx.downstream],
            "verification": [
                {
                    "test": v.test_trace_id,
                    "outcome": v.outcome,
                    "proof_level": v.proof_level,
                    "current": v.current,
                }
                for v in ctx.verification
            ],
            "provenance": ctx.provenance,
        }
        return json.dumps(result, indent=2, default=str), False

    def why(args: dict[str, Any]) -> tuple[str, bool]:
        paths = engine.why(args["trace_id"])
        if not paths:
            return json.dumps({"error": f"no causal path for {args['trace_id']}"}), True
        result = [[n.trace_id for _, n in path] + [args["trace_id"]] for path in paths]
        return json.dumps(result, indent=2), False

    def impact(args: dict[str, Any]) -> tuple[str, bool]:
        imp = engine.impact(args["trace_id"], depth=args.get("depth", 3))
        result = {
            "semantic": [n.trace_id for n in imp.semantic],
            "structural": [n.trace_id for n in imp.structural],
            "tests": [n.trace_id for n in imp.tests],
            "stale": [[n.trace_id, s] for n, s in imp.stale],
        }
        return json.dumps(result, indent=2), False

    def verify(args: dict[str, Any]) -> tuple[str, bool]:
        result = engine.verify(
            scope=args.get("scope", "all"),
            lifecycle=args.get("lifecycle"),
        )
        payload = {
            "status": result.status,
            "policy": result.policy,
            "lifecycle": result.lifecycle,
            "blocking": result.blocking,
            "diagnostics": [d.to_json() for d in result.diagnostics],
        }
        return json.dumps(payload, indent=2), False

    def index(args: dict[str, Any]) -> tuple[str, bool]:
        report = (
            engine.index_changed()
            if args.get("scope", "changed") == "changed"
            else engine.index_all()
        )
        payload = {
            "nodes": report.nodes,
            "edges": report.edges,
            "markers": report.markers,
            "diagnostics": report.diagnostics,
            "changed_files": report.changed_files,
            "duration_ms": report.duration_ms,
        }
        return json.dumps(payload, indent=2), False

    return {
        "status": status,
        "search": search,
        "context": context,
        "why": why,
        "impact": impact,
        "verify": verify,
        "index": index,
    }


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle_message(
    msg: dict[str, Any], handlers: dict[str, ToolHandler], server_version: str
) -> list[dict[str, Any]]:
    """Handle one JSON-RPC message; returns responses (empty for notifications)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_request = "id" in msg
    if method == "initialize" and is_request:
        params = msg.get("params", {})
        requested = params.get("protocolVersion", PROTOCOL_VERSIONS[0])
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return [
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "tracelayer", "version": server_version},
                },
            }
        ]
    if method in ("notifications/initialized", "notifications/cancelled"):
        return []
    if method == "ping" and is_request:
        return [{"jsonrpc": "2.0", "id": msg_id, "result": {}}]
    if method == "tools/list" and is_request:
        return [{"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}]
    if method == "tools/call" and is_request:
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = handlers.get(name)
        if handler is None:
            return [_jsonrpc_error(msg_id, -32601, f"unknown tool: {name}")]
        try:
            text, is_error = handler(args)
        except Exception as exc:  # tool failures are reported, never fatal
            text = json.dumps({"error": str(exc)}, indent=2)
            is_error = True
        return [
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                },
            }
        ]
    if not is_request:
        return []
    return [_jsonrpc_error(msg_id, -32601, f"method not found: {method}")]


def run_mcp(engine: Engine, *, stdin=None, stdout=None) -> int:
    """Serve the MCP stdio transport until EOF. Returns the exit code."""
    from tracelayer import __version__  # avoid import cycle at module load

    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    handlers = _handlers(engine)

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Without an id we can only drop the line (parse errors cannot be
            # replied to reliably); log to stderr for debugging.
            print(f"mcp: invalid JSON: {line[:200]}", file=sys.stderr)
            continue
        for response in _handle_message(msg, handlers, __version__):
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
