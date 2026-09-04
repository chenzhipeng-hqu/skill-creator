---
status: accepted
---

# Unify the C file workflow

Use one `c-file-workflow` Skill for mechanical C/H formatting, deterministic checks, default Pub/CB/Ops component creation, Agent-guided migration, and documented external-chip Register Structure conventions. This replaces overlapping generic-format and GD32-component Skills while keeping Style Format, Component Structure Check, and behavior-aware Component Migration as distinct operations; the detailed contract lives in [the C File Workflow design](../design/c-file-workflow.md).

## Considered Options

- Separate template, formatter, checker, and component Skills: rejected because their shared formatting and component conventions could drift.
- Keep `gd32-component-creator`: rejected because the unified workflow now owns its component and register conventions; retirement remains an explicit user action.
- Name the Skill `c-file-format`: rejected after component creation, checking, and migration made formatting too narrow a description.
