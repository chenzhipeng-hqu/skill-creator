---
name: c-file-workflow
description: Create, format, and check self-authored C source and header files. Use for plain C/H scaffolding, Pub/CB/Ops component files, mechanical clang-format layout, component-structure checks, register-structure checks, or behavior-aware migration into the component pattern; do not apply it automatically to vendor, generated, or ordinary logic-only edits.
---

# C File Workflow

Use the bundled script for deterministic creation, formatting, and read-only checks. Resolve `scripts/c_file_workflow.py` relative to this Skill directory and invoke it with `python3`.

## Select the operation

- Create a Pub/CB/Ops component pair: `python3 scripts/c_file_workflow.py uart`
- Create a plain pair: `python3 scripts/c_file_workflow.py --plain uart`
- Create one plain file: `python3 scripts/c_file_workflow.py --new uart.c`
- Format existing files: `python3 scripts/c_file_workflow.py uart.c uart.h`
- Check style without writing: `python3 scripts/c_file_workflow.py --check uart.c uart.h`
- Check a component pair: `python3 scripts/c_file_workflow.py --check --component uart.c`
- Check register structures: `python3 scripts/c_file_workflow.py --check --register chip.c chip.h`

Run `python3 scripts/c_file_workflow.py --help` for output paths, recursion, exclusions, symlinks, author, style, and clang-format selection.

## Boundaries

- Treat formatting as mechanical layout only. Never claim that it proves syntax, compilation, target behavior, or hardware behavior.
- Do not insert Component Structure into an existing file mechanically. For a requested migration, first understand and preserve its behavior, make the architectural changes as normal code edits, and then run format and checks.
- Read [component patterns](references/component-patterns.md) when adding cross-module public handles, callbacks, GPIO/platform operations, or other component extensions.
- Read [register structures](references/register-structures.md) when implementing or reviewing a documented external-chip register map. Do not invent register facts that are absent from the device documentation.
- Keep vendor, third-party, generated, and externally maintained files out of bulk processing. Process one only when the user explicitly brings that exact file under local ownership.

## Verification

After creation or migration, run the relevant Style Check and structural checks. Run available focused and aggregate host tests plus a target build when the destination project supports them. Report host tests, target builds, flashing, and hardware results as separate proof levels.
