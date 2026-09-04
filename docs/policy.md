# Policy
<!-- trace:v1 id=doc.tracelayer.policy -->

Policy answers the question: *is this repository state good enough for the
current lifecycle?* It is deliberately separate from the marker schema, which
only answers *is this marker structurally valid?* (24.1).

## Severity levels

- `ERROR` — blocks under the current lifecycle/profile.
- `WARNING` — important but does not block.
- `INFO` — enrichment or maintenance suggestion.

## Lifecycle

The lifecycle is the stage a change is moving through. Built-in lifecycles:

```text
draft, wip, review, merge, release
```

Verification is lifecycle-aware: the same repository state can be fine at
`wip` and blocking at `merge`. The lifecycle is requested explicitly or taken
from policy configuration (`lifecycle.ci = "merge"` for CI runs).

## Profiles

Profiles define the default rule set and requirement gates. They are meant to
be used as a ladder during adoption.

### minimal

Good for initial adoption. At merge it requires:

- marker syntax valid;
- IDs unique;
- declared edge targets resolve;
- no source marker attached ambiguously where the parser claims structural
  support.

### standard

Adds:

- changed traced implementation has work or requirement ancestry;
- changed requirement propagates stale status;
- linked tests must exist;
- tests required by policy must pass at current revision;
- required stale nodes block merge.

### strict

Adds:

- new/changed meaningful public behavior must be traced;
- implementation must satisfy requirement;
- test must verify requirement;
- test should exercise implementation;
- coverage/execution evidence required where supported;
- unexplained deletion of traced behavior blocks;
- semantic hash changes invalidate old evidence.

### safety-critical

Adds configurable formal requirements:

- no unverified requirements in protected scope;
- all evidence tied to exact revision;
- independent audit artifact required;
- explicit waiver records with approver identity;
- optional signed attestations.

## Policy IDs

Every deterministic rule has a stable ID, so failures are explainable
(NFR-008). Diagnostics reference these IDs and carry a remediation action.

| ID | Rule |
|---|---|
| TL001 | duplicate trace ID |
| TL002 | unresolved edge target |
| TL003 | detached/ambiguous structural marker |
| TL004 | malformed marker syntax |
| TL005 | invalid trace ID |
| TL006 | duplicate key on one marker |
| TL007 | invalid field value |
| TL010 | changed behavior missing requirement ancestry |
| TL011 | changed requirement has stale downstream implementation |
| TL012 | changed file has no traced behavior at all |
| TL013 | individual behavior boundary is not trace-accounted (local marker, explicit inherit, or exempted) |
| TL014 | plan expected artifact missing or not linked via implements |
| TL020 | required verification test missing |
| TL021 | linked test did not pass at current revision |
| TL022 | exercise claim lacks required execution evidence |
| TL030 | traced symbol deleted with unresolved incoming edges |
| TL040 | unknown marker key |
| TL050 | evidence revision mismatch |
| TL051 | evidence parser failure |
| TL060 | semantic audit required |
| TL061 | expired waiver |
| TL062 | evidence not bound to exact revision |
| TL070 | canonical fact drift: live dependent diverges from live source |
| TL100 | configuration error |
| TL110 | required stale node blocks lifecycle |

## Example policy configuration

`.trace/policy.toml`:

```toml
profile = "standard"

[lifecycle]
default = "wip"
ci = "merge"

[requirements.merge]
require_work_ancestry = true
require_requirement_for_changed_behavior = true
require_verifying_test = true
require_test_pass = true
require_coverage_confirmation = false
block_stale = true

[requirements.release]
require_coverage_confirmation = true
require_semantic_audit = true

[exclusions]
paths = ["vendor/**", "generated/**", "docs/vendor/**"]
```

Explicit per-lifecycle requirements override the profile defaults; anything
not mentioned falls back to the profile's default for that lifecycle.

## Waivers

Waivers are explicit, scoped, expiring where possible, and reviewable. There
are no magic `trace:ignore-all` comments.

```toml
[[waiver]]
rule = "TL022"
trace_id = "impl.legacy.crypto-adapter"
reason = "Coverage tool cannot instrument vendor boundary; integration evidence attached"
expires = "2026-10-01"
owner = "security-team"
```

A waiver matches a specific rule, optionally scoped to a trace ID and/or
path. Expired waivers become blocking under strict profiles (TL061).

## Enforcement

`trace verify --changed --lifecycle merge` evaluates the enabled rules for
the profile and effective requirements against the changed scope, returns a
verdict, and stores the diagnostics back into the index. Exit codes:

- `0` — no blocking diagnostics;
- `1` — blocking trace/policy failure;
- `2` — configuration/schema/input error;
- `3` — repository/index unavailable or corrupt;
- `4` — evidence parser failure when evidence required.

Waived diagnostics are downgraded to `INFO` and carry the waiver owner in
their metadata.
