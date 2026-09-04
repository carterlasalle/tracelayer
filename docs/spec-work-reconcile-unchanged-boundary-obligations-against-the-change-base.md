# Reconcile unchanged-boundary obligations against the change base

<!-- trace:v1 id=doc.reconcile-unchanged-boundary-obligations-against-the-change-base -->

<!-- trace:exempt reason=document-structure -->
## Goal

Stop-hook nag lists obligations for pre-existing unmarked helpers in touched files. Mirror TL013 fingerprint semantics in reconciliation: drop obligations whose boundary is fingerprint-identical to the change-base version.

<!-- trace:exempt reason=document-structure -->
## Requirements

### REQ-base-fingerprint-reconciliation — Base-fingerprint reconciliation

<!-- trace:v1 id=REQ-base-fingerprint-reconciliation type=requirement work=WORK-reconcile-unchanged-boundary-obligations-against-the-change-base -->

Pending obligations whose boundary fingerprint matches the change-base version resolve automatically; new or changed boundaries keep blocking.
