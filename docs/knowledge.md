# Engineering knowledge

<!-- trace:v1 id=doc.engineering-knowledge work=WORK-durable-knowledge-nodes-and-canonical-facts -->

Durable findings and lessons (addendum Sections 81-93). Query with
`trace knowledge --for <artifact>` or `trace knowledge <id>`.
<!-- trace:v1 id=LEARN-TL-REGISTRY-CONTRACT type=learning title="Registry edits must extend the contract tests" state=ACTIVE work=WORK-durable-knowledge-nodes-and-canonical-facts applies_to=impl.protocol.work-model-ontology -->
## Registry edits must extend the contract tests


Protocol registry tests pin node/edge counts, exact sets, and ID
namespaces. Extending the ontology without extending `SPEC_NODE_TYPES`,
`SPEC_SEMANTIC`, and `INFERENCE_CASES` fails the suite — and the same
exact-match assertions once caught an accidental deletion of the
`produces`/`consumes` edges during a range edit.

When changing `protocol/ontology.py` or `protocol/ids.py`, update
`tests/unit/protocol/test_ontology.py` and `test_ids.py` in the same
change, and diff the registry before and after to confirm no entry was
lost.
