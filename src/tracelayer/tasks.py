"""Ambient task lifecycle: bootstrap, resolve, activate, finalize (Ambient §39).

TraceLayer's zero-ceremony machinery. The AGENT calls these; the user never
does. IDs are minted deterministically from titles (stable slugs), the spec
and work item are written atomically, the graph is indexed, and the session
context is activated — so a natural-language request becomes a fully traced
task without any user-facing TraceLayer ceremony.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tracelayer.config import Project
from tracelayer.discovery.suggest import _slug
from tracelayer.graph.store import GraphStore
from tracelayer.hooks.session_state import SessionState

SPEC_PATH = "docs/spec.md"


# trace:exempt reason=internal-helper
def _mint_id(kind: str, title: str, store: GraphStore, allocated: set[str] | None = None) -> str:
    """Deterministic stable ID: ``WORK-<slug>`` / ``REQ-<slug>`` / ``PLAN-<slug>``.

    Uniqueness: when the id already exists in the graph (or was already
    allocated earlier in this bootstrap transaction), append ``-2``, ``-3``...
    so repeated bootstrap calls — and same-transaction slug collisions like
    "API Rate Limit" vs "API-Rate-Limit" — never collide (review P0).
    """
    base = f"{kind}-{_slug(title)}"
    candidate = base
    counter = 2
    while store.get_node(trace_id=candidate) is not None or (allocated and candidate in allocated):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


# trace:exempt reason=internal-helper
def _work_entry(bundle: dict, session_id: str, work_id: str) -> dict:
    """The ``[work."ID"]`` entry: title + durable origin provenance."""
    entry: dict = {"title": bundle.get("title") or work_id, "status": "active"}
    origin: dict = {
        "source": bundle.get("kind", "user_request"),
    }
    if bundle.get("intent"):
        origin["intent"] = bundle["intent"]
    if session_id:
        origin["session"] = session_id
    prompt = bundle.get("prompt")
    if prompt:
        origin["prompt_hash"] = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()[:16]
    entry["origin"] = origin
    return entry


# trace:exempt reason=internal-helper
def _spec_markdown(bundle: dict, work_id: str, req_ids: dict[str, str]) -> str:
    """Render the minimal spec document with requirement markers.

    ``req_ids`` maps each requirement title to its minted REQ id. The
    document's own structural headings (title/Goal/Requirements) are
    trace-accounted so the generated spec passes the boundary gate.
    """
    slug = _slug(bundle.get("title") or work_id)
    lines = [f"# {bundle.get('title') or work_id}", ""]
    lines += [f"<!-- trace:v1 id=doc.{slug} -->", ""]
    if bundle.get("intent"):
        lines += [
            "<!-- trace:exempt reason=document-structure -->",
            "## Goal",
            "",
            str(bundle["intent"]),
            "",
        ]
    lines += ["<!-- trace:exempt reason=document-structure -->", "## Requirements", ""]
    for req in bundle.get("requirements", []):
        title = str(req.get("title") or "Requirement")
        rid = req_ids[title]
        # The heading carries the stable id (REQ-<slug>) so the block body
        # — statement + acceptance criteria — becomes the requirement node.
        lines += [f"### {rid} — {title}", ""]
        lines += [f"<!-- trace:v1 id={rid} type=requirement work={work_id} -->", ""]
        lines += [str(req.get("statement") or ""), ""]
        for criterion in req.get("acceptance", []) or []:
            lines += [f"- acceptance: {criterion}", ""]
    return "\n".join(lines)


# trace:exempt reason=internal-helper
def _plan_markdown(bundle: dict, work_id: str, plan_id: str) -> str:
    lines = [f"# {plan_id}", ""]
    lines += [f"<!-- trace:v1 id={plan_id} type=plan work={work_id} -->", ""]
    lines += ["<!-- trace:exempt reason=document-structure -->", "## Steps", ""]
    for step in bundle.get("plan", {}).get("steps", []) or []:
        lines += [f"- {step}", ""]
    return "\n".join(lines)


# Behavioral task kinds must carry at least one requirement; refactor-like
# kinds may legitimately add none (adversarial review P0).
_BEHAVIORAL_KINDS = frozenset(
    {
        "GREENFIELD_PROJECT",
        "NEW_FEATURE",
        "FEATURE_EXTENSION",
        "BEHAVIOR_CHANGE",
        "BUG_CONTRACT_BACKFILL",
    }
)
_NON_BEHAVIORAL_KINDS = frozenset({"REFACTOR", "MAINTENANCE", "NON_BEHAVIORAL_EDIT"})
_VALID_KINDS = _BEHAVIORAL_KINDS | _NON_BEHAVIORAL_KINDS


# trace:exempt reason=internal-helper
def _normalize_kind(kind: object) -> str | None:
    """Accept 'greenfield_project', 'GREENFIELD_PROJECT', 'greenfield-project',
    'new feature'... -> canonical UPPER_SNAKE; None when unrecognized."""
    if not isinstance(kind, str) or not kind.strip():
        return None
    canon = kind.strip().upper().replace("-", "_").replace(" ", "_")
    return canon if canon in _VALID_KINDS else None


# trace:exempt reason=internal-helper
def validate_bundle(bundle: object) -> list[str]:
    """Schema-validate a TaskBootstrapBundle; return human errors (review P0).

    A behavioral task kind with zero requirements must be rejected — the
    causal gate must never allow new product behavior with only ``work=``.
    """
    errors: list[str] = []
    if not isinstance(bundle, dict):
        return ["bundle must be a JSON object (title, kind, intent, requirements, plan)"]
    title = bundle.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title: non-empty string required")
    kind = _normalize_kind(bundle.get("kind"))
    if kind is None:
        errors.append(
            "kind: one of " + ", ".join(sorted(k.lower() for k in _VALID_KINDS)) + " required"
        )
    requirements = bundle.get("requirements", [])
    if requirements is None:
        requirements = []
    if not isinstance(requirements, list):
        errors.append("requirements: must be a list")
        requirements = []
    for idx, req in enumerate(requirements):
        if not isinstance(req, dict):
            errors.append(f"requirements[{idx}]: must be an object with a title")
            continue
        rtitle = req.get("title")
        if not isinstance(rtitle, str) or not rtitle.strip():
            errors.append(f"requirements[{idx}]: title: non-empty string required")
        statement = req.get("statement")
        if statement is not None and not isinstance(statement, str):
            errors.append(f"requirements[{idx}]: statement must be a string")
        acceptance = req.get("acceptance")
        if acceptance is not None and (
            not isinstance(acceptance, list) or not all(isinstance(a, str) for a in acceptance)
        ):
            errors.append(f"requirements[{idx}]: acceptance must be a list of strings")
    if kind in _BEHAVIORAL_KINDS and not requirements:
        errors.append(
            f"requirements: at least one requirement is required for kind {kind.lower()} — "
            "new behavioral product intent gets a spec before implementation"
        )
    plan = bundle.get("plan")
    if plan is not None and not isinstance(plan, dict):
        errors.append("plan: must be an object with optional 'recommended' and 'steps'")
    intent = bundle.get("intent")
    if intent is not None and not isinstance(intent, str):
        errors.append("intent: must be a string")
    return errors


# trace:exempt reason=internal-helper
def bundle_from_prompt(prompt: str) -> dict:
    """Deterministic minimal bundle derived from the user's prose.

    The semantic agent may refine this (``--json``) later; this primitive
    makes the zero-ceremony path real: prose alone yields work + spec +
    requirement + plan with no user-facing TraceLayer input (review P0).
    """
    text = " ".join(str(prompt).split())
    title = (text[:72] + "…") if len(text) > 72 else text
    if not title:
        title = "Task"
    return {
        "title": title,
        "kind": "new_feature",
        "intent": text,
        "requirements": [
            {
                "title": "Implement " + (title[:60] + "…" if len(title) > 60 else title),
                "statement": text,
            }
        ],
        "plan": {"recommended": True, "steps": [f"Implement: {text}"]},
    }


# trace:v1 id=impl.ambient.bootstrap work=WORK-TL-001
def bootstrap(
    project: Project,
    bundle: dict,
    *,
    session_id: str,
    spec_path: str | None = None,
) -> dict:
    """Atomically create work + spec + requirements (+ plan), index, activate.

    Returns the resulting graph slice (the agent's handle on the task).
    """
    errors = validate_bundle(bundle)
    if errors:
        raise ValueError("invalid bootstrap bundle: " + "; ".join(errors))
    from tracelayer.engine import Engine

    engine = Engine(project)
    try:
        store = engine.store
        allocated: set[str] = set()
        work_id = _mint_id("WORK", bundle.get("title") or "task", store, allocated)
        allocated.add(work_id)
        req_ids: dict[str, str] = {}
        for req in bundle.get("requirements", []) or []:
            title = str(req.get("title") or "Requirement")
            rid = _mint_id("REQ", title, store, allocated)
            allocated.add(rid)
            req_ids[title] = rid

        root: Path = project.root
        spec_rel = spec_path or _artifact_path(root, SPEC_PATH, work_id)
        spec_file = root / spec_rel
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text(_spec_markdown(bundle, work_id, req_ids), encoding="utf-8")

        work_file = root / ".trace" / "work.toml"
        work_file.parent.mkdir(parents=True, exist_ok=True)
        existing = work_file.read_text(encoding="utf-8") if work_file.exists() else ""
        entry = _work_entry(bundle, session_id, work_id)
        import tomllib

        try:
            data = tomllib.loads(existing) if existing.strip() else {}
        except tomllib.TOMLDecodeError:
            data = {}
        work_sections = data.setdefault("work", {})
        work_sections[work_id] = entry
        rendered = [
            "[work.%s]" % _toml_key(work_id),
            f"title = {_toml_value(entry['title'])}",
            'status = "active"',
        ]
        for k, v in entry.get("origin", {}).items():
            rendered.append(f"origin.{k} = {_toml_value(v)}")
        rendered.append("")
        new_block = "\n".join(rendered)
        if existing.strip():
            work_file.write_text(existing.rstrip("\n") + "\n" + new_block, encoding="utf-8")
        else:
            work_file.write_text(new_block, encoding="utf-8")

        plan_id = None
        if bundle.get("plan", {}).get("recommended"):
            plan_id = _mint_id("PLAN", bundle.get("title") or "task", store, allocated)
            allocated.add(plan_id)
            plan_file = root / _artifact_path(
                root, f"docs/plan-{_slug(bundle.get('title') or 'task')}.md", work_id, suffix="plan"
            )
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(_plan_markdown(bundle, work_id, plan_id), encoding="utf-8")

        engine.index_all()
    finally:
        engine.close()

    state = SessionState(project)
    state.set_active_work(session_id, work_id)
    state.set_active_requirements(session_id, list(req_ids.values()))
    if plan_id:
        state.set_active_plan(session_id, plan_id)
    state.clear_pending_bootstrap(session_id)
    state.clear_pending_spec_update(session_id)

    return {
        "work": work_id,
        "title": bundle.get("title"),
        "kind": bundle.get("kind"),
        "spec_path": spec_rel,
        "requirements": [{"id": rid, "title": title} for title, rid in req_ids.items()],
        "plan": plan_id,
        "active": {
            "work": work_id,
            "requirements": list(req_ids.values()),
            "plan": plan_id,
        },
    }


# trace:v1 id=impl.ambient.activate work=WORK-TL-001
def activate(project: Project, store: GraphStore, work_id: str, session_id: str) -> dict:
    """Activate a work item with ALL its requirements resolved from the graph."""
    work = store.get_node(trace_id=work_id)
    if work is None or not work.active:
        raise ValueError(f"no active work node: {work_id}")
    requirements: list[str] = []
    plan_id: str | None = None
    for edge in store.edges_to(work.entity_uid):
        src = store.get_node(uid=edge.from_uid)
        if src is None:
            continue
        if src.node_type == "requirement":
            requirements.append(src.trace_id)
        if src.node_type == "plan" and plan_id is None:
            plan_id = src.trace_id
        if src.node_type == "implementation":
            for sat in store.edges_from(src.entity_uid, "satisfies"):
                target = store.get_node(uid=sat.to_uid)
                if target is not None and target.trace_id not in requirements:
                    requirements.append(target.trace_id)
            for imp in store.edges_from(src.entity_uid, "implements"):
                target = store.get_node(uid=imp.to_uid)
                if target is not None and plan_id is None:
                    plan_id = target.trace_id
    state = SessionState(project)
    state.set_active_work(session_id, work_id)
    if requirements:
        state.set_active_requirements(session_id, sorted(set(requirements)))
    if plan_id:
        state.set_active_plan(session_id, plan_id)
    return {
        "work": work_id,
        "requirements": sorted(set(requirements)),
        "plan": plan_id,
    }


# trace:v1 id=impl.ambient.record_receipt work=WORK-TL-001
def record_receipt(
    project: Project,
    session_id: str,
    *,
    path: str,
    operation: str,
    targets: list[str],
    before: str = "",
    after: str = "",
    harness: str = "",
) -> None:
    """Mutation receipt (Ambient §27): derived history of which work item
    touched which traced boundaries, attached to the commit at finalization.
    """
    state = SessionState(project)
    work = state.active_work(session_id)
    if not work:
        return
    from tracelayer.git.repo import GitRepo

    commit = None
    try:
        repo = GitRepo.open(project.root)
        if repo is not None:
            commit = repo.rev()
    except Exception:
        commit = None
    receipt = {
        "work": work,
        "operation": operation,
        "path": path,
        "targets": targets,
        "before": before,
        "after": after,
        "session": session_id,
        "harness": harness,
        "commit": commit,
    }
    receipts_dir = project.root / ".trace" / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    import json as _json

    path_file = receipts_dir / "receipts.jsonl"
    with path_file.open("a", encoding="utf-8") as fh:
        fh.write(_json.dumps(receipt, sort_keys=True) + "\n")


# trace:v1 id=impl.ambient.finish work=WORK-TL-001
def finish(
    project: Project,
    store: GraphStore,
    *,
    session_id: str,
    lifecycle: str = "merge",
) -> dict:
    """Task finalization (Ambient §32-33): run the completion gate; when it
    passes, mark the work item done and clear the session context.

    Work becomes ``done`` only under merge-grade policy (requirement
    ancestry, verifying test, passed evidence, no stale blockers) — "the
    agent may stop coding" and "WORK status = done" are deliberately
    separated (adversarial review P0). On success, mutation receipts are
    bound to the commit that now contains the work's changes.
    """
    from tracelayer.engine import Engine
    from tracelayer.git.repo import GitRepo

    state = SessionState(project)
    work = state.active_work(session_id)
    pending = state.pending_obligations(session_id)
    engine = Engine(project, GitRepo.open(project.root))
    try:
        changed = engine.verify(scope="changed", lifecycle=lifecycle)
        whole = engine.verify(scope="all", lifecycle=lifecycle)
        blocking = changed.blocking or whole.blocking or bool(pending)
        diagnostics = list(changed.diagnostics) + list(whole.diagnostics)
    finally:
        engine.close()
    if blocking:
        return {
            "status": "blocked",
            "work": work,
            "lifecycle": lifecycle,
            "pending_obligations": len(pending),
            "diagnostics": [d.rule_id for d in diagnostics if d.severity == "ERROR"][:10],
        }
    if work:
        _set_work_status(project, work, "done")
        _bind_receipts(project, work)
    state.clear(session_id)
    receipts = _receipt_count(project, work) if work else 0
    return {
        "status": "done",
        "work": work,
        "lifecycle": lifecycle,
        "verify": "pass",
        "receipts": receipts,
    }


# trace:exempt reason=internal-helper
def _bind_receipts(project: Project, work_id: str) -> int:
    """Bind the work's mutation receipts to the commit that contains them.

    Receipts are recorded at post-mutation time, when HEAD is the OLD
    commit; the change lands in the NEXT commit. At finalization the
    work's changes are in HEAD, so every receipt for the work is rebound
    to ``git rev-parse HEAD`` (adversarial review P0: receipts must bind
    to the eventual commit, not the pre-mutation SHA).
    """
    from tracelayer.git.repo import GitRepo

    path = project.root / ".trace" / "receipts" / "receipts.jsonl"
    if not path.exists():
        return 0
    import json as _json

    repo = GitRepo.open(project.root)
    head = repo.rev() if repo is not None else None
    if head is None:
        return 0
    bound = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = _json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if rec.get("work") == work_id and rec.get("commit") != head:
            rec["commit"] = head
            rec["bound"] = True
            bound += 1
        out.append(_json.dumps(rec, sort_keys=True))
    if bound:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return bound


# trace:v1 id=impl.ambient.intake work=WORK-TL-001
def intake(
    project: Project,
    store: GraphStore,
    *,
    session_id: str,
    kind: str,
    work: str | None = None,
    requirements: list[str] | None = None,
) -> dict:
    """Semantic intake classification (adversarial review P0: spec evolution).

    The agent classifies the user request after resolution; TraceLayer then
    ENFORCES the consequence deterministically:

    - behavior_change: implementation edits are gated until the governing
      requirement's text actually changes (fingerprint) or intake is
      re-run with kind=implementation_only;
    - new_feature/greenfield_project/bug_contract_backfill: equivalent to
      the prompt hook's pending-bootstrap state;
    - refactor/maintenance/non_behavioral_edit: implementation_only —
      clears all pending intake state.
    """
    canon = _normalize_kind(kind)
    if canon is None:
        raise ValueError(
            "kind: one of " + ", ".join(sorted(k.lower() for k in _VALID_KINDS)) + " required"
        )
    state = SessionState(project)
    resolved: dict = {"kind": canon, "work": None, "requirements": []}
    if work:
        node = store.get_node(trace_id=work)
        if node is None or node.node_type != "work":
            raise ValueError(f"unknown work item: {work}")
        state.set_active_work(session_id, work)
        resolved["work"] = work
    reqs: list[str] = []
    for rid in requirements or []:
        node = store.get_node(trace_id=rid)
        if node is None or node.node_type != "requirement":
            raise ValueError(f"unknown requirement: {rid}")
        if rid not in reqs:
            reqs.append(rid)
    if reqs:
        state.set_active_requirements(session_id, reqs)
        resolved["requirements"] = reqs
    if canon in _BEHAVIORAL_KINDS:
        state.clear_pending_bootstrap(session_id)
        if canon == "BEHAVIOR_CHANGE" and reqs:
            state.set_pending_spec_update(session_id, reqs)
        elif canon == "BEHAVIOR_CHANGE" and not reqs and work:
            req_ids = _requirements_for(store, work)
            if req_ids:
                state.set_pending_spec_update(session_id, req_ids)
                resolved["requirements"] = req_ids
    else:
        state.clear_pending_bootstrap(session_id)
        state.clear_pending_spec_update(session_id)
    resolved["state"] = {
        "pending_bootstrap": state.pending_bootstrap(session_id),
        "pending_spec_update": state.pending_spec_update(session_id),
    }
    return resolved


# trace:exempt reason=internal-helper
def _set_work_status(project: Project, work_id: str, status: str) -> None:
    """Rewrite the work item's status in .trace/work.toml (deterministic)."""
    path = project.root / ".trace" / "work.toml"
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").split("\n")
    in_block = False
    changed = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(f'[work."{work_id}"]'):
            in_block = True
            continue
        if in_block:
            if ln.strip().startswith("["):
                break
            if ln.strip().startswith("status ="):
                lines[i] = f'status = "{status}"'
                changed = True
                break
    if not changed and not in_block:
        lines += [f'[work."{work_id}"]', f'title = "{work_id}"', f'status = "{status}"', ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# trace:exempt reason=internal-helper
def _receipt_count(project: Project, work_id: str) -> int:
    path = project.root / ".trace" / "receipts" / "receipts.jsonl"
    if not path.exists():
        return 0
    import json as _json

    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and _json.loads(line).get("work") == work_id
    )


# trace:v1 id=impl.ambient.context work=WORK-TL-001
def context(project: Project, session_id: str) -> dict:
    """The agent-visible task context (internal API)."""
    state = SessionState(project)
    return {
        "work": state.active_work(session_id),
        "requirements": state.active_requirements(session_id),
        "plan": state.active_plan(session_id),
        "pending_obligations": state.pending_obligations(session_id),
        "pending_bootstrap": state.pending_bootstrap(session_id),
        "pending_spec_update": state.pending_spec_update(session_id),
    }


# trace:v1 id=impl.ambient.resolve work=WORK-TL-001
def resolve(
    project: Project,
    store: GraphStore,
    prompt: str,
    *,
    session_id: str,
    gitrepo=None,
) -> dict:
    """Resolve a natural-language request to an existing task (Ambient §25).

    Signals, deterministically scored:
      A  explicit active session work          -> 1.0
      E  prompt token overlap with work title  -> strong
      E  prompt token overlap with the work's  -> strong
         requirement titles
      E  FTS hits on traced artifacts mapped   -> medium
         back to their work item
      B  current branch name vs work slug      -> medium
      D  recency of active work items          -> small

    ``resolution`` is ``resume`` when a best match clears the threshold,
    else ``new`` (the spec prefers a new work item over false attribution).
    """
    if not prompt.strip():
        return {
            "resolution": "new",
            "work": None,
            "confidence": 0.0,
            "requirements": [],
            "source": [],
        }
    state = SessionState(project)
    prompt_tokens = set(_tokenize(prompt))
    explicit = state.active_work(session_id)
    if explicit:
        reqs = _requirements_for(store, explicit)
        active = store.get_node(trace_id=explicit)
        active_tokens = set(_tokenize(active.title or active.trace_id)) if active else set()
        req_tokens: set[str] = set()
        for rid in reqs:
            rn = store.get_node(trace_id=rid)
            if rn is not None:
                req_tokens |= set(_tokenize(rn.title or rn.trace_id))
        referential = bool(prompt_tokens & active_tokens) or bool(prompt_tokens & req_tokens)
        continuity = any(w in prompt_tokens for w in ("continue", "keep", "again"))
        low = prompt.lower()
        demonstrative = any(w in low for w in ("this", "that", " it ")) or low.endswith(" it")
        if referential or continuity or demonstrative:
            return {
                "resolution": "resume",
                "work": explicit,
                "confidence": 1.0,
                "requirements": reqs,
                "source": ["active_session"],
            }
        # An unrelated prompt under an active session falls through to normal
        # scoring so genuinely new intent can resolve to "new" (spec §26).

    prompt_tokens = set(_tokenize(prompt))
    if not prompt_tokens:
        return {
            "resolution": "new",
            "work": None,
            "confidence": 0.0,
            "requirements": [],
            "source": [],
        }
    branch = None
    if gitrepo is not None:
        try:
            branch = gitrepo.current_branch()
        except Exception:
            branch = None
    branch_tokens = set(_tokenize(branch or ""))

    # Continuity beyond titles (adversarial review P0): mutation receipts,
    # changed-boundary ownership, and recent Git history let a fresh
    # session resolve "continue what I was changing on this branch".
    receipt_paths = _receipt_paths_by_work(project)
    changed_work_owners = _work_owners_for_paths(store, _changed_paths(gitrepo))
    history_work_owners = _work_owners_for_paths(store, _recent_history_paths(gitrepo))

    open_works = _open_work_statuses(project)
    works = sorted(
        (n for n in store.all_nodes(active_only=True) if n.node_type == "work"),
        key=lambda n: n.trace_id,
    )
    fts_hits = set()
    try:
        for node in store.search(prompt, limit=10):
            fts_hits.add(node.trace_id)
    except Exception:
        fts_hits = set()

    best: tuple[float, str, list[str], list[str]] = (0.0, "", [], [])
    for work in works:
        reqs = _requirements_for(store, work.trace_id)
        req_nodes = [store.get_node(trace_id=r) for r in reqs]
        req_titles = [n.title or n.trace_id for n in req_nodes if n is not None]
        score = 0.0
        sources: list[str] = []
        title_tokens = set(_tokenize(work.title or work.trace_id))
        overlap = prompt_tokens & title_tokens
        if overlap:
            score += 2.0 * len(overlap) / len(prompt_tokens)
            sources.append("work_title")
        for rt in req_titles:
            rt_tokens = set(_tokenize(rt))
            if prompt_tokens & rt_tokens:
                score += 1.5 / len(req_titles) if req_titles else 0.0
                if "requirement_title" not in sources:
                    sources.append("requirement_title")
        if fts_hits & set(reqs + [work.trace_id]):
            score += 0.6
            sources.append("fts")
        if branch_tokens & (set(_tokenize(work.trace_id)) | title_tokens):
            score += 0.4
            sources.append("branch")
        # Ownership signals: paths this work mutated (receipts), paths that
        # are changed right now, and paths in the most recent commit.
        work_paths = receipt_paths.get(work.trace_id, set())
        work_boundary_tokens = _boundary_tokens_for_paths(store, work_paths)
        overlap = prompt_tokens & work_boundary_tokens
        if overlap and work_paths:
            score += 0.5 * len(overlap) / len(prompt_tokens)
            if "receipts" not in sources:
                sources.append("receipts")
        if work.trace_id in changed_work_owners:
            score += 0.4
            if "changed_boundaries" not in sources:
                sources.append("changed_boundaries")
        if work.trace_id in history_work_owners:
            score += 0.3
            if "git_history" not in sources:
                sources.append("git_history")
        if open_works.get(work.trace_id) == "done":
            continue  # completed work is not resumable; follow-ups create new work (§45)
        if open_works.get(work.trace_id) == "active":
            score += 0.3
            if "open_work" not in sources:
                sources.append("open_work")
        if score > best[0]:
            best = (score, work.trace_id, reqs, sources)

    confidence = min(1.0, best[0])
    if confidence >= 0.55:
        return {
            "resolution": "resume",
            "work": best[1],
            "confidence": round(confidence, 2),
            "requirements": best[2],
            "source": best[3],
        }
    return {
        "resolution": "new",
        "work": None,
        "confidence": round(confidence, 2),
        "requirements": [],
        "source": best[3],
    }


# trace:exempt reason=internal-helper
def _receipt_paths_by_work(project: Project) -> dict[str, set[str]]:
    """work id -> set of mutated paths from .trace/receipts/receipts.jsonl."""
    path = project.root / ".trace" / "receipts" / "receipts.jsonl"
    if not path.exists():
        return {}
    import json as _json

    out: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = _json.loads(line)
        except ValueError:
            continue
        work = rec.get("work")
        rel = rec.get("path")
        if work and isinstance(rel, str) and rel:
            out.setdefault(work, set()).add(rel)
    return out


# trace:exempt reason=internal-helper
def _changed_paths(gitrepo) -> list[str]:
    """Repo-relative paths of the current working-tree change set."""
    if gitrepo is None:
        return []
    try:
        return [f.path for f in gitrepo.changed_files() if f.path]
    except Exception:
        return []


# trace:exempt reason=internal-helper
def _recent_history_paths(gitrepo, max_commits: int = 3) -> list[str]:
    """Paths touched by the most recent commits (newest first)."""
    if gitrepo is None:
        return []
    try:
        r = gitrepo.run("log", f"-{max_commits}", "--name-only", "--format=")
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


# trace:exempt reason=internal-helper
def _work_owners_for_paths(store: GraphStore, paths: list[str]) -> set[str]:
    """Work ids owning traced nodes at ``paths`` (via declared work edges)."""
    owners: set[str] = set()
    for n in store.all_nodes(active_only=True):
        if not n.canonical_path or n.canonical_path not in paths:
            continue
        for edge in store.edges_to(n.entity_uid):
            if edge.predicate != "work":
                continue
            src = store.get_node(uid=edge.from_uid)
            if src is not None:
                owners.add(src.trace_id)
    return owners


# trace:exempt reason=internal-helper
def _boundary_tokens_for_paths(store: GraphStore, paths: set[str]) -> set[str]:
    """Tokens of boundary names at ``paths`` (work ownership vocabulary)."""
    tokens: set[str] = set()
    for n in store.all_nodes(active_only=True):
        if not n.canonical_path or n.canonical_path not in paths:
            continue
        label = n.symbol_qualified_name or n.title or n.trace_id
        tokens |= set(_tokenize(str(label)))
    return tokens


# trace:exempt reason=internal-helper
def _req_fingerprint_changed(project: Project, store: GraphStore, req_id: str) -> bool:
    """True when the requirement's current file text differs from the index.

    Compares the live Markdown block (fingerprint + title) against the
    indexed node's stored fingerprint — no reindex needed, so the authoring
    gate can tell "the agent updated the requirement" from "the requirement
    is untouched" deterministically (adversarial review P0: spec evolution
    enforcement).
    """
    node = store.get_node(trace_id=req_id)
    if node is None or not node.canonical_path:
        return False
    path = project.root / node.canonical_path
    if not path.is_file():
        return True  # the requirement's file is gone: it changed
    try:
        from tracelayer.artifacts.markdown import extract_markdown_blocks

        text = path.read_text(encoding="utf-8")
        blocks = extract_markdown_blocks(node.canonical_path, text, project.config)
    except (OSError, UnicodeDecodeError, Exception):
        return False
    block = next((b for b in blocks if b.trace_id == req_id), None)
    if block is None:
        return True  # marker removed from the file: it changed
    if node.artifact_fingerprint is None:
        return False  # no indexed baseline to compare; other gates apply
    return block.fingerprint != node.artifact_fingerprint or block.title != node.title


# trace:exempt reason=internal-helper
def _reconcile_spec_updates(project: Project, store: GraphStore, session_id: str) -> list[str]:
    """Drop spec-update requirements whose text actually changed; return the rest."""
    state = SessionState(project)
    pending = state.pending_spec_update(session_id)
    if not pending:
        return []
    remaining = [rid for rid in pending if not _req_fingerprint_changed(project, store, rid)]
    resolved = [rid for rid in pending if rid not in remaining]
    for rid in resolved:
        data = state._read(session_id)
        pending_list = data.get("pending_spec_update", [])
        if rid in pending_list:
            pending_list.remove(rid)
        state._write(session_id, data)
    return remaining


# trace:exempt reason=internal-helper
def _open_work_statuses(project: Project) -> dict[str, str]:
    """work.toml statuses (status=active means the work is open)."""
    path = project.root / ".trace" / "work.toml"
    if not path.exists():
        return {}
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        key: str(entry.get("status", "active")) if isinstance(entry, dict) else "active"
        for key, entry in (data.get("work", {}) or {}).items()
    }


# trace:exempt reason=internal-helper
def _requirements_for(store: GraphStore, work_id: str) -> list[str]:
    """Requirement trace ids belonging to a work item (via declared edges)."""
    work = store.get_node(trace_id=work_id)
    if work is None:
        return []
    requirements: set[str] = set()
    for edge in store.edges_to(work.entity_uid):
        src = store.get_node(uid=edge.from_uid)
        if src is None:
            continue
        if src.node_type == "requirement":
            requirements.add(src.trace_id)
        elif src.node_type == "implementation":
            for sat in store.edges_from(src.entity_uid, "satisfies"):
                target = store.get_node(uid=sat.to_uid)
                if target is not None and target.node_type == "requirement":
                    requirements.add(target.trace_id)
    return sorted(requirements)


# trace:exempt reason=internal-helper
def _tokenize(text: str) -> list[str]:
    """Lowercased word tokens (stopwords dropped)."""
    STOP = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "with",
        "to",
        "it",
        "this",
        "that",
        "of",
        "in",
        "on",
        "is",
        "are",
        "was",
        "be",
        "make",
        "add",
        "fix",
        "change",
        "continue",
        "work",
        "also",
        "ignore",
        "using",
        "use",
        "so",
        "then",
        "now",
        "but",
        "not",
        "what",
        "when",
        "how",
    }
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


# trace:exempt reason=internal-helper
def _artifact_path(root: Path, default_rel: str, work_id: str, suffix: str = "spec") -> str:
    """Spec/plan path for a bootstrap: reuse the default only when it belongs
    to this work; otherwise namespace by the minted work id so a repeated
    bootstrap never overwrites a prior work's artifacts (Ambient §15)."""
    default = root / default_rel
    if not default.exists():
        return default_rel
    text = default.read_text(encoding="utf-8")
    if f"work={work_id}" in text:
        return default_rel
    stem = default.stem
    return f"{default.parent / (stem + '-' + work_id.replace('WORK-', 'work-'))}{default.suffix}"


def _toml_key(value: str) -> str:
    return json.dumps(value) if re.match(r"^[A-Za-z0-9._-]+$", value) is None else f'"{value}"'


# trace:exempt reason=internal-helper
def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))
