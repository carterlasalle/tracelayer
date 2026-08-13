## TraceLayer change

### Trace IDs

<!-- Requirement/work IDs this change touches, e.g. REQ-AUTH-017 / WORK-AUTH-237 -->

-

### Change summary

-

### Safety

- Security impact: none / described below
- Staleness impact: none / described below
- Policy impact: none / described below
- Rollback:

### Verification

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run trace docs generate --check` passes when the protocol changed
- [ ] `uv run trace verify --changed` passes under the active policy
- [ ] Evidence ingested for changed traced behavior, when applicable
