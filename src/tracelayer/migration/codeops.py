"""CodeOps migration: scan, plan, apply (spec Section 33, contract §M).

Reads legacy ``codeops:trace`` annotations and proposes first-class
``trace:v1`` markers. Classification is deterministic from marker content plus
attachment context (test-file detection by path). ``apply_plan`` rewrites ONLY
items classified ``deterministic`` or ``high_confidence``; everything else is
left for human/agent review per spec 33.3 ("never perform semantic rewrites in
one opaque automatic step").

Ambiguities (simplest deterministic behavior, per build contract):
- ``build_plan`` has no graph store, so ``spec=`` targets are resolved
  syntactically (fragment after the last ``#``, else the bare value when it is
  a valid trace ID); whether the requirement exists is verified at
  apply/verify time (TL002).
- Proposed node IDs are derived deterministically with
  ``protocol.generate_id`` (unique within the plan); reviewers may adjust them
  in the plan file before ``--apply``.
- A marker's classification is the highest-priority classification among its
  edge-contributing fields only (work_item / plan / resolvable spec). Note-only
  fields (test, doc, ops, prompt, incident, evidence, jira_ref, github_ref,
  notion_ref, commit, unknown keys, unresolvable spec) are recorded in the
  item note and scan diagnostics but never demote a marker that has a real
  edge (spec 33.4). A marker with no edge-contributing field falls back to the
  worst of its remaining non-dropped fields. Nothing mapped into the new
  marker is lost: every field is either carried into the marker, or recorded
  in the item note (and scan diagnostics).
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tracelayer.config import Project, TraceConfig
from tracelayer.diagnostics import SEVERITY_INFO, Diagnostic, make
from tracelayer.protocol import (
    ParsedMarker,
    generate_id,
    infer_node_type,
    is_valid_id,
    render_marker,
)

CODEOPS_PREFIX = "codeops:trace"
MAX_SCAN_BYTES = 2 * 1024 * 1024

FIELD_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Documented CodeOps fields (spec 33.1) plus the accepted undocumented
# variants (spec 33.2: ops=, incident=).
KNOWN_FIELDS = frozenset({
    "work_item", "spec", "plan", "test", "doc", "ops", "prompt", "incident",
    "commit", "jira_ref", "github_ref", "notion_ref", "evidence",
})
NONCANONICAL_FIELDS = frozenset({"ops", "incident"})

_SOURCE_SUFFIXES = frozenset({
    ".py", ".pyw", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh",
    ".hpp",
})

# Classification priority: higher wins for the marker.
_CLASS_PRIORITY = {
    "dropped": 0,
    "deterministic": 1,
    "high_confidence": 2,
    "derived": 3,
    "requires_review": 4,
}
CLASSIFICATIONS = tuple(_CLASS_PRIORITY)


@dataclass
class CodeOpsMarker:
    path: str
    line: int
    raw: str
    fields: dict[str, str]


@dataclass
class MigrationItem:
    path: str
    line: int
    classification: str  # deterministic|high_confidence|requires_review|dropped|derived
    new_marker: str | None  # canonical trace:v1 line or None
    note: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    raw: str = ""  # original codeops line (for unchanged detection)


@dataclass
class MigrationPlan:
    schema: str  # "tracelayer-migration/v1"
    items: list[MigrationItem]
    summary: dict  # counts per classification


def _matches(rel: str, pat: str) -> bool:
    """fnmatch with ``**/*`` also matching top-level files.

    Plain ``fnmatch`` treats ``**`` like ``*``, so ``**/*`` requires a ``/``
    and silently excludes root-level files; that is corrected here.
    """
    if fnmatch.fnmatch(rel, pat):
        return True
    return pat == "**/*" and "/" not in rel


def _iter_text_files(root: Path, config: TraceConfig) -> Iterator[Path]:
    """Yield root-relative text files to scan, sorted and bounded.

    Honors config.discovery include/exclude/generated globs (fnmatch), skips
    the cache dir, oversized files, binary files, and symlinks escaping root.
    """
    include = config.discovery.include or ["**/*"]
    exclude = list(config.discovery.exclude) + [".git/**", "__pycache__/**"]
    generated = list(config.discovery.generated) + [config.cache_dir + "/**"]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(_matches(rel, pat) for pat in exclude):
            continue
        if any(_matches(rel, pat) for pat in generated):
            continue
        if not any(_matches(rel, pat) for pat in include):
            continue
        if path.is_symlink():
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
        except OSError:
            continue
        yield path


def _parse_fields(
    payload: str, path: str, line: int
) -> tuple[dict[str, str], list[Diagnostic]]:
    """Permissively parse the fields after ``codeops:trace``.

    ``key=value`` tokens split on whitespace (first ``=``); blank values are
    recorded as ``""`` (spec 33.1 step 9). Tokens without ``=`` and invalid
    keys are skipped with an INFO diagnostic. Unknown and undocumented
    (ops=/incident=) keys are kept and flagged TL040 INFO (spec 33.2: accept
    permissively, emit diagnostics showing noncanonical fields).
    """
    fields: dict[str, str] = {}
    diags: list[Diagnostic] = []
    for tok in payload.split():
        if "=" not in tok:
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=path, line=line,
                message=f"Malformed codeops field token {tok!r} ignored",
            ))
            continue
        key, value = tok.split("=", 1)
        if not FIELD_KEY_RE.match(key):
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=path, line=line,
                message=f"Malformed codeops field key {key!r} ignored",
            ))
            continue
        if key in fields:
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=path, line=line,
                message=f"Duplicate codeops field {key!r}; first value kept",
            ))
            continue
        if key not in KNOWN_FIELDS:
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=path, line=line,
                message=f"Unknown codeops field {key}={value} preserved for review",
            ))
        elif key in NONCANONICAL_FIELDS:
            diags.append(make(
                "TL040", severity=SEVERITY_INFO, path=path, line=line,
                message=(
                    f"Undocumented codeops field {key}={value} accepted "
                    "permissively; mapped to requires_review"
                ),
            ))
        fields[key] = value
    return fields, diags


def scan_codeops(root: Path, config: TraceConfig) -> tuple[list[CodeOpsMarker], list[Diagnostic]]:
    """Find every line containing ``codeops:trace`` under ``root``.

    Permissive: unknown fields are kept (with INFO diagnostics), blank values
    are recorded as ``""``. Returns markers sorted by (path, line).
    """
    markers: list[CodeOpsMarker] = []
    diags: list[Diagnostic] = []
    for path in _iter_text_files(root, config):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:8192]:
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), start=1):
            if CODEOPS_PREFIX not in line:
                continue
            idx = line.index(CODEOPS_PREFIX)
            payload = line[idx + len(CODEOPS_PREFIX):]
            # Block-comment closers that share the line (mirrors the trace:v1
            # grammar); a value containing `-->` cannot be represented.
            payload = payload.split("-->", 1)[0].split("*/", 1)[0]
            fields, fdiags = _parse_fields(payload, rel, i)
            diags.extend(fdiags)
            markers.append(CodeOpsMarker(path=rel, line=i, raw=line, fields=fields))
    markers.sort(key=lambda m: (m.path, m.line))
    return markers, diags


def _is_test_file(rel: str) -> bool:
    """Test-file detection by path: tests/ dir, ``*_test.*``, or ``test_*``."""
    parts = rel.split("/")
    if any(p in ("tests", "test") for p in parts):
        return True
    name = parts[-1]
    if name.startswith("test_"):
        return True
    return bool(re.search(r"_test\.", name))


def _is_source_file(rel: str) -> bool:
    """True for known implementation source-code suffixes."""
    return Path(rel).suffix.lower() in _SOURCE_SUFFIXES


def _node_type_for(rel: str) -> str:
    """Node type proposed for a migrated marker by file kind."""
    if _is_test_file(rel):
        return "test"
    suffix = Path(rel).suffix.lower()
    if suffix in (".md", ".markdown", ".rst", ".txt"):
        return "document"
    if suffix in (".yaml", ".yml", ".json", ".toml"):
        return "config"
    if suffix == ".sh":
        return "operation"
    return "implementation"


def _resolve_spec(value: str) -> str | None:
    """Extract a requirement ID from a ``spec=`` value (syntactic resolution).

    Takes the fragment after the last ``#`` when present, else the bare value.
    Resolves only when the fragment is a valid trace ID whose namespace infers
    a requirement-family type (requirement/nfr/goal/prd); a bare file path
    like ``specs/billing.md`` is NOT an ID and yields None (review). The store
    is not consulted at plan time (documented in the module docstring).
    """
    fragment = value.rsplit("#", 1)[-1] if "#" in value else value
    fragment = fragment.strip()
    if is_valid_id(fragment) and infer_node_type(fragment) in (
        "requirement", "nfr", "goal", "prd",
    ):
        return fragment
    return None


def _split_line(raw: str) -> tuple[str, str]:
    """Split a codeops line into (prefix, suffix) around the annotation.

    Prefix: text before ``codeops:trace`` (comment opener, e.g. ``# `` or
    ``<!-- ``). Suffix: text after the last parsed ``key=value`` token (e.g.
    `` -->`` for HTML comments), so a rewritten line keeps its wrapping.
    """
    if CODEOPS_PREFIX not in raw:
        return "", ""
    idx = raw.index(CODEOPS_PREFIX)
    prefix = raw[:idx]
    tail = raw[idx + len(CODEOPS_PREFIX):]
    consumed = 0
    last_field_end = 0
    for tok in tail.split():
        end = tail.find(tok, consumed) + len(tok)
        consumed = end
        if "=" in tok:
            last_field_end = end
    return prefix, tail[last_field_end:]


def _field_classification(
    key: str, value: str, *, test_file: bool, source_file: bool
) -> tuple[str, str]:
    """Map one codeops field to (classification, note) per spec 33.1."""
    if not value:
        return "dropped", f"{key}= blank placeholder omitted"
    if key == "work_item":
        if is_valid_id(value):
            return "deterministic", f"{key}={value} -> work={value}"
        return "requires_review", f"{key}={value} is not a valid trace ID; review required"
    if key == "plan":
        if is_valid_id(value):
            return "deterministic", f"{key}={value} -> implements={value}"
        return "requires_review", f"{key}={value} is not a valid trace ID; review required"
    if key == "spec":
        req = _resolve_spec(value)
        if req is None:
            return "requires_review", (
                f"{key}={value} cannot be resolved to a requirement ID"
            )
        if test_file:
            return "high_confidence", f"{key}={value} -> verifies={req} (test attachment)"
        if source_file:
            return "high_confidence", (
                f"{key}={value} -> satisfies={req} (implementation attachment)"
            )
        return "requires_review", (
            f"{key}={value} -> {req}: attachment context unclear; requires review"
        )
    if key in ("test", "doc", "ops", "prompt", "incident", "evidence"):
        return "requires_review", (
            f"{key}={value} proposes a first-class artifact; requires review"
        )
    if key in ("jira_ref", "github_ref", "notion_ref"):
        return "requires_review", (
            f"{key}={value} consolidated onto the work node; requires review"
        )
    if key == "commit":
        return "derived", f"{key}={value} dropped from source; recorded as import metadata"
    return "requires_review", f"unknown field {key}={value} preserved for review"


def build_plan(markers: list[CodeOpsMarker], project: Project) -> MigrationPlan:
    """Classify CodeOps markers into a migration plan (spec 33.1/33.4).

    Item classification is computed over edge-contributing fields only
    (work_item / plan / resolvable spec), taking the highest-priority class
    among them; note-only fields never demote a marker that has at least one
    real edge. A marker with no edge-contributing field falls back to the
    worst of its remaining non-dropped fields (an all-requires_review marker
    stays requires_review; a commit-only marker becomes derived).
    ``new_marker`` is the verbatim replacement line (comment prefix and any
    trailing comment closer preserved); it is None for dropped markers and for
    markers with no name source (work_item / plan / resolved spec). Every
    field is carried into the marker or recorded in the item note (no silent
    field loss). ``project`` is used only for scope context; the store is not
    available at plan time.
    """
    items: list[MigrationItem] = []
    taken: set[str] = set()
    for m in markers:
        test_file = _is_test_file(m.path)
        source_file = not test_file and _is_source_file(m.path)
        classes: list[str] = []
        notes: list[str] = []
        edges: dict[str, list[str]] = {}
        name_source: str | None = None
        edge_classes: list[str] = []
        for key, value in m.fields.items():
            cls, note = _field_classification(
                key, value, test_file=test_file, source_file=source_file
            )
            classes.append(cls)
            notes.append(note)
            if cls in ("deterministic", "high_confidence") and value:
                if key == "work_item":
                    edges.setdefault("work", []).append(value)
                    name_source = name_source or value
                    edge_classes.append(cls)
                elif key == "plan":
                    edges.setdefault("implements", []).append(value)
                    name_source = name_source or value
                    edge_classes.append(cls)
                elif key == "spec":
                    req = _resolve_spec(value)
                    if req is not None:
                        edge = "verifies" if test_file else "satisfies"
                        edges.setdefault(edge, []).append(req)
                        name_source = name_source or req
                        edge_classes.append(cls)
        if edge_classes:
            # Spec 33.4: classify over edge-contributing fields only; note-only
            # fields (test/doc/ops/…/commit/unknown, unresolvable spec) never
            # demote a marker that has at least one real edge.
            classification = max(
                edge_classes, key=lambda c: _CLASS_PRIORITY[c]
            )
        else:
            # No edge-contributing field: fall back to the worst non-dropped
            # field so all-requires_review markers stay reviewable and a
            # commit-only marker becomes derived.
            remaining = [c for c in classes if c != "dropped"]
            classification = (
                max(remaining, key=lambda c: _CLASS_PRIORITY[c])
                if remaining else "dropped"
            )
        item_diags: list[Diagnostic] = []
        for key, value in m.fields.items():
            if key == "spec" and value and _resolve_spec(value) is None:
                item_diags.append(make(
                    "TL002", severity=SEVERITY_INFO, path=m.path, line=m.line,
                    message=f"spec={value} has no resolvable requirement ID; review required",
                ))
        new_marker: str | None = None
        if classification != "dropped" and name_source and edges:
            node_type = _node_type_for(m.path)
            trace_id = generate_id(node_type, name_source, taken=taken)
            taken.add(trace_id)
            prefix, suffix = _split_line(m.raw)
            marker = ParsedMarker(
                path=m.path, line=m.line, column=1, raw=m.raw,
                trace_id=trace_id, node_type=node_type, edges=edges,
            )
            new_marker = prefix + render_marker(marker) + suffix
        note = "; ".join(notes) if notes else "no mappable fields"
        items.append(MigrationItem(
            path=m.path, line=m.line, classification=classification,
            new_marker=new_marker, note=note, diagnostics=item_diags, raw=m.raw,
        ))
    summary = {
        c: sum(1 for it in items if it.classification == c) for c in CLASSIFICATIONS
    }
    return MigrationPlan(schema="tracelayer-migration/v1", items=items, summary=summary)


def apply_plan(
    plan: MigrationPlan, root: Path, config: TraceConfig, *, dry_run: bool = False
) -> dict:
    """Rewrite deterministic + high_confidence items in place (spec 33.3).

    requires_review / dropped / derived items are never edited. Paths are
    confined to ``root``. ``dry_run=True`` reports what would change without
    writing. Returns a per-file report:
    {"dry_run": bool, "applied": int, "changed_files": int,
     "files": {rel: {class counts, "rewritten": int}}}
    """
    by_path: dict[str, list[tuple[int, str]]] = {}
    file_counts: dict[str, dict[str, int]] = {}
    for item in plan.items:
        counts = file_counts.setdefault(item.path, {
            c: 0 for c in CLASSIFICATIONS
        })
        counts[item.classification] += 1
        if item.classification not in ("deterministic", "high_confidence"):
            continue
        if item.new_marker is None or item.new_marker == item.raw:
            continue
        full = (root / item.path).resolve()
        if not full.is_relative_to(root.resolve()):
            continue
        by_path.setdefault(item.path, []).append((item.line, item.new_marker))
    applied = sum(len(v) for v in by_path.values())
    for rel, reps in by_path.items():
        file_counts[rel]["rewritten"] = len(reps)
    if dry_run:
        return {"dry_run": True, "applied": applied, "files": file_counts}
    changed_files = 0
    for rel, replacements in sorted(by_path.items()):
        full = (root / rel).resolve()
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.split("\n")
        crlf = "\r\n" in text
        for lineno, new_line in sorted(replacements):
            if 1 <= lineno <= len(lines):
                lines[lineno - 1] = new_line + ("\r" if crlf else "")
        new_text = "\n".join(lines)
        if new_text == text:
            continue
        tmp = full.with_name(full.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, full)
        changed_files += 1
    return {"dry_run": False, "applied": applied, "changed_files": changed_files,
            "files": file_counts}
