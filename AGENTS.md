## Agent skills

### Issue tracker

Issues live in GitHub for `chenzhipeng-hqu/skills`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles use their default label names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Skill development

Development Skills live at the repository root in Development Directories named `sk-<skill-name>/`. The root is the active workset, not a permanent archive of development copies. Stable, installable snapshots live under `skills/<skill-name>/`. Both mapped directories may coexist while work is active: the `sk-` directory is the only development source, while the `skills/` copy is the current stable release and must not be edited directly. After Promotion, remove an unchanged Development Directory when no further work is planned.

A stable skill must contain `SKILL.md` and `agents/openai.yaml`; add `scripts/`, `references/`, and `assets/` only when they are needed. Promotion from the root development copy to `skills/` requires validation and script tests.

Use lowercase letters, digits, and hyphens for Skill Names. Keep a Development Directory equal to `sk-` plus the `name` field in `SKILL.md`; keep its Stable directory equal to the field without the prefix. Canonical Skill Names beginning with `sk-` are reserved and invalid. Validate the Skill and test its scripts before calling it complete.

The current manager entrypoint is `python3 tools/skill_manager.py`; it owns listing, status, comparison, validation, Promotion, Release Recording, Release Backfill, Installation, uninstallation, and recovery. The legacy `python3 skills/manage.py` entrypoint has been removed. Do not write directly to global Agent skill directories unless the user explicitly requests installation. See `docs/agents/skill-development.md` and `docs/agents/skill-manager.md`.
