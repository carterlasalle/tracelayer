# Security Policy

TraceLayer is a deterministic traceability engine that can be embedded in the coding-agent control loop (hooks, Stop gates, CI). Because repository-controlled strings flow through it, we treat them as untrusted data and treat security reports seriously.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` (current) | :white_check_mark: |
| earlier / unreleased | :x: |

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

1. Open a private security advisory at <https://github.com/carterlasalle/tracelayer/security/advisories/new> (preferred), or
2. Contact the maintainer directly.

Include:

- Affected component and version
- Steps to reproduce, with a minimal fixture if possible
- Impact and whether you believe it is exploitable

We will acknowledge within 5 business days and aim to publish a fix and advisory within 90 days.

## Scope

In scope:

- Prompt injection through marker titles or repository text reaching hook output
- Shell injection through marker values, paths, or evidence files
- Path traversal / symlink escapes outside the repository root
- Bypasses of fail-closed policy or Stop-gate enforcement
- Evidence spoofing that could be presented as current

Out of scope:

- Vulnerabilities in dependencies, unless TraceLayer misuses the dependency in a way that creates the issue
- Issues in repositories that merely *use* TraceLayer, unless TraceLayer itself causes them
- Social engineering, or issues requiring an attacker to already have write access to the repository

## Security model

The design mitigations are documented in [docs/security.md](docs/security.md) (threats T1–T10):

- Hook output is bounded, sanitized, template-generated text; repository data is never elevated to instructions.
- All subprocess invocation uses argv arrays; trace-controlled values are never shell-interpolated.
- Evidence files are parsed as untrusted input and bound to revisions; fabricated evidence cannot become current.
- Policy/schema changes surface as sensitive changes (TL063) rather than silently weakening enforcement.

## Disclosure

We follow coordinated disclosure. If you report privately and the issue is confirmed, we will credit you in the advisory unless you ask otherwise.
