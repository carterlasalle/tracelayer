# Durable knowledge nodes and canonical facts

<!-- trace:v1 id=doc.durable-knowledge-nodes-and-canonical-facts -->

<!-- trace:exempt reason=document-structure -->
## Goal

Addendum sections 81-124: first-class FINDING/LEARNING/ANTI_PATTERN/CONVENTION/CONSTRAINT knowledge nodes with lifecycle and scoped injection, plus FACT/VALUE canonical-source tracking with trace facts verification.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-knowledge-node-ontology — Knowledge node ontology

<!-- trace:v1 id=REQ-knowledge-node-ontology type=requirement work=WORK-durable-knowledge-nodes-and-canonical-facts -->

FINDING, LEARNING, ANTI_PATTERN, CONVENTION, CONSTRAINT node types with ID namespaces, lifecycle states, and applies_to/learned_from relationships plus scoped knowledge injection queries.

### REQ-canonical-fact-tracking — Canonical fact tracking

<!-- trace:v1 id=REQ-canonical-fact-tracking type=requirement work=WORK-durable-knowledge-nodes-and-canonical-facts -->

FACT/VALUE nodes with canonical_source, dependent-edge predicates, and trace facts verify that detects drift between the canonical source and recorded values.
