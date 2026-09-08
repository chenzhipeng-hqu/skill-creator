---
status: accepted
---

# Use local Agent Target adapters

Model each supported runtime as a local Agent Target Adapter rather than a generic configurable path. The first implementation supports `codex`, `claude`, `agents`, `hermes`, and `opencode`, allowing each family to own its effective configuration root, lock, managed-link inspection, and duplicate-exposure rules; WorkBuddy remains out of scope until its filesystem package and installation contracts are verified.

## Consequences

Installation runs on the same host as the target and never extends the local Atomic Batch across SSH, Git transport, NAS, or another home device. Stable versions move between hosts through a separately authorized synchronization workflow, after which each host runs its own local Adapter. Hermes resolves only the active `HERMES_HOME`, while OpenCode accounts for its native, `.agents`, and `.claude` discovery roots.
