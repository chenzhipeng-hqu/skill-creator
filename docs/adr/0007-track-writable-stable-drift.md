---
status: accepted
---

# Track writable Stable drift

Keep Stable Skills writable instead of enforcing read-only filesystem modes, while retaining Development Skills as the only supported editing source. Record a local Promotion Receipt after every successful Promotion so the manager can distinguish the produced Stable Package Identity from later runtime or external edits; such edits are Stable Drift, immediately visible through existing installation links but not automatically promoted or recorded in Git.

## Consequences

Read-only inspection and structurally valid Installation may use a drifted Stable Skill with an explicit warning, while Promotion refuses to discard drift unless the caller passes `--replace-stable-drift`. Release Recording always commits content regenerated from Development, never silently adopts runtime edits. A missing receipt may be reconstructed from a valid annotated release tag; otherwise provenance remains unknown.
