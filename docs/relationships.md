<!-- GENERATED from protocol registries. Do not edit by hand; run `trace docs generate`. -->

# Edge Types

## Semantic edges

| Edge | Meaning | Typical source -> target |
|---|---|---|
| `work` | source was produced or authorized by the target work item | implementation/test/document -> WORK-... |
| `derived_from` | source was derived from target | requirement -> PRD; plan -> decision |
| `addresses` | source is intended to address target | decision/work -> requirement |
| `satisfies` | source behavior fulfills target contract | implementation -> requirement |
| `implements` | source realizes target plan or decision | implementation/config -> plan/decision |
| `verifies` | source test or evidence verifies target contract | test -> requirement |
| `exercises` | source test intends to execute target implementation | test -> implementation |
| `documents` | source documents target | document/runbook -> implementation/requirement |
| `deploys` | source operation or config deploys target | operation -> implementation |
| `depends_on` | semantic dependency beyond trivial inferred calls | implementation/requirement -> artifact |
| `supersedes` | source replaces target | decision/requirement -> same type |
| `produces` | source activity produces target | plan/CI -> implementation/evidence |
| `consumes` | source relies on target artifact or data | implementation -> data/config |
| `blocks` | source must resolve before target progresses | work/requirement -> work/release |
| `blocked_by` | source is blocked by target | task -> task/question |
| `related_to` | source is related to target | artifact -> artifact |
| `discovered_from` | source was discovered while working on target | task/work -> task/work |
| `asks` | source poses target question | task/work -> question |
| `answers` | source answers target question | decision -> question |
| `answered_by` | source question is answered by target | question -> decision |
| `resolves` | source resolves target | decision/implementation -> question/task |
| `proposes` | source proposes target design | rfc -> spec/decision |
| `decides` | source records the decision for target | decision -> question/requirement |
| `parent` | source is the parent of target in a work hierarchy | work/task -> task |
| `child` | source is a child of target in a work hierarchy | task -> work/task |
| `introduced_by` | source was introduced by target activity | artifact -> work/commit |
| `applies_to` | source knowledge applies to target artifact | knowledge -> artifact |
| `learned_from` | source knowledge was learned from target | knowledge -> work/task |
| `establishes` | source establishes target fact | decision -> fact/value |
| `canonicalizes` | source file is the canonical source of target fact | file -> fact/value |
| `depends_on_value` | source relies on target canonical value | artifact -> value |
| `documents_value` | source documents target canonical value | document -> value/fact |
| `mirrors_value` | source mirrors target canonical value | artifact -> value |
| `derives_value` | source value is derived from target | value -> value |
| `generated_from` | source is generated from target | artifact -> value/fact |
| `historical_reference` | source references target as history | document -> value/fact |
| `explains` | source explains target | knowledge/document -> artifact |
| `warns_against` | source warns against target approach | anti_pattern -> artifact |
| `recommended_for` | source is recommended for target | convention -> artifact |

## Structural edges

| Edge | Meaning | Typical source -> target |
|---|---|---|
| `contains` | structural containment | parent symbol -> child symbol |
| `calls` | function call relationship | caller -> callee |
| `imports` | import or use of another module | module -> module |
| `inherits` | class inheritance | subclass -> base class |
| `references_symbol` | identifier reference | reference site -> symbol |
| `reads` | reads a field or variable | reader -> field |
| `writes` | writes a field or variable | writer -> field |
| `changed_by` | revision that changed the artifact | node -> commit |
| `owned_by` | ownership relationship | node -> owner |

## Observed edges

| Edge | Meaning | Typical source -> target |
|---|---|---|
| `executed` | test executed the implementation at runtime | test -> implementation |
| `passed` | test passed in an evidence run | test -> run |
| `failed` | test failed in an evidence run | test -> run |
| `built_in` | artifact built in an evidence run | artifact -> run |
| `deployed_in` | artifact deployed in an environment | artifact -> environment |
| `attested_by` | evidence attestation | artifact -> attestation |

