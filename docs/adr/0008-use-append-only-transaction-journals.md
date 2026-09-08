---
status: accepted
---

# Use append-only transaction journals

Represent each Atomic Batch with an immutable `plan.json` and an append-only, durably flushed, SHA-256 hash-chained `events.jsonl` under `.skill-manager/transactions/<uuidv4>/`, rather than repeatedly overwriting one mutable state file. This preserves the verified preconditions and intended operations while making interruption, deletion, and reordering between filesystem actions observable and recoverable; recovery combines complete events with actual before/after identities instead of trusting either source alone.

## Consequences

Only one transaction may remain short of `CLEANED` in a repository. A truncated final event is ignored and reconciled from actual state, hash-chain corruption before the final record or multiple active transactions requires operator recovery, and `COMMITTED` remains the sole point separating rollback from roll-forward cleanup. Active journals are never migrated across schema versions automatically. After `CLEANED` is durably recorded, the manager immediately removes the transaction directory; an interrupted deletion is resumed idempotently, and permanent audit evidence lives outside transaction scratch state.
