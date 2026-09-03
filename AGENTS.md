## Agent skills

### Issue tracker

Issues live in GitHub for `chenzhipeng-hqu/skills`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles use their default label names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Skill development

Development skills live at the repository root as `<skill-name>/`. The root is the active workset, not a permanent archive of development copies. Stable, installable snapshots live under `skills/<skill-name>/`. The same name may exist in both places while work is active: the root copy is the only development source, while the `skills/` copy is the current stable release and must not be edited directly. After Promotion, remove an unchanged root copy when no further work is planned.

A stable skill must contain `SKILL.md` and `agents/openai.yaml`; add `scripts/`, `references/`, and `assets/` only when they are needed. Promotion from the root development copy to `skills/` requires validation and script tests.

Use lowercase letters, digits, and hyphens for skill names, and keep the folder name equal to the `name` field in `SKILL.md`. Validate the skill and test its scripts before calling it complete.

The accepted manager entrypoint is `python3 tools/skill_manager.py`; it owns listing, status, comparison, validation, promotion, installation, and uninstallation. Until that relocation is implemented, the current installation-only entrypoint remains `python3 skills/manage.py`. Do not write directly to global Agent skill directories unless the user explicitly requests installation. See `docs/agents/skill-development.md` and `docs/agents/skill-manager.md`.
