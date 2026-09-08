---
status: accepted
---

# Record promoted skills individually in local Git

When explicitly requested with `promote --record-release`, include local Git history in the recoverable Atomic Batch: create one normal, hook-validated commit per changed Skill in canonical name order and attach one immutable unsigned annotated tag named `<skill-name>-YYMMDD-vN` to each corresponding commit. Caller-supplied commit messages preserve the repository's `git-commit` approval workflow, while per-Skill commits make later history and rollback attributable; temporary intermediate commits may be visible before the batch commit point because preserving normal Git hooks is preferred over constructing an off-ref commit chain with plumbing commands.

When a completed Promotion omitted Release Recording, permit an explicitly requested `record-release` Release Backfill only while the current Stable Package Identity exactly matches its Promotion Receipt. Release Backfill uses a separate recoverable Git-only Atomic Batch, commits only the selected Stable path, and annotates the tag with both `Recording-Mode: backfill` and the original Promotion transaction. It never rewrites the Promotion Receipt or represents the later Git transaction as part of the original Promotion.

## Consequences

Release Recording requires an attached existing HEAD, clean index, configured Git identity, no in-progress Git operation, and recoverable local refs. Existing tags are never moved or deleted, no-change Skills create no commit or tag, and remote fetch or push is outside the manager's transaction.

Release Backfill has the same Git preflight and scoped-hook guarantees. It is idempotent when an immutable tag already matches the Receipt identity and Promotion transaction. It refuses missing or mismatched Receipts and refuses to create an empty commit or attach a new tag to an arbitrary existing commit. Deleting and repeating a Promotion to manufacture release history is not supported.
