---
status: proposed
---

# Unify C file creation, formatting, and checking

Use one `c-file-format` skill for all Managed C Files rather than tying layout rules to component architecture. A bare C file name selects creation from the C File Templates, while existing `.c` and `.h` paths select in-place Style Format; the same formatting core also provides a read-only Style Check. This keeps one formatting authority while leaving Pub/CB/Ops, state machines, and other component semantics outside the skill.

## Considered Options

- Keep the three capabilities in `gd32-component-creator`: rejected because ordinary board, task, driver, and utility files are not necessarily components.
- Create separate template, formatter, and checker skills: rejected because their formatting rules could drift and users would need to choose between overlapping tools.
- Require an explicit subcommand for every operation: not selected for the common create/format paths because input shape can distinguish them; explicit syntax for Style Check remains undecided.
