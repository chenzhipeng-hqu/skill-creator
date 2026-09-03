# Skill Development

This repository is the source of truth for personal skills. A skill has separate development and stable states:

```text
<repo>/
├── <skill-name>/                # development source
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── scripts/                 # optional
│   ├── references/              # optional
│   └── assets/                  # optional
├── skills/
│   └── <skill-name>/            # stable release snapshot
├── tools/
│   └── skill_manager.py
└── tests/
    └── <skill-name>/
        └── run                  # deterministic repository test entrypoint
```

The same skill name may exist at the root and under `skills/` while work is active. The root directory is the only editable development source; `skills/<skill-name>/` is the current stable release and the only installation source. The repository root is an active workset, not a permanent archive: after Promotion, remove an unchanged root copy when no further work is planned. Keep repository tests outside the installed skill package.

## Repository contract

- Make each Skill Name match `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII letters and digits separated only by single hyphens.
- Keep the directory name equal to the `name` field in `SKILL.md`.
- Treat `SKILL.md` and `agents/openai.yaml` as required for a Stable Skill.
- Put deterministic operations in `scripts/`, conditional documentation in `references/`, and reusable output material in `assets/`.
- Remove unfinished scaffold placeholders. A Skill with executable scripts must provide `tests/<skill-name>/run`; when this entrypoint exists, Promotion must run it successfully.
- Do not edit a Stable Skill directly; create or reuse its same-named root Development Skill.
- Promote only after validation and tests pass. Promotion retains the Development Skill by default, but a `promoted-clean` root copy should remain only while further work is active or planned.
- Keep repository source separate from installed copies. Installation happens only through the manager or another explicit user request.

## Skill manager

The accepted manager location and full responsibility contract are defined in [skill-manager.md](./skill-manager.md). Its target TUI entrypoint is:

```bash
python3 tools/skill_manager.py
```

The TUI is installation-focused. Its first screen selects detected Agent Targets, and its second screen shows Stable Skills and their Installation State. Use the arrow keys to move, Space to change the desired state, Enter to apply the full selection as one batch, and `q` to cancel. Validation, diff, and Promotion remain explicit non-interactive commands.

The accepted non-interactive operations are:

```bash
python3 tools/skill_manager.py list
python3 tools/skill_manager.py status
python3 tools/skill_manager.py diff c-file-format
python3 tools/skill_manager.py validate c-file-format
python3 tools/skill_manager.py promote c-file-format --dry-run
python3 tools/skill_manager.py install c-file-format --agent agents --dry-run
python3 tools/skill_manager.py uninstall c-file-format --agent agents --dry-run
python3 tools/skill_manager.py recover --dry-run
```

The current implementation has not yet moved: use `python3 skills/manage.py` only for its existing list, TUI, install, uninstall, and dry-run behavior until `tools/skill_manager.py` and its tests are implemented.

Known targets are Codex (`~/.codex/skills`), Claude Code (`~/.claude/skills`), and the shared Agent Skills directory (`~/.agents/skills`). A target is shown when its executable or configuration directory is present.

Do not select both Codex and the shared Agent Skills target in the same operation. Codex can discover both roots in this environment, which would expose the same skill twice.

## Installation states

- `[x]`: installed for every selected target.
- `[ ]`: not installed for any selected target.
- `[~]`: installed for only some selected targets.
- `[!]`: a regular file, real directory, or unrelated symbolic link occupies a destination.

Selecting a skill requests installation for every selected target. Clearing it requests uninstallation from every selected target.

## Development states

- `unpublished`: a root Development Skill exists without a Stable Skill.
- `stable`: only the Stable Skill exists; no work is active.
- `modified`: development and stable copies both exist and differ.
- `promoted-clean`: development and stable copies both exist and are identical; the development copy can be removed from the active workset when no further work is planned.

## Safety rules

- Discover candidate Development and Stable locations independently from validation, so invalid contents remain visible with diagnostics rather than being treated as absent.
- Exclude `skills/`, `tools/`, `tests/`, `docs/`, and other repository infrastructure from Development discovery.
- Never install a root Development Skill.
- Validate and test every Development Skill before Promotion.
- Preflight an entire requested batch before Promotion, Installation, or uninstallation; handle it as one recoverable transaction and do not leave requested changes partially applied.
- Create links only after all destinations for that skill pass preflight checks.
- Treat a symbolic link to the exact repository skill directory as installed and leave it unchanged.
- Never overwrite a regular file, real directory, or symbolic link to another location.
- Remove only a symbolic link that still points to the exact repository skill directory.
- Make no filesystem changes before Enter in the TUI or when `--dry-run` is used.
- Report every applied, skipped, and conflicting operation.
