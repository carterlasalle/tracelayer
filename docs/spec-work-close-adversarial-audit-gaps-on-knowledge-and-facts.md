# Close adversarial audit gaps on knowledge and facts

<!-- trace:v1 id=doc.close-adversarial-audit-gaps-on-knowledge-and-facts -->

<!-- trace:exempt reason=document-structure -->
## Goal

Independent audit found the knowledge/facts layers incomplete: unconfined canonical sources (P0), consumer-blind verification, narrow source support, no verify/hook wiring, direct-edge-only injection, missing metadata/templates/UI. Close each gap with tests.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-confined-live-fact-verification — Confined live fact verification

<!-- trace:v1 id=REQ-confined-live-fact-verification type=requirement work=WORK-close-adversarial-audit-gaps-on-knowledge-and-facts -->

Canonical sources are confined to the repo; dependents are read live across TOML/JSON/YAML/Python/Markdown; drift surfaces in facts, context, hooks, and verify.

### REQ-transitive-knowledge-relevance — Transitive knowledge relevance

<!-- trace:v1 id=REQ-transitive-knowledge-relevance type=requirement work=WORK-close-adversarial-audit-gaps-on-knowledge-and-facts -->

knowledge_for traverses work/requirement/path scope, knowledge appears in trace context, and metadata/templates capture workflow exist.
