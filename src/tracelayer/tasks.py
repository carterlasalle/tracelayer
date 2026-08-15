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
def _mint_id(kind: str, title: str, store: GraphStore) -> str:
    """Deterministic stable ID: ``WORK-<slug>`` / ``REQ-<slug>`` / ``PLAN-<slug>``.

    Uniqueness: when the id already exists in the graph, append ``-2``,
    ``-3``... so repeated bootstrap calls never collide.
    """
    base = f"{kind}-{_slug(title)}"
    candidate = base
    counter = 2
    while store.get_node(trace_id=candidate) is not None:
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
    from tracelayer.engine import Engine

    engine = Engine(project)
    try:
        store = engine.store
        work_id = _mint_id("WORK", bundle.get("title") or "task", store)
        req_ids: dict[str, str] = {}
        for req in bundle.get("requirements", []) or []:
            title = str(req.get("title") or "Requirement")
            req_ids[title] = _mint_id("REQ", title, store)

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
            plan_id = _mint_id("PLAN", bundle.get("title") or "task", store)
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
    lifecycle: str = "wip",
) -> dict:
    """Task finalization (Ambient §32-33): run the completion gate; when it
    passes, mark the work item done and clear the session context."""
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
            "pending_obligations": len(pending),
            "diagnostics": [d.rule_id for d in diagnostics if d.severity == "ERROR"][:10],
        }
    if work:
        _set_work_status(project, work, "done")
    state.clear(session_id)
    receipts = _receipt_count(project, work)
    return {
        "status": "done",
        "work": work,
        "verify": "pass",
        "receipts": receipts,
    }


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
