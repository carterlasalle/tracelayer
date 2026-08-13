"""Independent semantic auditor execution and integration (spec Section 30)."""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

from tracelayer.audit.schema import AUDIT_RESULT_SCHEMA, audit_result_schema
from tracelayer.diagnostics import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    Diagnostic,
    make,
)

# Hard caps keep auditor interaction bounded (Threat T9).
MAX_PROMPT_CHARS = 16_000
MAX_OUTPUT_CHARS = 1_000_000
MAX_STDERR_CHARS = 500

_RESULT_STATUSES = frozenset({"pass", "fail", "uncertain"})
_FINDING_SEVERITIES = frozenset({"high", "medium", "low"})

_AUDITOR_INSTRUCTIONS = """You are an independent semantic auditor for a software traceability graph.

Your job is to challenge meaning, not repeat deterministic checks. File
existence, syntax, ID resolution, and test-result parsing are already handled
deterministically by the engine. Answer questions such as:
- Does the implementation plausibly satisfy the requirement text?
- Does the test actually assert the important behavior rather than trivially pass?
- Does the plan omit a meaningful impacted component?
- Is an unexpected infra/config change justified by the traced work?
- Do the linked ADR and requirement contradict each other?
- Does the evidence support the claim the agent is making?
- Is a trace relationship semantically wrong even though both IDs resolve?

You may make the overall gate stricter, but you cannot declare a broken
reference valid or fabricate missing evidence.

Respond with ONLY a JSON object conforming to the schema shown below."""


def build_auditor_prompt(package: dict) -> str:
    """Bounded prompt: fixed instructions + package JSON + result schema.

    The package is serialized deterministically (sorted keys) and truncated to
    a hard cap so the prompt stays within a bounded size.
    """
    body = json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2)
    if len(body) > MAX_PROMPT_CHARS:
        body = body[:MAX_PROMPT_CHARS].rstrip() + "\n…[truncated]"
    schema = json.dumps(audit_result_schema(), ensure_ascii=False, indent=2)
    return (
        _AUDITOR_INSTRUCTIONS
        + "\n\nAUDIT PACKAGE:\n"
        + body
        + "\n\nEXPECTED OUTPUT SCHEMA:\n"
        + schema
    )


def run_auditor(
    package: dict, *, command: str, timeout: int = 300
) -> tuple[dict, list[Diagnostic]]:
    """Run an external auditor command with the package JSON on stdin.

    The command is split into an argv array with :func:`shlex.split` (never a
    shell) and run under ``timeout``. stdout is parsed as a
    tracelayer-audit-result/v1 JSON object; non-conforming output yields a
    diagnostic and an empty result dict. An empty command is a programmer
    error and raises ValueError.
    """
    argv = shlex.split(command)
    if not argv:
        raise ValueError("auditor command must not be empty")
    payload = json.dumps(package, ensure_ascii=False, sort_keys=True)
    diags: list[Diagnostic] = []
    try:
        proc = subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_WARNING,
                message=f"Semantic auditor timed out after {timeout}s.",
                metadata={"command": command, "timeout": timeout},
            )
        )
        return {}, diags
    except OSError as exc:
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_ERROR,
                message=f"Could not run semantic auditor: {exc}",
                metadata={"command": command},
            )
        )
        return {}, diags

    if proc.returncode != 0:
        stderr = (proc.stderr or "")[:MAX_STDERR_CHARS]
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_WARNING,
                message=f"Semantic auditor exited with code {proc.returncode}.",
                metadata={"command": command, "exit_code": proc.returncode, "stderr": stderr},
            )
        )
    stdout = proc.stdout or ""
    if len(stdout) > MAX_OUTPUT_CHARS:
        stdout = stdout[:MAX_OUTPUT_CHARS]
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_WARNING,
                message="Semantic auditor output exceeded the size cap; truncated.",
                metadata={"command": command},
            )
        )
    result, reason = _parse_result(stdout)
    if result is None:
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_ERROR,
                message=f"Semantic auditor output is not a valid audit result: {reason}",
                remediation=("Ask the auditor to emit JSON matching tracelayer-audit-result/v1."),
                metadata={"command": command},
            )
        )
        return {}, diags
    return result, diags


def integrate_audit_result(result: dict) -> list[Diagnostic]:
    """Map an auditor result to diagnostics (spec 30.3/30.4).

    Findings become TL060 diagnostics: WARNING for high severity, INFO for
    medium/low. A ``fail`` status with at least one high-severity finding also
    produces one TL060 INFO diagnostic flagged ``tl060_eligible`` so the policy
    engine can upgrade it to ERROR when semantic audit is required.
    """
    status = result.get("status")
    findings = result.get("findings") or []
    diags: list[Diagnostic] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = finding.get("severity", "low")
        refs = [r for r in (finding.get("trace_refs") or []) if isinstance(r, str)]
        claim = finding.get("claim", "")
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_WARNING if severity == "high" else SEVERITY_INFO,
                message=f"Semantic audit finding [{severity}]: {claim}",
                trace_id=refs[0] if refs else None,
                remediation=finding.get("recommended_action") or None,
                metadata={
                    "audit_finding": severity,
                    "audit_status": status,
                    "trace_refs": refs,
                },
            )
        )
    if status == "fail" and any(
        isinstance(f, dict) and f.get("severity") == "high" for f in findings
    ):
        diags.append(
            make(
                "TL060",
                severity=SEVERITY_INFO,
                message=("Semantic auditor reported status 'fail' with high-severity findings."),
                metadata={"audit_status": "fail", "tl060_eligible": True},
            )
        )
    return diags


# --------------------------------------------------------------------------
# Result parsing/validation
# --------------------------------------------------------------------------


def _parse_result(stdout: str) -> tuple[dict | None, str]:
    """Parse auditor stdout into a validated result dict, else (None, reason).

    The whole output is tried first; when that fails, the text between the
    first '{' and the last '}' is tried so auditors that wrap their JSON in
    prose or fenced code blocks still work.
    """
    text = stdout.strip()
    if not text:
        return None, "empty output"
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            ok, reason = _validate_result(data)
            if ok:
                return data, ""
            return None, reason
    return None, "output is not a JSON object"


def _validate_result(data: dict[str, Any]) -> tuple[bool, str]:
    """Shape-check a result dict against spec 30.3 / audit_result_schema()."""
    if "schema" in data and data["schema"] != AUDIT_RESULT_SCHEMA:
        return False, f"schema mismatch: {data['schema']!r}"
    if data.get("status") not in _RESULT_STATUSES:
        return False, "status must be one of pass|fail|uncertain"
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        return False, "findings must be a list"
    for finding in findings:
        if not isinstance(finding, dict):
            return False, "each finding must be an object"
        if finding.get("severity") not in _FINDING_SEVERITIES:
            return False, "finding severity must be one of high|medium|low"
        claim = finding.get("claim")
        if not isinstance(claim, str) or not claim:
            return False, "each finding must have a non-empty string claim"
        refs = finding.get("trace_refs", [])
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            return False, "trace_refs must be a list of strings"
    return True, ""
