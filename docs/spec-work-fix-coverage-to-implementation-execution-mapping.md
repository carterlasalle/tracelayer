# Fix coverage-to-implementation execution mapping

<!-- trace:v1 id=doc.fix-coverage-to-implementation-execution-mapping -->

<!-- trace:exempt reason=document-structure -->
## Goal

CI TL022: suite coverage never maps to implementation nodes because report paths mismatch canonical paths and same-file implementations collide in one dict slot, yielding zero execution edges. Normalize paths and carry all ranges.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-execution-edge-mapping — Execution edge mapping

<!-- trace:v1 id=REQ-execution-edge-mapping type=requirement work=WORK-fix-coverage-to-implementation-execution-mapping -->

Coverage hit lines map to every overlapping implementation range with tolerant path matching so in-scope exercises edges verify.
