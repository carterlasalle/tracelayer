"""TraceLayer CLI (contract §J): the ``trace`` entrypoint.

Typer-based command tree with a global ``--root/-C`` option (accepted both
before and after the subcommand), compact human output per spec 55, and
``--json`` where meaningful.  Exit codes follow spec 28.6 for ``verify``
(0 pass, 1 blocking, 2 config, 3 index unavailable, 4 evidence parse
failure when required) and 1 for any blocked hook decision.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import typer

from tracelayer.config import default_policy_toml, default_trace_toml
from tracelayer.diagnostics import SEVERITY_ERROR, Diagnostic
from tracelayer.engine import Engine
from tracelayer.graph.traverse import Subgraph
from tracelayer.install import AGENTS as INSTALL_AGENTS
from tracelayer.install import (
    append_agents_note,
    bundled_skill_dir,
    detect_agents,
    hook_config_for,
    hook_note,
    install_hook_assets,
    install_skill,
    merge_json_file,
    merge_mcp_json,
    skill_installed,
)
from tracelayer.mcp import run_mcp
from tracelayer.migration.codeops import MigrationItem, MigrationPlan
from tracelayer.protocol.schema import markdown_docs
from tracelayer.query.context import render_context_text

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="TraceLayer: agent-native software traceability",
)


# --------------------------------------------------------------------------
# Shared plumbing
# --------------------------------------------------------------------------


def _root_opt() -> Any:
    """Fresh per-command ``--root/-C`` option (default: resolved below)."""
    return typer.Option(None, "--root", "-C", help="Project root (default: current directory)")


@app.callback()
def _callback(
    ctx: typer.Context,
    root: Path = typer.Option(
        Path.cwd(), "--root", "-C", help="Project root (default: current directory)"
    ),
    debug: bool = typer.Option(
        False, "--debug", help="Emit structured performance diagnostics to stderr (spec 58)"
    ),
) -> None:
    """Global options; commands may also pass ``-C`` after the subcommand."""
    root = root.resolve()
    ctx.obj = {"root": root, "debug": debug}
    _maybe_hint_unconfigured(root)


def _maybe_hint_unconfigured(root: Path) -> None:
    """First-run guidance: printing install next steps when the repo isn't traced.

    `uv tool install` cannot run post-install scripts, so the message appears
    on the first command run outside a configured repository instead.
    Keyed to the resolved ``--root`` (what commands actually operate on),
    not the process cwd.
    """
    args = sys.argv[1:]
    if not args:
        return
    if os.environ.get("TRACE_NO_HINT"):
        return
    if any(a in ("init", "install", "--help", "-h", "--version") for a in args):
        return
    if (root / ".trace" / "trace.toml").exists():
        return
    typer.echo(
        "TraceLayer is not configured in this repository.\n"
        "  - trace init            enable traceability here\n"
        "  - trace install         install the skill + hooks into agent harnesses\n"
        "  - trace install --list  see detected agents",
        err=True,
    )


def main() -> None:
    """Console-script entrypoint (pyproject ``[project.scripts] trace``)."""
    app()


def _resolve_root(ctx: typer.Context, root: Path | None) -> Path:
    if root is None:
        return Path(ctx.obj["root"])
    return root.resolve()


def _emit_debug(ctx: typer.Context, payload: dict) -> None:
    """Print one JSON performance line to stderr when --debug is active."""
    if ctx.obj.get("debug"):
        typer.echo(json.dumps(payload, sort_keys=True), err=True)


def _open(root: Path) -> tuple[Engine, list[Diagnostic]]:
    """Open the engine; a corrupt/unavailable index exits 3 everywhere."""
    try:
        return Engine.open(root)
    except sqlite3.Error as exc:
        typer.echo(f"index unavailable: {exc}", err=True)
        raise typer.Exit(3) from exc


def _print_diag(d: Diagnostic) -> None:
    """Compact actionable line per spec 55."""
    loc = d.trace_id or d.path or ""
    if d.line is not None:
        loc = f"{loc}:{d.line}"
    suffix = f" ({loc})" if loc else ""
    typer.echo(f"{d.severity} {d.rule_id} - {d.message}{suffix}")


def _node_dict(n) -> dict[str, Any]:
    return {
        "trace_id": n.trace_id,
        "node_type": n.node_type,
        "title": n.title,
        "path": n.canonical_path,
        "symbol": n.symbol_qualified_name,
        "status": n.status(),
        "active": n.active,
    }


def _render_graph_tree(start_uid: str, sub: Subgraph) -> str:
    """Indented tree from the start node over outgoing edges (deterministic).

    Incoming edges of the start node are listed first (they are the
    dependents, e.g. tests/implementations of a requirement), then outgoing
    edges are walked recursively with a visited-set cycle guard.
    """
    labels = {uid: (n.trace_id if n.trace_id else uid) for uid, n in sub.nodes.items()}
    lines: list[str] = [labels[start_uid]]
    out_edges: dict[str, list] = {}
    for e in sub.edges:
        out_edges.setdefault(e.from_uid, []).append(e)
    for e in sorted(sub.edges, key=lambda e: (e.predicate, e.edge_uid)):
        if e.to_uid == start_uid:
            lines.append(f"  <- {e.predicate}: {labels.get(e.from_uid, e.from_uid)}")
    visited: set[str] = {start_uid}

    def walk(uid: str, depth: int) -> None:
        for e in sorted(out_edges.get(uid, []), key=lambda e: (e.predicate, e.edge_uid)):
            target = e.to_uid
            mark = " (visited)" if target in visited else ""
            lines.append("  " * (depth + 1) + f"{e.predicate}: {labels.get(target, target)}{mark}")
            if target not in visited:
                visited.add(target)
                walk(target, depth + 1)

    walk(start_uid, 0)
    return "\n".join(lines) + "\n"


def _render_mermaid(sub: Subgraph) -> str:
    lines = ["graph TD"]
    for e in sorted(sub.edges, key=lambda e: e.edge_uid):
        frm = sub.nodes.get(e.from_uid)
        to = sub.nodes.get(e.to_uid)
        fl = f'"{frm.trace_id}"' if frm else f'"{e.from_uid}"'
        tl = f'"{to.trace_id}"' if to else f'"{e.to_uid}"'
        lines.append(f"  {fl} -->|{e.predicate}| {tl}")
    return "\n".join(lines) + "\n"


def _render_dot(sub: Subgraph) -> str:
    lines = ["digraph trace {"]
    for e in sorted(sub.edges, key=lambda e: e.edge_uid):
        frm = sub.nodes.get(e.from_uid)
        to = sub.nodes.get(e.to_uid)
        fl = f'"{frm.trace_id}"' if frm else f'"{e.from_uid}"'
        tl = f'"{to.trace_id}"' if to else f'"{e.to_uid}"'
        lines.append(f'  {fl} -> {tl} [label="{e.predicate}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_jsonl(sub: Subgraph) -> str:
    """Newline-delimited JSON: one line per node, then one line per edge.

    Deterministic order: nodes sorted by trace_id; edges sorted by
    (from, predicate, to).
    """
    lines = []
    for uid, n in sorted(sub.nodes.items(), key=lambda kv: (kv[1].trace_id or "",)):
        lines.append(
            json.dumps(
                {
                    "uid": uid,
                    "trace_id": n.trace_id,
                    "node_type": n.node_type,
                    "status": n.status(),
                    "path": n.canonical_path,
                    "symbol": n.symbol_qualified_name,
                    "active": n.active,
                }
            )
        )
    for e in sorted(sub.edges, key=lambda e: (e.from_uid, e.predicate, e.to_uid)):
        lines.append(
            json.dumps(
                {
                    "from": e.from_uid,
                    "predicate": e.predicate,
                    "to": e.to_uid,
                    "source_kind": e.source_kind,
                }
            )
        )
    return "\n".join(lines) + "\n"


def _plan_to_json(plan: MigrationPlan) -> dict[str, Any]:
    return {
        "schema": plan.schema,
        "items": [
            {
                "path": item.path,
                "line": item.line,
                "classification": item.classification,
                "new_marker": item.new_marker,
                "note": item.note,
                "raw": item.raw,
            }
            for item in plan.items
        ],
        "summary": plan.summary,
    }


def _plan_from_json(data: dict[str, Any]) -> MigrationPlan:
    items = [
        MigrationItem(
            path=item["path"],
            line=item["line"],
            classification=item["classification"],
            new_marker=item.get("new_marker"),
            note=item.get("note", ""),
            raw=item.get("raw", ""),
        )
        for item in data.get("items", [])
    ]
    return MigrationPlan(
        schema=data.get("schema", "tracelayer-migration/v1"),
        items=items,
        summary=data.get("summary", {}),
    )


def _read_payload() -> dict[str, Any]:
    """Hook payload from stdin: {event, payload, session_id} envelope or raw."""
    data = sys.stdin.read()
    if not data.strip():
        return {}
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        payload = parsed.get("payload")
        if isinstance(payload, dict):
            merged = dict(payload)
            if "session_id" in parsed and "session_id" not in merged:
                merged["session_id"] = parsed["session_id"]
            return merged
        return parsed
    return {}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@app.command()
def init(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    observe: bool = typer.Option(False, "--observe", help="Initialize without a policy file"),
    skill: bool = typer.Option(
        True,
        "--skill/--no-skill",
        help="Install the skill (and hooks) for every detected agent, project scope",
    ),
    agents_note: bool = typer.Option(
        True,
        "--agents-note/--no-agents-note",
        help="Append the trace invariant to AGENTS.md/CLAUDE.md",
    ),
    mcp: bool = typer.Option(
        True,
        "--mcp/--no-mcp",
        help="Register the trace MCP server in .mcp.json (optional adapter, on by default)",
    ),
    all_features: bool = typer.Option(
        False,
        "--all",
        help="Everything is the default; kept for compatibility",
    ),
) -> None:
    """Bootstrap the repository (spec 28.1).

    Config + policy + gitignore + AGENTS.md/CLAUDE.md invariant, the skill
    (with hooks) for every detected agent, and the MCP server. Project scope
    only: all files land inside the repository. Use `trace install` for
    global (user-level) agent installs.
    """
    root = _resolve_root(ctx, root)
    _ = all_features  # compat: everything is the default
    written = _run_init(root, observe=observe, skill=skill, agents_note=agents_note, mcp=mcp)
    if written:
        for p in written:
            typer.echo(f"wrote {p.relative_to(root)}")
    else:
        typer.echo("nothing to do; files already present")


def _run_init(
    root: Path,
    *,
    observe: bool,
    skill: bool = True,
    agents_note: bool = True,
    mcp: bool = True,
) -> list[Path]:
    root = root.resolve()
    dot = root / ".trace"
    dot.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    cfg = dot / "trace.toml"
    if not cfg.exists():
        cfg.write_text(default_trace_toml(root.name), encoding="utf-8")
        written.append(cfg)
    pol = dot / "policy.toml"
    if not observe and not pol.exists():
        pol.write_text(default_policy_toml(), encoding="utf-8")
        written.append(pol)
    gi = root / ".gitignore"
    line = ".trace/cache/"
    content = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in content:
        content = (content.rstrip("\n") + "\n" if content.strip() else "") + line + "\n"
        gi.write_text(content, encoding="utf-8")
        written.append(gi)
    if agents_note:
        status, path = append_agents_note(root)
        if status == "appended" and path is not None:
            written.append(path)
    if skill:
        dst = root / ".agents" / "skills" / "traceability"
        if not dst.exists():
            shutil.copytree(bundled_skill_dir(), dst)
            written.append(dst)
        for name in sorted(detect_agents()):
            status, path = install_skill(name, root, link=False, force=False)
            if status == "installed":
                written.append(path)
            typer.echo(f"{name}: {status} -> {path}")
            hooks = hook_config_for(name, root)
            if hooks is not None:
                file, config = hooks
                hstatus, hpath = merge_json_file(file, config)
                typer.echo(f"  hooks: {hstatus} -> {hpath}")
                if hstatus != "already-installed":
                    written.append(hpath)
                continue
            rows = install_hook_assets(name, root, force=False)
            for hstatus, hpath in rows:
                typer.echo(f"  hooks: {hstatus} -> {hpath}")
                if hstatus != "already-installed":
                    written.append(hpath)
            note = hook_note(name)
            if note:
                typer.echo(f"  hooks note: {note}")
    if mcp:
        status, path = merge_mcp_json(root)
        if status == "installed":
            written.append(path)
    return written


@app.command()
def install(
    ctx: typer.Context,
    agent: list[str] | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Target agent(s), repeatable: claude-code, codex, pi, omp, hermes-agent, opencode, cursor, generic",
    ),
    global_install: bool = typer.Option(
        False, "--global", "-g", help="Install to user-wide agent dirs"
    ),
    link: bool = typer.Option(False, "--link", help="Symlink the skill instead of copying"),
    update: bool = typer.Option(
        False, "--update", help="Refresh existing skill and hook installs (force)"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip prompts; install to all detected agents"
    ),
    list_only: bool = typer.Option(
        False, "--list", "-l", help="List detected agents and install state"
    ),
) -> None:
    """Install the traceability skill (and hooks) into agent harnesses.

    Project scope (default) installs into the repository's agent directories;
    --global installs into ~/<agent>/skills. Skill directories follow the
    skills.sh agent-directory table so `npx skills add` and this command
    agree. Hooks install for every agent: JSON-merged settings for
    claude-code and codex, file-based hook configs for pi, omp, and opencode.
    After upgrading the tool, run `trace install --update` to refresh copies.
    """
    root = _resolve_root(ctx, None)
    if list_only:
        detected = set(detect_agents())
        for name in sorted(INSTALL_AGENTS):
            installed = skill_installed(name, None if global_install else root)
            marker = "*" if name in detected else " "
            typer.echo(f"{marker} {name:<14} {'installed' if installed else 'not installed'}")
        return
    targets = list(agent) if agent else []
    if not targets:
        targets = detect_agents()
        if not targets:
            typer.echo(
                "no agents detected; pass --agent explicitly "
                f"({', '.join(sorted(INSTALL_AGENTS))})",
                err=True,
            )
            raise typer.Exit(2)
        if not yes and len(targets) > 1:
            keep: list[str] = []
            for name in targets:
                if typer.confirm(f"Install into {name}?"):
                    keep.append(name)
            targets = keep
    for name in targets:
        if name not in INSTALL_AGENTS:
            typer.echo(
                f"unknown agent {name!r}; valid: {', '.join(sorted(INSTALL_AGENTS))}", err=True
            )
            raise typer.Exit(2)
    _install_agents(root, targets, global_install=global_install, link=link, update=update)


# trace:v1 id=impl.cli.install-agents work=WORK-TL-001
def _install_agents(
    root: Path,
    targets: list[str],
    *,
    global_install: bool,
    link: bool = False,
    update: bool = False,
) -> None:
    """Install/refresh skill + hooks for one or more agents (shared loop)."""
    for name in targets:
        status, path = install_skill(
            name, None if global_install else root, link=link, force=update
        )
        typer.echo(f"{name}: {status} -> {path}")
        hooks = hook_config_for(name, None if global_install else root)
        if hooks is not None:
            file, config = hooks
            hstatus, hpath = merge_json_file(file, config)
            typer.echo(f"  hooks: {hstatus} -> {hpath}")
            continue
        rows = install_hook_assets(name, None if global_install else root, force=update)
        for hstatus, hpath in rows:
            typer.echo(f"  hooks: {hstatus} -> {hpath}")
        note = hook_note(name)
        if note:
            typer.echo(f"  hooks note: {note}")
    if not global_install:
        status, path = append_agents_note(root)
        if status == "appended" and path is not None:
            typer.echo(f"invariant: appended -> {path}")
        mstatus, mpath = merge_mcp_json(root)
        typer.echo(f"mcp: {mstatus} -> {mpath}")


@app.command()
def update(
    ctx: typer.Context,
    global_install: bool = typer.Option(
        False, "--global", "-g", help="Refresh global (user-level) copies instead of project scope"
    ),
) -> None:
    """Refresh skill copies and hooks for every detected agent.

    Run after upgrading the tool: propagates updated skill/hook content
    into existing installs (project scope by default).
    """
    root = _resolve_root(ctx, None)
    targets = sorted(detect_agents())
    if not targets:
        typer.echo(
            "no agents detected; run `trace install --list` or pass "
            f"--agent ({', '.join(sorted(INSTALL_AGENTS))}) via `trace install`",
            err=True,
        )
        raise typer.Exit(2)
    _install_agents(root, targets, global_install=global_install, update=True)


marker_app = typer.Typer(no_args_is_help=True, help="Marker authoring helpers")
app.add_typer(marker_app, name="marker")


# trace:v1 id=impl.cli.marker-suggest work=WORK-TL-001
@marker_app.command("suggest")
def marker_suggest(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="path or path:line of the boundary"),
    root: Path | None = _root_opt(),
    session: str | None = typer.Option(
        None, "--session", help="Session id (default: $TRACE_SESSION or 'default')"
    ),
) -> None:
    """Suggest the canonical trace marker for a behavioral boundary.

    Resolves the boundary at ``path[:line]`` and prints the exact
    ``# trace:v1`` line with the session's active work/requirement.
    """
    from tracelayer.config import load_project
    from tracelayer.discovery.boundaries import extract_boundaries
    from tracelayer.hooks.session_state import SessionState

    root = _resolve_root(ctx, root)
    path_part, _, line_part = target.partition(":")
    line = int(line_part) if line_part.isdigit() else None
    rel = os.path.relpath(path_part, root) if os.path.isabs(path_part) else path_part
    file_path = root / rel
    if not file_path.is_file():
        typer.echo(f"no such file: {rel}", err=True)
        raise typer.Exit(2)
    text = file_path.read_text(encoding="utf-8")
    boundaries = extract_boundaries(rel, text)
    boundary = None
    if line is not None:
        boundary = next((b for b in boundaries if b.start_line <= line <= b.end_line), None)
    if boundary is None and boundaries:
        boundary = next((b for b in boundaries if b.start_line >= (line or 1)), boundaries[0])
    if boundary is None:
        typer.echo(f"no behavioral boundary found in {rel}", err=True)
        raise typer.Exit(2)
    project, _diags = load_project(root)
    state = SessionState(project)
    sid = session or os.environ.get("TRACE_SESSION") or "default"
    work = state.active_work(sid)
    req = state.active_requirement(sid)
    plan = state.active_plan(sid)
    work_attr = f" work={work}" if work else ""
    req_attr = f" satisfies={req}" if req else ""
    plan_attr = f" implements={plan}" if plan else ""
    marker_line = f"# trace:v1 id=impl.{boundary.name}{work_attr}{req_attr}{plan_attr}"
    typer.echo(f"boundary: {rel}:{boundary.start_line}::{boundary.name} ({boundary.kind})")
    if not (work or req):
        typer.echo(
            "note: no active work/requirement in this session; run "
            "`trace task begin <WORK-ID>` to attach causal context"
        )
    typer.echo(marker_line)


plan_app = typer.Typer(no_args_is_help=True, help="Plan discipline helpers")
app.add_typer(plan_app, name="plan")


# trace:v1 id=impl.cli.plan-status work=WORK-TL-001
@plan_app.command("status")
def plan_status(
    ctx: typer.Context,
    plan_id: str = typer.Argument(..., help="PLAN-... id"),
    root: Path | None = _root_opt(),
) -> None:
    """Show a plan's expected obligations and whether they are met.

    A plan marker may declare ``expects=<id>,<id>``; every expected
    artifact must exist as an active node linked by an ``implements`` edge
    back to the plan (the TL014 gate).
    """
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        node = engine.store.get_node(trace_id=plan_id)
        if node is None or not node.active:
            typer.echo(f"no active plan node: {plan_id}", err=True)
            raise typer.Exit(2)
        expected = node.metadata.get("expects") or []
        implemented = {
            e.from_uid
            for e in engine.store.edges_to(node.entity_uid)
            if e.status == "active" and e.predicate == "implements"
        }
        typer.echo(f"plan: {plan_id}  ({node.title or 'no title'})")
        if not expected:
            typer.echo("expected artifacts: none declared (add `expects=` to the plan marker)")
            return
        failed = 0
        for expected_id in expected:
            target = engine.store.get_node(trace_id=expected_id)
            ok = target is not None and target.active and target.entity_uid in implemented
            status = "ok" if ok else "MISSING"
            failed += 0 if ok else 1
            typer.echo(f"  {expected_id}: {status}")
        if failed:
            typer.echo(f"trace verify will BLOCK (TL014) with {failed} unmet obligation(s)")
            raise typer.Exit(1)
        typer.echo("all expected artifacts present and linked to the plan")
    finally:
        engine.close()


# trace:v1 id=impl.cli.task-context work=WORK-TL-001
@app.command()
def task(
    ctx: typer.Context,
    command: str = typer.Argument(..., help="begin | end"),
    work_id: str | None = typer.Argument(None, help="WORK-... id (for begin)"),
    requirement: str | None = typer.Option(
        None, "--requirement", "-r", help="Active requirement (REQ-...) for the task"
    ),
    plan: str | None = typer.Option(
        None, "--plan", "-p", help="Active plan (PLAN-...) the task implements"
    ),
    session: str | None = typer.Option(
        None, "--session", help="Session id (default: $TRACE_SESSION or 'default')"
    ),
    root: Path | None = _root_opt(),
) -> None:
    """Manage the session's active trace context (review P1).

    ``trace task begin WORK-X`` records the active work item for the
    session; hooks then attach new behavior to it automatically (markers
    suggested with ``work=WORK-X``), and the pre-edit authoring gate
    demands causal context before untraced new behavior is written.
    """
    from tracelayer.config import load_project
    from tracelayer.hooks.session_state import SessionState

    root = _resolve_root(ctx, root)
    project, _diags = load_project(root)
    state = SessionState(project)
    sid = session or os.environ.get("TRACE_SESSION") or "default"
    if command == "begin":
        if not work_id:
            typer.echo(
                "usage: trace task begin <WORK-ID> [--requirement REQ-X] [--plan PLAN-X]", err=True
            )
            raise typer.Exit(2)
        state.set_active_work(sid, work_id)
        if requirement:
            state.set_active_requirement(sid, requirement)
        if plan:
            state.set_active_plan(sid, plan)
        lines = [f"active work: {work_id}"]
        if requirement:
            lines.append(f"active requirement: {requirement}")
        if plan:
            lines.append(f"active plan: {plan}")
        lines.append("Hooks will attach new behavior to this work item automatically.")
        typer.echo("\n".join(lines))
        return
    if command == "end":
        state.clear(sid)
        typer.echo("session trace context cleared")
        return
    typer.echo(f"unknown task command {command!r} (begin|end)", err=True)
    raise typer.Exit(2)


# trace:v1 id=impl.cli.task-summary work=WORK-TL-001
@app.command()
def summary(
    ctx: typer.Context,
    session: str | None = typer.Option(
        None, "--session", help="Session id (default: $TRACE_SESSION or 'default')"
    ),
    root: Path | None = _root_opt(),
) -> None:
    """Task trace summary: session context + obligations + verification state."""
    from tracelayer.config import load_project
    from tracelayer.hooks.session_state import SessionState

    root = _resolve_root(ctx, root)
    project, _diags = load_project(root)
    state = SessionState(project)
    sid = session or os.environ.get("TRACE_SESSION") or "default"
    work = state.active_work(sid)
    req = state.active_requirement(sid)
    plan = state.active_plan(sid)
    obligations = state._read(sid).get("obligations", [])
    pending = [o for o in obligations if o.get("state") != "satisfied"]
    satisfied = [o for o in obligations if o.get("state") == "satisfied"]
    engine, _diags2 = _open(root)
    try:
        work_node = engine.store.get_node(trace_id=work) if work else None
    finally:
        engine.close()
    lines = ["TASK TRACE SUMMARY", ""]
    lines.append(
        f"work:        {work or '(none)'}"
        + (f"  [{work_node.title if work_node else 'not in graph'}]" if work else "")
    )
    lines.append(f"requirement: {req or '(none)'}")
    lines.append(f"plan:        {plan or '(none)'}")
    lines.append("")
    if obligations:
        lines.append(f"obligations: {len(satisfied)} satisfied, {len(pending)} pending")
        for obl in pending[:5]:
            marker = str(obl.get("suggested_marker", "")).strip()
            lines.append(f"  PENDING {obl.get('path')}::{obl.get('symbol')}")
            if marker:
                lines.append(f"    add: {marker}")
    else:
        lines.append("obligations: none recorded this session")
    if not pending:
        lines.append("")
        lines.append("no pending trace obligations")
    typer.echo("\n".join(lines))


@app.command()
def web(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Bind address (localhost only by default)"
    ),
    port: int = typer.Option(8765, "--port", help="Port for the web UI"),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the browser automatically"
    ),
) -> None:
    """Serve the trace graph as a local web UI with 3D visualization.

    Spawns a stdlib-only HTTP server on localhost. The graph is **markers
    only**: nodes from trace markers + work items, and only declared
    semantic edges (satisfies/verifies/exercises/work/...), never
    structural derivations. Ctrl-C to stop.
    """
    from tracelayer.web import run_web

    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        run_web(engine, host=host, port=port, open_browser=open_browser)
    except OSError as exc:
        typer.echo(f"cannot bind {host}:{port} ({exc}); try --port <other>", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()


@app.command()
def mcp(ctx: typer.Context, root: Path | None = _root_opt()) -> None:
    """Run the MCP stdio server (optional adapter).

    Exposes status/search/context/why/impact/verify/index as MCP tools for
    any MCP-capable agent. Deterministic and local; never required — the
    skill + CLI + hooks are the canonical interface (spec Phase 10).
    """
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        sys.exit(run_mcp(engine))
    except KeyboardInterrupt:
        sys.exit(0)


@app.command()
def index(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    all_: bool = typer.Option(False, "--all", help="Full index"),
    changed: bool = typer.Option(False, "--changed", help="Incremental index of changed files"),
    clean: bool = typer.Option(False, "--clean", help="Full rebuild of the materialized index"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Index the repository (spec 18.1/18.2)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if changed and not all_:
            report = engine.index_changed()
        else:
            report = engine.index_all(clean=clean)
    finally:
        engine.close()
    _emit_debug(
        ctx,
        {
            "command": "index",
            "changed": bool(changed and not all_),
            "nodes": report.nodes,
            "edges": report.edges,
            "markers": report.markers,
            "changed_files": report.changed_files,
            "duration_ms": report.duration_ms,
            "per_stage": report.per_stage,
        },
    )
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "schema": "tracelayer-index/v1",
                    "nodes": report.nodes,
                    "edges": report.edges,
                    "markers": report.markers,
                    "diagnostics": report.diagnostics,
                    "changed_files": report.changed_files,
                    "duration_ms": report.duration_ms,
                    "per_stage": report.per_stage,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        typer.echo(
            f"Indexed {report.nodes} nodes, {report.edges} edges, "
            f"{report.markers} markers, {report.diagnostics} diagnostics "
            f"in {report.duration_ms} ms ({report.changed_files} files)"
        )


@app.command()
def status(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
    strict_health: bool = typer.Option(False, "--strict-health", help="Exit 1 when warnings exist"),
) -> None:
    """Trace health summary (spec 28.2)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        report = engine.status()
    finally:
        engine.close()
    if json_out:
        typer.echo(json.dumps(_status_dict(report), indent=2, sort_keys=True))
    else:
        typer.echo("Trace health")
        typer.echo("------------")
        rows = [
            ("Nodes", report.nodes),
            ("Declared edges", report.declared_edges),
            ("Structural edges", report.structural_edges),
            ("Current evidence runs", report.evidence_runs),
            ("Broken refs", report.broken_refs),
            ("Blocking stale traces", report.blocking_stale),
            ("Warnings", report.warnings),
            ("Changed traced artifacts", report.changed_artifacts),
        ]
        for label, value in rows:
            typer.echo(f"{label + ':':<26}{value:>8}")
        typer.echo(f"Policy: {report.policy} / lifecycle={report.lifecycle}")
    if strict_health and report.warnings > 0:
        raise typer.Exit(1)


def _status_dict(report) -> dict[str, Any]:
    return {
        "nodes": report.nodes,
        "declared_edges": report.declared_edges,
        "structural_edges": report.structural_edges,
        "evidence_runs": report.evidence_runs,
        "broken_refs": report.broken_refs,
        "blocking_stale": report.blocking_stale,
        "warnings": report.warnings,
        "changed_artifacts": report.changed_artifacts,
        "policy": report.policy,
        "lifecycle": report.lifecycle,
    }


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search text"),
    root: Path | None = _root_opt(),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
    limit: int = typer.Option(20, "--limit", help="Maximum results"),
) -> None:
    """Search trace ids, titles, symbols, summaries (FTS5)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        nodes = engine.search(query, limit=limit)
    finally:
        engine.close()
    if json_out:
        typer.echo(json.dumps([_node_dict(n) for n in nodes], indent=2, sort_keys=True))
    else:
        for n in nodes:
            label = n.title or n.symbol_qualified_name or n.canonical_path or ""
            typer.echo(f"{n.trace_id}  {label}")


@app.command()
def context(
    ctx: typer.Context,
    trace_id: str = typer.Argument(..., help="Trace id"),
    root: Path | None = _root_opt(),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Context summary for one node (spec 28.3); records context-load."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        result = engine.context(trace_id)
        if result is None:
            typer.echo(f"unknown trace id: {trace_id}", err=True)
            raise typer.Exit(1)
        from tracelayer.hooks.session_state import SessionState

        state = SessionState(engine.project)
        state.record_context_load(state.session_id_from({}), trace_id)
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "trace_id": result.node.trace_id,
                        "node_type": result.node.node_type,
                        "title": result.node.title,
                        "path": result.node.canonical_path,
                        "symbol": result.node.symbol_qualified_name,
                        "status": result.staleness,
                        "upstream": [
                            {"predicate": e.predicate, "trace_id": n.trace_id}
                            for e, n in result.upstream
                        ],
                        "downstream": [
                            {"predicate": e.predicate, "trace_id": n.trace_id}
                            for e, n in result.downstream
                        ],
                        "verification": [
                            {
                                "test_trace_id": v.test_trace_id,
                                "outcome": v.outcome,
                                "proof_level": v.proof_level,
                                "current": v.current,
                            }
                            for v in result.verification
                        ],
                        "staleness": result.staleness,
                        "provenance": result.provenance,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            typer.echo(render_context_text(result), nl=False)
    finally:
        engine.close()


@app.command()
def why(
    ctx: typer.Context,
    trace_id: str = typer.Argument(..., help="Trace id"),
    root: Path | None = _root_opt(),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Causal paths explaining why a node exists (spec 28.4)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if engine.store.get_node(trace_id=trace_id) is None:
            typer.echo(f"unknown trace id: {trace_id}", err=True)
            raise typer.Exit(1)
        paths = engine.why(trace_id)
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "trace_id": trace_id,
                        "paths": [[hop[1].trace_id for hop in path] + [trace_id] for path in paths],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if not paths:
                typer.echo(f"no causal path found for {trace_id}")
            for path in paths:
                chain = [hop[1].trace_id for hop in path] + [trace_id]
                typer.echo(" -> ".join(chain))
    finally:
        engine.close()


@app.command()
def impact(
    ctx: typer.Context,
    trace_id: str = typer.Argument(..., help="Trace id"),
    root: Path | None = _root_opt(),
    semantic_only: bool = typer.Option(False, "--semantic-only", help="Declared edges only"),
    include_structural: bool = typer.Option(
        False, "--include-structural", help="Include structural downstream"
    ),
    include_tests: bool = typer.Option(True, "--include-tests/--no-tests", help="Include tests"),
    include_history: bool = typer.Option(False, "--include-history", help="Include commit history"),
    depth: int = typer.Option(3, "--depth", help="Traversal depth"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Impact analysis: what depends on this node (spec 28.5)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if engine.store.get_node(trace_id=trace_id) is None:
            typer.echo(f"unknown trace id: {trace_id}", err=True)
            raise typer.Exit(1)
        result = engine.impact(
            trace_id,
            semantic_only=semantic_only,
            include_structural=include_structural,
            include_tests=include_tests,
            include_history=include_history,
            depth=depth,
        )
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "trace_id": trace_id,
                        "semantic": [n.trace_id for n in result.semantic],
                        "structural": [n.trace_id for n in result.structural],
                        "tests": [n.trace_id for n in result.tests],
                        "stale": [{"trace_id": n.trace_id, "status": s} for n, s in result.stale],
                        "history": [
                            {"sha": c.sha, "author": c.author, "date": c.date, "summary": c.summary}
                            for c in result.history
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if result.semantic:
                typer.echo("Semantic downstream:")
                for n in result.semantic[:20]:
                    typer.echo(f"  {n.trace_id} ({n.status()})")
            if result.structural:
                typer.echo("Structural downstream:")
                for n in result.structural[:20]:
                    typer.echo(f"  {n.trace_id}")
            if result.tests:
                typer.echo("Tests:")
                for n in result.tests[:20]:
                    typer.echo(f"  {n.trace_id}")
            if result.stale:
                typer.echo("Stale:")
                for n, s in result.stale[:20]:
                    typer.echo(f"  {n.trace_id} ({s})")
            if result.history:
                typer.echo(f"History: {len(result.history)} commits")
    finally:
        engine.close()


@app.command()
def graph(
    ctx: typer.Context,
    trace_id: str = typer.Argument(..., help="Trace id"),
    root: Path | None = _root_opt(),
    depth: int = typer.Option(2, "--depth", help="Traversal depth"),
    fmt: str = typer.Option("tree", "--format", help="tree|mermaid|dot|json|jsonl"),
) -> None:
    """Subgraph around a node (spec 28.8)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        start = engine.store.get_node(trace_id=trace_id)
        if start is None:
            typer.echo(f"unknown trace id: {trace_id}", err=True)
            raise typer.Exit(1)
        sub = engine.subgraph(trace_id, depth=depth)
        if fmt == "json":
            typer.echo(
                json.dumps(
                    {
                        "trace_id": trace_id,
                        "nodes": {uid: _node_dict(n) for uid, n in sub.nodes.items()},
                        "edges": [
                            {
                                "from": e.from_uid,
                                "predicate": e.predicate,
                                "to": e.to_uid,
                                "source_kind": e.source_kind,
                            }
                            for e in sub.edges
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif fmt == "mermaid":
            typer.echo(_render_mermaid(sub), nl=False)
        elif fmt == "dot":
            typer.echo(_render_dot(sub), nl=False)
        elif fmt == "jsonl":
            typer.echo(_render_jsonl(sub), nl=False)
        elif fmt == "tree":
            typer.echo(_render_graph_tree(start.entity_uid, sub), nl=False)
        else:
            raise typer.BadParameter("--format must be tree|mermaid|dot|json|jsonl")
    finally:
        engine.close()


@app.command()
def new(
    ctx: typer.Context,
    node_type: str = typer.Argument(..., help="Artifact type"),
    name: str = typer.Option(..., "--name", help="Human name to derive the id from"),
    root: Path | None = _root_opt(),
) -> None:
    """Generate a fresh stable trace id (FR-020)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        tid = engine.new_id(node_type, name)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.close()
    typer.echo(tid)


@app.command()
def verify(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    changed: bool = typer.Option(False, "--changed", help="Verify changed scope"),
    all_: bool = typer.Option(False, "--all", help="Verify the whole repository"),
    lifecycle: str | None = typer.Option(
        None, "--lifecycle", help="Lifecycle (draft|wip|review|merge|release)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
    require_evidence: bool = typer.Option(
        False, "--require-evidence", help="Force evidence-dependent rules"
    ),
) -> None:
    """Policy verification (spec 28.6). Exit: 0 pass, 1 blocking, 2 config,
    3 index unavailable, 4 evidence parse failure when required."""
    root = _resolve_root(ctx, root)
    try:
        engine, diags = Engine.open(root)
    except sqlite3.Error as exc:
        typer.echo(f"index unavailable: {exc}", err=True)
        raise typer.Exit(3) from exc
    cfg_errors = [d for d in diags if d.severity == SEVERITY_ERROR and d.rule_id == "TL100"]
    if cfg_errors:
        for d in cfg_errors:
            _print_diag(d)
        raise typer.Exit(2)
    try:
        result = engine.verify(
            scope="all" if all_ else "changed",
            lifecycle=lifecycle,
            require_evidence=require_evidence,
        )
        evidence_fail = require_evidence and bool(engine.store.get_diagnostics(rule_id="TL051"))
    except sqlite3.Error as exc:
        typer.echo(f"index unavailable: {exc}", err=True)
        raise typer.Exit(3) from exc
    finally:
        engine.close()
    _emit_debug(
        ctx,
        {
            "command": "verify",
            "scope": "all" if all_ else "changed",
            "lifecycle": result.lifecycle,
            "policy": result.policy,
            "status": result.status,
            "diagnostics": len(result.diagnostics),
        },
    )
    if evidence_fail:
        typer.echo("verify: evidence parser failure (TL051); fix the evidence file", err=True)
        raise typer.Exit(4)
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "schema": "tracelayer-verify/v1",
                    "status": result.status,
                    "policy": result.policy,
                    "lifecycle": result.lifecycle,
                    "diagnostics": [d.to_json() for d in result.diagnostics],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for d in result.diagnostics:
            _print_diag(d)
        if result.blocking:
            count = sum(1 for d in result.diagnostics if d.severity == SEVERITY_ERROR)
            typer.echo(
                f"verify: FAIL ({count} blocking) lifecycle={result.lifecycle} "
                f"policy={result.policy}"
            )
        else:
            typer.echo(
                f"verify: pass ({len(result.diagnostics)} diagnostics) "
                f"lifecycle={result.lifecycle} policy={result.policy}"
            )
    raise typer.Exit(result.exit_code())


@app.command()
def doctor(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    fix: bool = typer.Option(False, "--fix", help="Apply deterministic cosmetic fixes"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Re-detect trace issues; --fix applies cosmetic fixes only (spec 28.7)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        diags, report = engine.doctor(fix=fix)
    finally:
        engine.close()
    blocking = sum(1 for d in diags if d.severity == SEVERITY_ERROR)
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "diagnostics": [d.to_json() for d in diags],
                    "fix_report": report,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for d in diags:
            _print_diag(d)
        typer.echo(f"doctor: {len(diags)} issues ({blocking} blocking)")
        if report is not None:
            typer.echo(f"fixes applied: {report.get('total_fixed', 0)}")


@app.command()
def review(
    ctx: typer.Context,
    trace_id: str = typer.Argument(..., help="Trace id"),
    root: Path | None = _root_opt(),
) -> None:
    """Review a stale node: STALE_REVIEW_REQUIRED -> REVIEWED_NEEDS_VERIFICATION."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        ok = engine.review(trace_id)
    finally:
        engine.close()
    if not ok:
        typer.echo(f"unknown trace id: {trace_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"reviewed {trace_id}: status -> REVIEWED_NEEDS_VERIFICATION")


# --------------------------------------------------------------------------
# Nested groups: evidence, report, migrate, audit, docs, hook
# --------------------------------------------------------------------------

evidence_app = typer.Typer(no_args_is_help=True, help="Evidence ingestion")
app.add_typer(evidence_app, name="evidence")


@evidence_app.command("ingest")
def evidence_ingest(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    junit: Path | None = typer.Option(None, "--junit", help="JUnit XML report"),
    coverage: Path | None = typer.Option(None, "--coverage", help="Cobertura XML coverage"),
    normalized: Path | None = typer.Option(
        None, "--normalized", help="tracelayer-evidence/v1 JSON"
    ),
    run_id: str | None = typer.Option(None, "--run-id", help="Evidence run id"),
    revision: str | None = typer.Option(None, "--revision", help="Git revision bound to the run"),
    provider: str | None = typer.Option(None, "--provider", help="Evidence provider"),
    workflow: str | None = typer.Option(None, "--workflow", help="CI workflow name"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Ingest evidence; exit 4 when a required evidence file fails to parse."""
    root = _resolve_root(ctx, root)
    if junit is None and coverage is None and normalized is None:
        raise typer.BadParameter("at least one of --junit/--coverage/--normalized is required")
    engine, _diags = _open(root)
    try:
        result = engine.ingest_evidence(
            junit=junit,
            coverage=coverage,
            normalized=normalized,
            run_id=run_id,
            revision=revision,
            provider=provider,
            workflow=workflow,
        )
    finally:
        engine.close()
    _emit_debug(
        ctx,
        {
            "command": "evidence-ingest",
            "run_id": result.run_id,
            "tests_ingested": result.tests_ingested,
            "executions_ingested": result.executions_ingested,
        },
    )
    for d in result.diagnostics:
        _print_diag(d)
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "tests_ingested": result.tests_ingested,
                    "executions_ingested": result.executions_ingested,
                    "diagnostics": [d.to_json() for d in result.diagnostics],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        typer.echo(
            f"Ingested run {result.run_id}: {result.tests_ingested} tests, "
            f"{result.executions_ingested} execution edges"
        )
    if any(d.rule_id == "TL051" for d in result.diagnostics):
        raise typer.Exit(4)


report_app = typer.Typer(no_args_is_help=True, help="Reports")
app.add_typer(report_app, name="report")


@report_app.command("pr")
def report_pr(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    output: Path | None = typer.Option(None, "--output", help="Write to file instead of stdout"),
) -> None:
    """Generated PR impact summary (spec Section 27)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        text = engine.pr_summary()
    finally:
        engine.close()
    if output is not None:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text, nl=False)


migrate_app = typer.Typer(no_args_is_help=True, help="Migration tooling")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("codeops")
def migrate_codeops(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    scan: bool = typer.Option(False, "--scan", help="Scan and classify codeops markers"),
    plan: Path | None = typer.Option(None, "--plan", help="Write the migration plan JSON to PATH"),
    apply: Path | None = typer.Option(
        None, "--apply", help="Apply a migration plan JSON from PATH"
    ),
) -> None:
    """CodeOps migration: scan / plan / apply (spec Section 33)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if apply is not None:
            data = json.loads(apply.read_text(encoding="utf-8"))
            mplan = _plan_from_json(data)
            report = engine.migration_apply(mplan, dry_run=False)
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
        elif plan is not None:
            mplan = engine.migration_plan()
            plan.write_text(
                json.dumps(_plan_to_json(mplan), indent=2, sort_keys=True), encoding="utf-8"
            )
            typer.echo(f"wrote plan to {plan}")
        elif scan:
            markers, diags = engine.migration_scan()
            mplan = engine.migration_plan()
            for d in diags:
                _print_diag(d)
            summary = ", ".join(f"{k}={v}" for k, v in sorted(mplan.summary.items()))
            typer.echo(f"codeops: {len(markers)} markers; plan summary: {summary}")
            for item in mplan.items:
                flag = "X" if item.classification in ("deterministic", "high_confidence") else " "
                typer.echo(f"  [{flag}] {item.path}:{item.line} {item.classification}: {item.note}")
        else:
            typer.echo("specify --scan, --plan PATH, or --apply PATH", err=True)
            raise typer.Exit(2)
    finally:
        engine.close()


@migrate_app.command("scry")
def migrate_scry(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
) -> None:
    """Detect scry annotations for manual migration review (v1 detection only)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        records, diags = engine.migration_scry()
    finally:
        engine.close()
    for d in diags:
        _print_diag(d)
    for r in records:
        typer.echo(f"{r['path']}:{r['line']} scry:{r['kind']}")
    typer.echo(f"scry: {len(records)} annotations detected (detection only)")


audit_app = typer.Typer(no_args_is_help=True, help="Independent semantic audit")
app.add_typer(audit_app, name="audit")


@audit_app.command("package")
def audit_package(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    work: str | None = typer.Option(None, "--work", help="Work item trace id"),
    changed: bool = typer.Option(False, "--changed", help="Scope to changed nodes"),
    output: Path | None = typer.Option(None, "--output", help="Write the package JSON to PATH"),
) -> None:
    """Build the bounded auditor input package (spec 30.2)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        pkg = engine.audit_package(work_id=work)
    finally:
        engine.close()
    data = json.dumps(pkg, indent=2, sort_keys=True)
    if output is not None:
        output.write_text(data, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(data)


@audit_app.command("run")
def audit_run(
    ctx: typer.Context,
    command: str = typer.Option(..., "--command", help="Auditor command (argv-split)"),
    root: Path | None = _root_opt(),
    package: Path | None = typer.Option(None, "--package", help="Package JSON (else built fresh)"),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds"),
) -> None:
    """Run the independent semantic auditor (spec 30.3)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if package is not None:
            json.loads(package.read_text(encoding="utf-8"))  # validate package input
        else:
            engine.audit_package()
        result, diags = engine.run_auditor(command=command, timeout=timeout)
    finally:
        engine.close()
    for d in diags:
        _print_diag(d)
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if any(d.severity == SEVERITY_ERROR for d in diags):
        raise typer.Exit(1)


docs_app = typer.Typer(no_args_is_help=True, help="Generated protocol documentation")
app.add_typer(docs_app, name="docs")


@docs_app.command("generate")
def docs_generate(
    ctx: typer.Context,
    root: Path | None = _root_opt(),
    check: bool = typer.Option(False, "--check", help="Diff generated docs; exit 1 when stale"),
) -> None:
    """Write the three generated protocol docs from the registries (56.1)."""
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        if check:
            up_to_date = engine.docs_generate(check=True)
            if up_to_date:
                typer.echo("docs up to date")
                return
            stale = [
                rel
                for rel, content in markdown_docs().items()
                if not (root / rel).exists() or (root / rel).read_text(encoding="utf-8") != content
            ]
            typer.echo(
                "docs out of date: " + ", ".join(stale) + " (run `trace docs generate`)",
                err=True,
            )
            raise typer.Exit(1)
        engine.docs_generate(check=False)
        typer.echo("generated " + ", ".join(sorted(markdown_docs())))
    finally:
        engine.close()


def _normalize_hook_payload(raw: dict) -> dict:
    """Map harness-specific hook payloads onto the canonical hook shape.

    Claude Code sends ``{"tool_name": "Edit", "tool_input": {"file_path":
    ..., "old_string": ..., "new_string": ...}}``; other harnesses use
    ``file_path`` or top-level ``path``. Everything is normalized to
    ``path`` plus the mutation text (``content`` / ``old_string`` +
    ``new_string``) so hook handlers see one contract (adapter drift fix).
    """
    out = dict(raw)
    ti = raw.get("tool_input")
    if not isinstance(ti, dict):
        return out
    if not out.get("path"):
        out["path"] = ti.get("file_path") or ti.get("path") or ""
    for key in ("content", "old_string", "new_string", "line"):
        if key in ti and key not in out:
            out[key] = ti[key]
    return out


@app.command()
def hook(
    ctx: typer.Context,
    event: str = typer.Argument(
        ..., help="session-start|prompt-context|pre-mutation|post-mutation|post-batch|stop"
    ),
    root: Path | None = _root_opt(),
    fmt: str = typer.Option("text", "--format", help="text|claude|json"),
) -> None:
    """Run a hook event with the payload JSON from stdin (spec Section 22).

    Exit 0 on allow, 2 on block (Claude Code blocks on exit 2; exit 1 is a
    non-blocking error there).
    """
    root = _resolve_root(ctx, root)
    engine, _diags = _open(root)
    try:
        out = engine.hook(event, _normalize_hook_payload(_read_payload()))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    finally:
        engine.close()
    typer.echo(out.render(fmt))
    if out.decision == "block":
        raise typer.Exit(2)


if __name__ == "__main__":
    main()
