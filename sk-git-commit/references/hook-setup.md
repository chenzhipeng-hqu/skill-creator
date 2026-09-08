# Commit gate setup

Read this reference only when the user explicitly asks to install, change, or diagnose the optional commit gate for Codex or Claude Code.

## Boundary

`scripts/commit-gate.sh` is shared runtime hook logic. It belongs under `scripts/` because the Agent hook runtime executes it directly; it is not a template copied into generated output.

The gate is advisory: it blocks the first matching commit attempt and allows one retry. It cannot prove from `PreToolUse` input alone that the `git-commit` Skill was actually invoked. Do not describe it as an authorization or security boundary.

Skill Installation and hook registration are separate:

1. Promote and install the Stable Skill through the repository Skill Manager only when explicitly requested.
2. Register the installed Stable script in each requested Agent's hook configuration only when explicitly requested.

The Skill Manager does not edit hook configuration during ordinary Skill installation. Keep one runtime script source of truth; do not maintain separate Codex and Claude Code copies that can drift.

Root `skill-manager.yaml` declares this optional integration. Manager installation reads that manifest and reports it, but never enables it implicitly.

## Codex

Codex supports synchronous `PreToolUse` command hooks. Configure the hook to execute the script through the installed Codex Skill path, normally:

```text
~/.codex/skills/git-commit/scripts/commit-gate.sh
```

Codex accepts the script's legacy top-level `decision` and `reason` output. After changing configuration, inspect the effective hook list and trust state; configuration presence alone is not proof that a particular commit triggered the hook.

When the repository's current Stable `git-commit` version is installed to Codex, prefer the explicit Hook Adapter:

```bash
python3 tools/skill_manager.py hook install git-commit --agent codex
```

For one explicit installation batch, use:

```bash
python3 tools/skill_manager.py install git-commit --agent codex --with-hooks
```

It owns only its exact `~/.codex/hooks.json` entry. It does not edit `config.toml`; a differing existing entry is a conflict requiring user direction.

## Claude Code

Configure Claude Code's `PreToolUse` hook to execute the same script through the installed Claude Code Skill path, normally:

```text
~/.claude/skills/git-commit/scripts/commit-gate.sh
```

Do not copy the script into a separate `~/.claude/scripts/` location unless the user explicitly accepts that the copy can drift from the Stable Skill.
