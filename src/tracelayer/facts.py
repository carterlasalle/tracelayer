"""Canonical facts and values: one source, tracked dependents (spec Sections 94-112).

A FACT/VALUE node records ``canonical_source`` (``path::selector``) and its
last-verified ``value``. Verification reads the LIVE canonical source
through format adapters (TOML/JSON/YAML, Python constants, text claims)
and reads each dependent's LIVE content via its ``selector`` — never by
comparing two hand-copied metadata strings. Consumers that cannot be read
(e.g. generated files without selectors) keep the legacy ``value=``
comparison; consumers with neither report UNVERIFIED, never CURRENT.

Canonical sources are confined to the repository (symlink-aware). A
``.trace/facts.toml`` manifest may declare values and consumers for
artifacts that cannot carry markers (generated files, JSON configs).
Nothing is auto-rewritten.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

from tracelayer.graph.store import GraphStore

FACT_TYPES = ("fact", "value")

# Dependent predicates from the dependent's perspective (spec Sections 98, 121).
_DEPENDENT_PREDICATES = (
    "depends_on_value",
    "documents_value",
    "mirrors_value",
    "derives_value",
    "generated_from",
    "historical_reference",
)

MANIFEST_FILE = ".trace/facts.toml"

_KEYVAL_RE = re.compile(r"^\s*(?:ARG\s+|ENV\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.+?)\s*$")


# trace:exempt reason=internal-helper
def _confine(root: Path | str, path_part: str) -> Path | None:
    """Resolve ``path_part`` strictly beneath ``root`` (symlink-aware).

    Returns None for absolute escapes, ``..`` traversal, missing files, or
    symlinks pointing outside the repository. Fail-closed by design.
    """
    try:
        root_resolved = Path(root).resolve()
        candidate = (root_resolved / path_part).resolve()
    except (OSError, ValueError):
        return None
    if candidate == root_resolved or root_resolved in candidate.parents:
        return candidate if candidate.is_file() else None
    return None


# trace:exempt reason=internal-helper
def _render(value: object) -> str:
    """Scalar rendering shared by every adapter."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True)


# trace:exempt reason=internal-helper
def _traverse(data: object, dotted: str) -> tuple[bool, object]:
    """Walk dotted keys through nested dicts."""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


# trace:exempt reason=internal-helper
def _read_structured(path: Path, selector: str) -> tuple[bool, str]:
    """TOML/JSON/YAML dotted-key extraction."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, ""
    try:
        if path.suffix == ".toml":
            data = tomllib.loads(text)
        elif path.suffix in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
    except Exception:
        return False, ""
    found, node = _traverse(data, selector)
    return (True, _render(node)) if found else (False, "")


# trace:exempt reason=internal-helper
def _read_python_const(path: Path, selector: str) -> tuple[bool, str]:
    """Top-level ``NAME = <constant>`` extraction via AST."""
    if not selector or "." in selector or not selector.isidentifier():
        return False, ""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return False, ""
    for stmt in tree.body:
        targets: list = []
        value = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id == selector:
                if isinstance(value, ast.Constant) and not isinstance(value.value, bytes):
                    return True, _render(value.value)
                return False, ""
    return False, ""


# trace:exempt reason=internal-helper
def _read_claim(path: Path, selector: str) -> tuple[bool, str]:
    """Text claim extraction: ``regex:<pattern>`` or ``KEY = value`` lines."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, ""
    if selector.startswith("regex:"):
        try:
            match = re.search(selector[len("regex:") :], text)
        except re.error:
            return False, ""
        if not match:
            return False, ""
        groups = match.groups()
        return True, (groups[0] if groups else match.group(0)).strip()
    for line in text.splitlines():
        match = _KEYVAL_RE.match(line)
        if match and match.group(1) == selector:
            return True, match.group(2).strip().strip("\"'")
    return False, ""


# trace:exempt reason=internal-helper
def read_value(root: Path | str, source: str) -> tuple[bool, str]:
    """Read ``path::selector`` confined to ``root`` across adapters."""
    path_part, sep, selector = str(source or "").partition("::")
    if not sep or not selector:
        return False, ""
    path = _confine(root, path_part)
    if path is None:
        return False, ""
    if path.suffix in (".toml", ".json", ".yaml", ".yml"):
        return _read_structured(path, selector)
    if path.suffix == ".py":
        return _read_python_const(path, selector)
    return _read_claim(path, selector)


# trace:exempt reason=internal-helper
def read_canonical(root: Path | str, source: str) -> tuple[bool, str]:
    """Read ``path::selector`` confined to ``root`` (adapter-dispatched)."""
    return read_value(root, source)


# trace:exempt reason=internal-helper
def read_manifest(root: Path | str) -> dict:
    """Parse ``.trace/facts.toml`` into ``{fact_id: entry}``; {} when absent."""
    path = Path(root) / MANIFEST_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    facts = data.get("facts", {})
    return dict(facts) if isinstance(facts, dict) else {}


# trace:exempt reason=internal-helper
def _observe_consumer(
    root: Path | str, path: str | None, selector: object, legacy: object
) -> tuple[str | None, str]:
    """Live content of a dependent: (observed value or None, via).

    Selector-based live reads win; the legacy ``value=`` property is the
    fallback; neither means UNVERIFIED — never assumed CURRENT.
    """
    if isinstance(selector, str) and selector and path:
        found, live = read_value(root, f"{path}::{selector}")
        if found:
            return live, "selector"
    if legacy is not None:
        return str(legacy), "recorded"
    return None, "none"


# trace:v1 id=impl.facts.verify work=WORK-durable-knowledge-nodes-and-canonical-facts satisfies=REQ-canonical-fact-tracking
def verify_facts(store: GraphStore, root: Path | str) -> list[dict]:
    """Drift check for every active FACT/VALUE node with a canonical source."""
    manifest = read_manifest(root)
    nodes = [n for n in store.all_nodes(active_only=True) if n.node_type in FACT_TYPES]
    seen: set[str] = set()
    results = []
    for node in sorted(nodes, key=lambda n: n.trace_id):
        seen.add(node.trace_id)
        results.append(
            _verify_node(
                store,
                root,
                manifest,
                node.trace_id,
                str(node.metadata.get("canonical_source") or ""),
                node.metadata.get("value"),
            )
        )
    for fact_id in sorted(manifest):
        if fact_id in seen:
            continue
        entry = manifest[fact_id] if isinstance(manifest[fact_id], dict) else {}
        results.append(
            _verify_node(
                store,
                root,
                manifest,
                fact_id,
                str(entry.get("canonical_source") or ""),
                entry.get("value"),
            )
        )
    return results


# trace:exempt reason=internal-helper
def _verify_node(
    store: GraphStore, root: Path | str, manifest: dict, fact_id: str, source: str, recorded: object
) -> dict:
    """Verify one fact against its live source and live dependents."""
    if not source:
        return {
            "id": fact_id,
            "status": "UNVERIFIED",
            "dependents": [],
            "canonical_source": "",
            "canonical": None,
            "recorded": None,
        }
    found, current = read_value(root, source)
    recorded_text = str(recorded) if recorded is not None else None
    if not found:
        status = "REVIEW_REQUIRED"
    elif recorded_text is None or recorded_text == current:
        status = "CURRENT"
    else:
        status = "REVIEW_REQUIRED"
    dependents = []
    node = store.get_node(trace_id=fact_id)
    edges = store.edges_to(node.entity_uid) if node is not None else []
    manifest_consumers = []
    for entry in manifest.values():
        if not isinstance(entry, dict):
            continue
        for consumer in entry.get("consumers", []) or []:
            if isinstance(consumer, dict) and consumer.get("for") == fact_id:
                manifest_consumers.append(consumer)
    for edge in edges:
        if edge.status != "active" or edge.predicate not in _DEPENDENT_PREDICATES:
            continue
        consumer = store.get_node(uid=edge.from_uid)
        if consumer is None or not consumer.active:
            continue
        dependents.append(
            _verify_consumer(
                root,
                consumer.trace_id,
                edge.predicate,
                consumer.canonical_path,
                consumer.metadata.get("selector"),
                consumer.metadata.get("value"),
                current if found else None,
            )
        )
    for consumer in manifest_consumers:
        dependents.append(
            _verify_consumer(
                root,
                str(consumer.get("path", "")),
                str(consumer.get("predicate", "mirrors_value")),
                str(consumer.get("path", "")),
                consumer.get("selector"),
                consumer.get("value"),
                current if found else None,
            )
        )
    dependents.sort(key=lambda d: d["id"])
    return {
        "id": fact_id,
        "type": node.node_type if node is not None else "value",
        "canonical_source": source,
        "canonical": current if found else None,
        "recorded": recorded_text,
        "status": status,
        "dependents": dependents,
    }


# trace:exempt reason=internal-helper
def _verify_consumer(
    root: Path | str,
    consumer_id: str,
    predicate: str,
    path: str | None,
    selector: object,
    legacy: object,
    canonical: str | None,
) -> dict:
    """One dependent: live-observed value vs live canonical value."""
    if predicate == "historical_reference":
        return {
            "id": consumer_id,
            "predicate": predicate,
            "path": path,
            "status": "CURRENT",
            "observed": None,
            "expected": canonical,
        }
    observed, _ = _observe_consumer(root, path, selector, legacy)
    if observed is None:
        status = "UNVERIFIED"
    elif canonical is None:
        status = "REVIEW_REQUIRED"
    else:
        status = "CURRENT" if observed == canonical else "REVIEW_REQUIRED"
    return {
        "id": consumer_id,
        "predicate": predicate,
        "path": path,
        "status": status,
        "observed": observed,
        "expected": canonical,
    }


# trace:exempt reason=internal-helper
def consumers_of(store: GraphStore, root: Path | str, path: str) -> list[dict]:
    """Facts whose canonical source lives in ``path``, with live dependents.

    Pre-edit coaching input: editing this file may drift these consumers.
    """
    out = []
    for result in verify_facts(store, root):
        source = result.get("canonical_source") or ""
        if source.partition("::")[0] != path:
            continue
        out.append(
            {
                "id": result["id"],
                "canonical": result.get("canonical"),
                "dependents": [
                    {"id": d["id"], "predicate": d["predicate"], "status": d["status"]}
                    for d in result.get("dependents", [])
                ],
            }
        )
    return out
