"""Local web UI for the trace graph.

``trace web`` spawns a stdlib-only HTTP server (localhost) serving a
self-contained 3D force-directed visualization of the marker graph.
**Markers only**: the API exposes declared semantic edges
(``source_kind='declared'``, predicates from the marker ontology) — never
structural derivations like calls or imports.

Endpoints:
- ``GET /``              single-file HTML UI (bundled asset)
- ``GET /api/graph``     markers-only nodes + declared edges
- ``GET /api/node/<id>`` context detail for one trace node
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from tracelayer.engine import Engine
from tracelayer.protocol.ontology import SEMANTIC_EDGES

_HTML_PATH = Path(__file__).parent / "web" / "index.html"
_NODE_COUNT_CAP = 5000  # Threat T9 safeguard: never serialize unbounded graphs.


def _node_entry(node) -> dict:
    return {
        "id": node.trace_id,
        "type": node.node_type,
        "title": node.title or "",
        "status": node.status(),
        "path": node.canonical_path or "",
    }


# trace:v1 id=impl.web.ui work=WORK-TL-001
def graph_payload(engine: Engine) -> dict:
    """Markers-only graph: active nodes + declared semantic edges."""
    nodes: list[dict] = []
    edges: list[dict] = []
    by_uid: dict[str, object] = {}
    for node in sorted(engine.store.all_nodes(active_only=True), key=lambda n: n.trace_id):
        by_uid[node.entity_uid] = node
        nodes.append(_node_entry(node))
        if len(nodes) >= _NODE_COUNT_CAP:
            break
    for edge in sorted(
        engine.store.all_edges(status="active"),
        key=lambda e: (e.from_uid, e.predicate, e.to_uid),
    ):
        if edge.source_kind != "declared" or edge.predicate not in SEMANTIC_EDGES:
            continue
        src = by_uid.get(edge.from_uid)
        dst = by_uid.get(edge.to_uid)
        if src is None or dst is None:
            continue  # unresolved targets are diagnostics, not visuals
        edges.append({"source": src.trace_id, "target": dst.trace_id, "predicate": edge.predicate})
    return {"nodes": nodes, "edges": edges, "counts": {"nodes": len(nodes), "edges": len(edges)}}


# trace:v1 id=impl.web.node-detail work=WORK-html-work-and-question-browser-panel satisfies=REQ-richer-node-detail
def node_detail(engine: Engine, trace_id: str) -> dict | None:
    """Context detail for one trace node (mirrors ``trace context``)."""
    from tracelayer.query.context import ContextResult

    ctx: ContextResult | None = engine.context(trace_id)
    if ctx is None:
        return None
    return {
        "id": ctx.node.trace_id,
        "type": ctx.node.node_type,
        "title": ctx.node.title or "",
        "status": ctx.staleness,
        "path": ctx.node.canonical_path or "",
        "symbol": ctx.node.symbol_qualified_name or "",
        "upstream": [
            {"id": n.trace_id, "predicate": e.predicate, "status": e.status}
            for e, n in ctx.upstream
        ],
        "downstream": [
            {"id": n.trace_id, "predicate": e.predicate, "status": e.status}
            for e, n in ctx.downstream
        ],
        "verification": [
            {
                "test": v.test_trace_id,
                "outcome": v.outcome,
                "proof_level": v.proof_level,
                "current": v.current,
            }
            for v in ctx.verification
        ],
        "related": [
            {"section": header, "id": n.trace_id, "type": n.node_type} for header, n in ctx.related
        ],
        "adjacent": dict(ctx.adjacent or {}),
    }


# trace:v1 id=impl.web.work-ready work=WORK-global-setup-filesystem-hygiene-web-work-view-and-skill-refresh satisfies=REQ-web-work-view-data
def work_payload(engine: Engine, work_id: str) -> dict | None:
    """Ready/blocked work state for the work view (spec Section 65)."""
    from tracelayer.work import compute_readiness

    try:
        return compute_readiness(engine.store, work_id)
    except ValueError:
        return None


def _read_html() -> bytes:
    try:
        return _HTML_PATH.read_bytes()
    except OSError:
        return b"TraceLayer web UI asset missing (broken install)."


def _read_vendor() -> bytes:
    """Serve the bundled 3D-graph library (offline-first, NFR-001)."""
    try:
        return (_HTML_PATH.parent / "vendor" / "3d-force-graph.min.js").read_bytes()
    except OSError:
        return b"// vendor asset missing (broken install)"


# trace:exempt reason=internal-routing
class _Handler(BaseHTTPRequestHandler):
    engine: Engine | None = None  # set by run_web

    def log_message(self, *args: object) -> None:  # keep the console quiet
        pass

    def _engine(self) -> Engine:
        if self.engine is None:  # pragma: no cover - unreachable before run_web
            raise RuntimeError("engine not configured")
        return self.engine

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # trace:exempt reason=internal-routing
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path == "/":
            body = _read_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/vendor/3d-force-graph.min.js":
            body = _read_vendor()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/graph":
            self._send_json(graph_payload(self._engine()))
            return
        if path.startswith("/api/node/"):
            trace_id = unquote(path[len("/api/node/") :])
            detail = node_detail(self._engine(), trace_id)
            if detail is None:
                self._send_json({"error": f"unknown trace id: {trace_id}"}, status=404)
            else:
                self._send_json(detail)
            return
        if path.startswith("/api/work/") and path.endswith("/ready"):
            work_id = unquote(path[len("/api/work/") : -len("/ready")])
            payload = work_payload(self._engine(), work_id)
            if payload is None:
                self._send_json({"error": f"unknown work item: {work_id}"}, status=404)
            else:
                self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)


def run_web(
    engine: Engine,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Serve the graph UI until interrupted (Ctrl-C).

    Single-threaded on purpose: the graph store's SQLite connection belongs
    to the main thread, so every request must be handled there too.
    """
    _Handler.engine = engine
    server = HTTPServer((host, port), _Handler)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"TraceLayer graph UI: {url}  (markers only; Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
