---
status: accepted
---

# Use recoverable atomic batches

Treat all selected changes from one Skill Manager command and one operation type as one Atomic Batch. Full instantaneous atomicity cannot be guaranteed portably across repository and Agent Target filesystems, so the manager uses durable transaction state under `<repo>/.skill-manager/transactions/`, immediate rollback for handled failures, and mandatory recovery to the complete pre-transaction state after an interrupted process; it refuses new mutations when recovery cannot complete. A durably recorded `COMMITTED` phase is the only commit point: interruption before it rolls back, while interruption after it preserves the new state and finishes cleanup.

When Release Recording is explicitly requested, the local Git commits and annotated tags are part of the same recoverable Atomic Batch. Each promoted Skill receives its own commit for traceability. Remote fetch, push, and remote tag mutation remain outside the transaction.

## Consequences

The manager serializes cooperating writers with repository and Agent Target locks, verifies planned preconditions against concurrent external changes, and tests rollback and restart recovery with injected failures. It durably flushes staged content, journals, links, renames, and their affected directories so the contract covers power loss rather than only ordinary process failure. A process interruption may expose an intermediate filesystem state until recovery runs, but the manager never treats that state as a completed batch. Read-only commands report unfinished transaction state without mutating it; mutating commands must recover first, and an explicit `recover` command makes that action inspectable and automatable. Recovery has no force or accept-current mode: uncertain user content is never overwritten or reclassified as committed.
