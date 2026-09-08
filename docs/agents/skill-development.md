# Skill Development

This repository is the source of truth for personal skills. A skill has separate development and stable states:

```text
<repo>/
├── sk-<skill-name>/             # development source
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

The mapped `sk-<skill-name>/` and `skills/<skill-name>/` directories may coexist while work is active. The prefixed root Development Directory is the only editable source; `skills/<skill-name>/` is the current stable release and the only installation source. The `sk-` prefix is not part of the Skill Name. The repository root is an active workset, not a permanent archive: after Promotion, remove an unchanged Development Directory when no further work is planned. Keep repository tests outside the installed Skill package.

## Repository contract

- Make each Skill Name match `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII letters and digits separated only by single hyphens.
- Reserve canonical names beginning with `sk-`; name the Development Directory `sk-` plus the `name` field in `SKILL.md`, while Stable and test directories use the field exactly.
- Treat `SKILL.md` and `agents/openai.yaml` as required for a Stable Skill. Repository metadata requires non-empty `interface.display_name` and `interface.short_description`; other Codex metadata remains optional and type-checked.
- Put deterministic operations in `scripts/`, conditional documentation in `references/`, and reusable output material in `assets/`.
- Remove unfinished scaffold placeholders. A Skill containing any managed file under `scripts/` must provide `tests/<skill-name>/run`; when this entrypoint exists, Promotion must run it successfully.
- Do not edit a Stable Skill directly; create or reuse its mapped root Development Directory.
- Stable directories remain physically writable, but changes outside Promotion are Stable Drift rather than supported development and may be overwritten only with explicit drift replacement.
- Promote only after validation and tests pass. Promotion retains the Development Skill by default, but a `promoted-clean` root copy should remain only while further work is active or planned.
- Keep repository source separate from installed copies. Installation happens only through the manager or another explicit user request.

## Skill manager

The accepted manager location and full responsibility contract are defined in [skill-manager.md](./skill-manager.md). Its target TUI entrypoint is:

```bash
python3 tools/skill_manager.py
```

The manager's declared runtime dependencies are installed from the repository `pyproject.toml`; it never installs them automatically. Version 1 supports Linux only so its locking, symbolic-link, directory-flush, and recovery guarantees remain exact.

The TUI is installation-focused. It selects detected Agent Targets, edits desired Stable Skill Installation State, then previews the complete Batch Plan before an explicit `y` confirmation. Use the arrow keys to move, Space to change the desired state, Enter to advance, and `q` to cancel. Validation, diff, and Promotion remain explicit non-interactive commands.

The accepted non-interactive operations are:

```bash
python3 tools/skill_manager.py list
python3 tools/skill_manager.py status
python3 tools/skill_manager.py diff c-file-workflow
python3 tools/skill_manager.py validate c-file-workflow
python3 tools/skill_manager.py promote c-file-workflow --dry-run
python3 tools/skill_manager.py promote c-file-workflow --record-release --commit-message-file c-file-workflow=/tmp/c-file-workflow.msg --dry-run
python3 tools/skill_manager.py record-release c-file-workflow --commit-message-file c-file-workflow=/tmp/c-file-workflow.msg --dry-run
python3 tools/skill_manager.py install c-file-workflow --agent agents --dry-run
python3 tools/skill_manager.py uninstall c-file-workflow --agent agents --dry-run
python3 tools/skill_manager.py recover --dry-run
```

The current implementation is `python3 tools/skill_manager.py`; the legacy `python3 skills/manage.py` entrypoint has been removed. Use the accepted commands above for repository lifecycle and Agent installation operations.

Initial built-in targets are Codex, Claude Code, the shared Agent Skills directory, Hermes, and OpenCode. Each is a local Agent Target Adapter with its own effective configuration root and duplicate-exposure rules. A target is shown only when its Adapter resolves a real configuration root; an executable alone is insufficient. WorkBuddy is deferred until its filesystem installation contract is verified.

Do not select Codex together with the shared Agent Skills target, or OpenCode together with the shared Agent Skills or Claude target, in the same operation. Those consumers discover both roots and would expose the same Skill twice.

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
