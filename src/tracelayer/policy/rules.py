"""Deterministic policy rule functions TL001-TL062 + TL100/TL110.

Every rule takes an ``EvalContext`` and returns a list of ``Diagnostic``
built via ``tracelayer.diagnostics.make`` (registry-only rule IDs).

Two kinds of rules:

- *live* rules inspect the graph store (TL002, TL003, TL010, TL011, TL012,
  TL020, TL021, TL022, TL030, TL050, TL060, TL061, TL062, TL110);
- *re-emit* rules surface diagnostics stored at parse/index time, since the
  syntax errors they describe (duplicate IDs, malformed markers, unknown
  keys, config errors) are detected upstream (TL001, TL004, TL005, TL006,
  TL007, TL040, TL051, TL100).

Each rule handles whole-repo scope (``ctx.changed_ids is None``) by checking
every relevant node instead of only the changed set; TL012 is the exception —
without changed paths there is nothing to compare, so it emits nothing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from fnmatch import fnmatch

from tracelayer.diagnostics import Diagnostic, make
from tracelayer.discovery.boundaries import (
    boundary_is_traced,
    extract_boundaries,
    supported_extension,
)
from tracelayer.graph.models import Node
from tracelayer.policy.models import EvalContext
from tracelayer.protocol.ontology import OBSERVED_EDGES, SEMANTIC_EDGES

RuleFn = Callable[[EvalContext], list[Diagnostic]]

# Predicates that give an implementation its "requirement or work ancestry".
_ANCESTRY_PREDICATES = frozenset({"work", "satisfies"})


def _scope_nodes(ctx: EvalContext) -> list[Node]:
    """Active nodes in scope: changed trace IDs when given, else all nodes.

    ``store.all_nodes`` already orders by entity uid, so results are
    deterministic for a given store.
    """
    if ctx.changed_ids is None:
        return ctx.store.all_nodes(active_only=True)
    return [n for n in ctx.store.all_nodes(active_only=True) if n.trace_id in ctx.changed_ids]


def _stored(ctx: EvalContext, rule_id: str) -> list[Diagnostic]:
    """Re-emit diagnostics of ``rule_id`` from the last index."""
    return list(ctx.store.get_diagnostics(rule_id=rule_id))


def _has_outgoing(ctx: EvalContext, node: Node, predicates: frozenset[str]) -> bool:
    return any(
        e.status == "active" and e.predicate in predicates
        for e in ctx.store.edges_from(node.entity_uid)
    )


def _has_incoming(ctx: EvalContext, node: Node, predicates: frozenset[str]) -> bool:
    return any(
        e.status == "active" and e.predicate in predicates
        for e in ctx.store.edges_to(node.entity_uid)
    )


# --------------------------------------------------------------------------
# TL001-TL007: marker hygiene
# --------------------------------------------------------------------------


def rule_tl001(ctx: EvalContext) -> list[Diagnostic]:
    """Duplicate trace IDs — parse-time detection, re-emitted from the store.

    The nodes table enforces unique trace IDs, so duplicates can only have
    been observed at marker parse time; the indexer stores those findings.
    """
    return _stored(ctx, "TL001")


def rule_tl002(ctx: EvalContext) -> list[Diagnostic]:
    """Active edge whose target node is missing from the store."""
    diags: list[Diagnostic] = []
    for edge in sorted(ctx.store.all_edges(), key=lambda e: e.edge_uid):
        if edge.status != "active":
            continue
        if ctx.store.get_node(uid=edge.to_uid) is None:
            from_node = ctx.store.get_node(uid=edge.from_uid)
            diags.append(
                make(
                    "TL002",
                    trace_id=from_node.trace_id if from_node else None,
                    path=edge.source_path,
                    line=edge.source_line,
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"{edge.predicate} edge targets missing node "
                        f"{edge.to_uid} (declared in {edge.source_path or 'unknown'})"
                    ),
                )
            )
    return diags


def rule_tl003(ctx: EvalContext) -> list[Diagnostic]:
    """Markers detached/ambiguous in a language the parser supports.

    Consumes indexer metadata: ``structural_attachment`` in ("file",
    "ambiguous") with a non-generic ``parser_support`` means the marker did
    not land on a parsed symbol (spec 18.5 — generic parsers are honest
    file-level attachment and do NOT trip this rule).  A bare truthy
    ``metadata["detached"]`` flag is also honored.
    """
    diags: list[Diagnostic] = []
    for node in ctx.store.all_nodes(active_only=True):
        meta = node.metadata or {}
        attach = meta.get("structural_attachment")
        parser = meta.get("parser_support")
        detached = meta.get("detached") in (True, "true")
        if detached or (attach in ("file", "ambiguous") and parser not in (None, "generic")):
            diags.append(
                make(
                    "TL003",
                    trace_id=node.trace_id,
                    path=node.canonical_path,
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"Marker for {node.trace_id} is detached or ambiguous "
                        f"in a supported language (structural_attachment={attach!r})"
                    ),
                )
            )
    return diags


def rule_tl004(ctx: EvalContext) -> list[Diagnostic]:
    """Malformed marker syntax — re-emitted from the last index."""
    return _stored(ctx, "TL004")


def rule_tl005(ctx: EvalContext) -> list[Diagnostic]:
    """Invalid trace ID — re-emitted from the last index."""
    return _stored(ctx, "TL005")


def rule_tl006(ctx: EvalContext) -> list[Diagnostic]:
    """Duplicate key on one marker — re-emitted from the last index."""
    return _stored(ctx, "TL006")


def rule_tl007(ctx: EvalContext) -> list[Diagnostic]:
    """Invalid field value — re-emitted from the last index."""
    return _stored(ctx, "TL007")


# --------------------------------------------------------------------------
# TL010-TL012: changed behavior tracing
# --------------------------------------------------------------------------


def rule_tl010(ctx: EvalContext) -> list[Diagnostic]:
    """Changed implementation lacking work/satisfies ancestry.

    Whole-repo scope checks every active implementation node.
    """
    diags: list[Diagnostic] = []
    for node in _scope_nodes(ctx):
        if node.node_type != "implementation":
            continue
        if _has_outgoing(ctx, node, _ANCESTRY_PREDICATES):
            continue
        diags.append(
            make(
                "TL010",
                trace_id=node.trace_id,
                path=node.canonical_path,
                lifecycle=ctx.lifecycle,
                message=(
                    f"Implementation {node.trace_id} has no active work= or "
                    f"satisfies= edge providing requirement ancestry"
                ),
            )
        )
    return diags


def rule_tl011(ctx: EvalContext) -> list[Diagnostic]:
    """Changed requirement with a stale downstream node.

    "Downstream" artifacts are the nodes that point at the requirement
    (implementation --satisfies--> requirement, test --verifies--> ...); a
    node whose status is ``stale_review_required`` means the requirement
    change has not been reviewed against it yet.
    """
    diags: list[Diagnostic] = []
    for node in _scope_nodes(ctx):
        if node.node_type != "requirement":
            continue
        for edge in ctx.store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate not in SEMANTIC_EDGES:
                continue
            src = ctx.store.get_node(uid=edge.from_uid)
            if src is not None and src.status() == "stale_review_required":
                diags.append(
                    make(
                        "TL011",
                        trace_id=node.trace_id,
                        path=node.canonical_path,
                        lifecycle=ctx.lifecycle,
                        message=(
                            f"Changed requirement {node.trace_id} has stale "
                            f"downstream node {src.trace_id} "
                            f"({edge.predicate} edge)"
                        ),
                    )
                )
    return diags


# trace:v1 id=impl.policy.tl012 work=WORK-TL-001
def rule_tl013(ctx: EvalContext) -> list[Diagnostic]:
    """Behavior-boundary trace coverage (per-symbol, review P1).

    TL012 verifies that a changed FILE is claimed by some node; TL013
    verifies that every NEW or materially-changed behavioral boundary
    (function, class, method, heading, config key) inside a changed file
    is itself trace-accounted — marker attached, inherited from a traced
    parent, or explicitly exempted. This closes the loop where one marker
    in a file let any number of new untraced behaviors pass.
    """
    diags: list[Diagnostic] = []
    if not ctx.changed_paths or ctx.gitrepo is None:
        return diags
    excluded = ctx.project.policy.exclusions.paths if ctx.project.policy else []
    for path in sorted(ctx.changed_paths):
        if any(fnmatch(path, pat) for pat in excluded):
            continue
        if not supported_extension(path):
            continue
        try:
            current = (ctx.project.root / path).read_text(encoding="utf-8")
        except OSError:
            continue
        baseline = _baseline_text(ctx.gitrepo, path) or ""
        try:
            cur_bounds = extract_boundaries(path, current)
            base_bounds = extract_boundaries(path, baseline) if baseline else []
        except Exception:
            continue  # nosec B112: unparseable file -> skip (deterministic), never fail the gate
        base_by_name = {b.qualified_name or b.name: b for b in base_bounds}
        for boundary in cur_bounds:
            prior = base_by_name.get(boundary.qualified_name or boundary.name)
            if prior is None:
                changed = True  # new boundary (or renamed: re-trace it)
            elif boundary.kind == "heading":
                changed = False  # same title; body extends, not a change
            else:
                changed = _boundary_fp(prior) != _boundary_fp(boundary)
            if not changed:
                continue
            if boundary_is_traced(current, cur_bounds, boundary, ctx.project.root):
                continue
            diags.append(
                make(
                    "TL013",
                    path=path,
                    line=boundary.start_line,
                    message=(
                        f"Behavior boundary {boundary.kind} '{boundary.name}' is not "
                        "trace-accounted: add a trace:v1 marker above it, inherit from "
                        "a traced parent, or add `# trace:exempt reason=internal-detail`"
                    ),
                )
            )
    return diags


def _baseline_text(gitrepo, path: str) -> str | None:
    """HEAD content of ``path``, or None when the file is new."""
    try:
        r = gitrepo.run("show", f"HEAD:{path}")
        if r.returncode != 0:
            return None
        return r.stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _boundary_fp(boundary) -> str:
    from tracelayer.graph.fingerprints import normalize_block, semantic_fingerprint

    return semantic_fingerprint(normalize_block(boundary.source))


# trace:v1 id=impl.policy.tl014 work=WORK-TL-001
def rule_tl014(ctx: EvalContext) -> list[Diagnostic]:
    """Plan-level expected obligations (review P2).

    A plan marker may declare ``expects=<id>,<id>``: artifacts the plan
    commits to producing. Every expected id must exist as an active node
    with an ``implements`` edge back to the plan; anything missing blocks
    (the plan's obligations are durable, enforced by CI and the stop gate).
    """
    diags: list[Diagnostic] = []
    for node in ctx.store.all_nodes(active_only=True):
        if node.node_type != "plan":
            continue
        expected = node.metadata.get("expects")
        if not expected:
            continue
        implemented = {
            e.from_uid
            for e in ctx.store.edges_to(node.entity_uid)
            if e.status == "active" and e.predicate == "implements"
        }
        for expected_id in expected:
            target = ctx.store.get_node(trace_id=expected_id)
            if target is None or not target.active:
                diags.append(
                    make(
                        "TL014",
                        path=node.canonical_path or None,
                        trace_id=node.trace_id,
                        message=(
                            f"Plan {node.trace_id} expects {expected_id}, which does not "
                            "exist as an active node"
                        ),
                    )
                )
                continue
            if target.entity_uid not in implemented:
                diags.append(
                    make(
                        "TL014",
                        path=node.canonical_path or None,
                        trace_id=node.trace_id,
                        message=(
                            f"Plan {node.trace_id} expects {expected_id}, but no "
                            "active `implements` edge links it to the plan"
                        ),
                    )
                )
    return diags


def rule_tl012(ctx: EvalContext) -> list[Diagnostic]:
    """Changed path with no traced behavior.

    The core first-marker enforcement: a changed path counts as traced only
    when at least one active node has it as canonical path, so untraced
    repositories cannot silently absorb behavior changes (the pre/post/stop
    hooks and the CI gate all key off this).  Policy exclusion globs
    (``[policy] exclusions.paths``) are honored.  Whole-repo scope (no
    changed paths) emits nothing.
    """
    diags: list[Diagnostic] = []
    if not ctx.changed_paths:
        return diags
    excluded = ctx.project.policy.exclusions.paths if ctx.project.policy else []
    traced = {n.canonical_path for n in ctx.store.all_nodes(active_only=True) if n.canonical_path}
    for path in sorted(ctx.changed_paths):
        if any(fnmatch(path, pat) for pat in excluded):
            continue
        if path not in traced:
            diags.append(
                make(
                    "TL012",
                    path=path,
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"Changed path {path} has no traced behavior marker "
                        f"(no active node claims it)"
                    ),
                )
            )
    return diags


# --------------------------------------------------------------------------
# TL020-TL022: verification and execution evidence
# --------------------------------------------------------------------------


def rule_tl020(ctx: EvalContext) -> list[Diagnostic]:
    """Requirement without any incoming verifies edge.

    Only ``requirement`` nodes are checked; NFRs and other intent types are
    out of scope for this rule (simplest deterministic reading of the
    contract).
    """
    diags: list[Diagnostic] = []
    for node in _scope_nodes(ctx):
        if node.node_type != "requirement":
            continue
        if _has_incoming(ctx, node, frozenset({"verifies"})):
            continue
        diags.append(
            make(
                "TL020",
                trace_id=node.trace_id,
                path=node.canonical_path,
                lifecycle=ctx.lifecycle,
                message=f"Requirement {node.trace_id} has no active verifies= edge",
            )
        )
    return diags


def _outcomes_for(ctx: EvalContext) -> list:
    """Outcomes of the latest evidence run at the evaluated revision.

    With no revision, the latest run overall is used; a missing run means no
    evidence, so callers treat every check as failed.
    """
    run = ctx.store.latest_evidence_run(ctx.revision)
    if run is None:
        return []
    return ctx.store.outcomes_for_run(run["run_id"])


def _test_passed(ctx: EvalContext, test_node: Node, outcomes: list) -> bool:
    """Latest outcome for a linked test is pass, matched by uid or id."""
    for outcome in outcomes:
        if outcome.test_uid is not None and outcome.test_uid == test_node.entity_uid:
            return outcome.outcome == "pass"
    for outcome in outcomes:
        if outcome.test_uid is not None:
            continue  # binding was used; an unbound match is not authoritative
        if outcome.framework_id in (
            test_node.trace_id,
            (test_node.metadata or {}).get("framework_test_id"),
        ):
            return outcome.outcome == "pass"
    return False


def rule_tl021(ctx: EvalContext) -> list[Diagnostic]:
    """Linked test's latest outcome is not pass, or is missing."""
    linked: dict[str, tuple[Node, list[str]]] = {}
    for node in _scope_nodes(ctx):
        if node.node_type != "requirement":
            continue
        for edge in ctx.store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate != "verifies":
                continue
            test_node = ctx.store.get_node(uid=edge.from_uid)
            if test_node is not None:
                entry = linked.setdefault(test_node.entity_uid, (test_node, []))
                entry[1].append(node.trace_id)
    diags: list[Diagnostic] = []
    outcomes = _outcomes_for(ctx)
    for test_node, reqs in linked.values():
        if _test_passed(ctx, test_node, outcomes):
            continue
        diags.append(
            make(
                "TL021",
                trace_id=test_node.trace_id,
                path=test_node.canonical_path,
                lifecycle=ctx.lifecycle,
                message=(
                    f"Linked test {test_node.trace_id} (verifies "
                    f"{', '.join(reqs)}) did not pass at revision "
                    f"{ctx.revision or '(unknown)'}"
                ),
            )
        )
    return diags


def rule_tl022(ctx: EvalContext) -> list[Diagnostic]:
    """exercises edge without the required execution evidence.

    Required proof level follows ``[evidence] preferred_coverage_proof``:
    "suite" needs level >= 1 (any execution edge), "per_test" needs >= 2.
    When a revision is evaluated, evidence must exist at that exact revision;
    old-revision or absent evidence fails every exercises edge (semantic-hash
    invalidation is approximated revision-wise here).
    """
    from tracelayer.evidence.freshness import proof_level

    impl_uids = {n.entity_uid for n in _scope_nodes(ctx) if n.node_type == "implementation"}
    preferred = ctx.project.config.evidence.preferred_coverage_proof
    required = 2 if preferred == "per_test" else 1
    current = True
    if ctx.revision is not None:
        current = ctx.store.latest_evidence_run(ctx.revision) is not None
    diags: list[Diagnostic] = []
    for edge in sorted(ctx.store.all_edges(), key=lambda e: e.edge_uid):
        if edge.status != "active" or edge.predicate != "exercises":
            continue
        if edge.to_uid not in impl_uids:
            continue
        impl = ctx.store.get_node(uid=edge.to_uid)
        if impl is None:
            continue
        level = proof_level(ctx.store, edge.from_uid, edge.to_uid)
        if level < required or not current:
            test = ctx.store.get_node(uid=edge.from_uid)
            diags.append(
                make(
                    "TL022",
                    trace_id=impl.trace_id,
                    path=impl.canonical_path,
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"exercises edge from "
                        f"{test.trace_id if test else edge.from_uid} to "
                        f"{impl.trace_id} lacks required execution evidence "
                        f"(proof level {level} < {required}"
                        f"{', not current' if not current else ''})"
                    ),
                )
            )
    return diags


# --------------------------------------------------------------------------
# TL030: deletion hygiene
# --------------------------------------------------------------------------


def rule_tl030(ctx: EvalContext) -> list[Diagnostic]:
    """Inactive node with an active incoming semantic edge.

    One diagnostic per node (first offending edge).  Observed edges
    (executed/passed/...) are historical records and never unresolved.
    """
    inactive = [n for n in ctx.store.all_nodes(active_only=False) if not n.active]
    if ctx.changed_ids is not None:
        inactive = [n for n in inactive if n.trace_id in ctx.changed_ids]
    diags: list[Diagnostic] = []
    for node in sorted(inactive, key=lambda n: n.trace_id):
        for edge in ctx.store.edges_to(node.entity_uid):
            if edge.status != "active" or edge.predicate in OBSERVED_EDGES:
                continue
            src = ctx.store.get_node(uid=edge.from_uid)
            diags.append(
                make(
                    "TL030",
                    trace_id=node.trace_id,
                    path=node.canonical_path,
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"Deleted node {node.trace_id} still has an active "
                        f"incoming {edge.predicate} edge from "
                        f"{src.trace_id if src else edge.from_uid}"
                    ),
                )
            )
            break
    return diags


# --------------------------------------------------------------------------
# TL040-TL100: stored diagnostics and configuration
# --------------------------------------------------------------------------


def rule_tl040(ctx: EvalContext) -> list[Diagnostic]:
    """Unknown marker keys — re-emitted from the last index."""
    return _stored(ctx, "TL040")


def rule_tl050(ctx: EvalContext) -> list[Diagnostic]:
    """Evidence run bound to a different revision than the one evaluated."""
    if ctx.revision is None:
        return []
    diags: list[Diagnostic] = []
    for run in ctx.store.get_evidence_runs():
        run_rev = run.get("revision")
        if run_rev is not None and run_rev != ctx.revision:
            diags.append(
                make(
                    "TL050",
                    lifecycle=ctx.lifecycle,
                    message=(
                        f"Evidence run {run.get('run_id')} is bound to revision "
                        f"{run_rev}, evaluated revision is {ctx.revision}"
                    ),
                )
            )
    return diags


def rule_tl051(ctx: EvalContext) -> list[Diagnostic]:
    """Evidence parser failures — re-emitted from the last ingest/index."""
    return _stored(ctx, "TL051")


def rule_tl060(ctx: EvalContext) -> list[Diagnostic]:
    """Independent semantic audit result missing or non-conforming."""
    result = ctx.audit_result
    if result is not None and result.get("schema") == "tracelayer-audit-result/v1":
        return []
    return [
        make(
            "TL060",
            lifecycle=ctx.lifecycle,
            message=(
                "Independent semantic audit result is missing or not in "
                "tracelayer-audit-result/v1 schema"
            ),
        )
    ]


def rule_tl061(ctx: EvalContext) -> list[Diagnostic]:
    """Expired waiver records block (strict profiles)."""
    policy = ctx.project.policy
    if policy is None:
        return []
    diags: list[Diagnostic] = []
    for waiver in policy.waivers:
        if waiver.expired():
            diags.append(
                make(
                    "TL061",
                    trace_id=waiver.trace_id,
                    path=waiver.path,
                    lifecycle=ctx.lifecycle,
                    metadata={"owner": waiver.owner, "reason": waiver.reason},
                    message=(
                        f"Waiver for {waiver.rule} expired {waiver.expires} (owner={waiver.owner})"
                    ),
                )
            )
    return diags


def rule_tl062(ctx: EvalContext) -> list[Diagnostic]:
    """Evidence not bound to an exact revision (safety-critical)."""
    if ctx.revision is None:
        return []
    diags: list[Diagnostic] = []
    for run in ctx.store.get_evidence_runs():
        if run.get("revision") is None:
            diags.append(
                make(
                    "TL062",
                    lifecycle=ctx.lifecycle,
                    message=(f"Evidence run {run.get('run_id')} is not bound to an exact revision"),
                )
            )
    return diags


def rule_tl100(ctx: EvalContext) -> list[Diagnostic]:
    """Configuration errors — re-emitted from the last index/load."""
    return _stored(ctx, "TL100")


def rule_tl110(ctx: EvalContext) -> list[Diagnostic]:
    """Stale nodes block merge/release when block_stale is enabled.

    Only the ``stale_review_required`` status counts as blocking here;
    ``reviewed_needs_verification`` awaits evidence and is handled by
    TL021/TL022.
    """
    if ctx.lifecycle not in ("merge", "release"):
        return []
    diags: list[Diagnostic] = []
    for node in _scope_nodes(ctx):
        if node.status() == "stale_review_required":
            diags.append(
                make(
                    "TL110",
                    trace_id=node.trace_id,
                    path=node.canonical_path,
                    lifecycle=ctx.lifecycle,
                    message=f"Stale node {node.trace_id} blocks {ctx.lifecycle}",
                )
            )
    return diags


RULE_FUNCTIONS: dict[str, RuleFn] = {
    "TL001": rule_tl001,
    "TL002": rule_tl002,
    "TL003": rule_tl003,
    "TL004": rule_tl004,
    "TL005": rule_tl005,
    "TL006": rule_tl006,
    "TL007": rule_tl007,
    "TL010": rule_tl010,
    "TL011": rule_tl011,
    "TL012": rule_tl012,
    "TL013": rule_tl013,
    "TL014": rule_tl014,
    "TL020": rule_tl020,
    "TL021": rule_tl021,
    "TL022": rule_tl022,
    "TL030": rule_tl030,
    "TL040": rule_tl040,
    "TL050": rule_tl050,
    "TL051": rule_tl051,
    "TL060": rule_tl060,
    "TL061": rule_tl061,
    "TL062": rule_tl062,
    "TL100": rule_tl100,
    "TL110": rule_tl110,
}
