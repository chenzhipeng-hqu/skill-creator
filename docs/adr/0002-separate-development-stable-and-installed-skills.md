---
status: accepted
---

# Separate development, stable, and installed skills

Keep each actively worked Development Skill in a same-named repository-root directory, each Stable Skill in `skills/<skill-name>/`, and repository tooling in `tools/`. The repository root is an Active Workset rather than a permanent development-copy archive. The two same-named skill directories may coexist so a stable installed release can remain in use while its next version is developed; only the root copy is edited, Promotion updates the stable snapshot after validation and tests, and Installation uses only the stable snapshot. After Promotion, an unchanged development copy should leave the Active Workset when no further work is planned.

The Skill Manager owns discovery, status, comparison, validation, Promotion, Installation, and uninstallation. It uses one typed planning and execution core behind its CLI and installation-focused TUI, provides versioned machine-readable output for every non-interactive command, preflights every target before a mutation, supports dry runs, refuses conflicting destinations, and treats every requested batch as one transaction that must not leave requested mutations partially applied.

The manager reports Skill Presence, Lifecycle State, Validation State, and Installation State without allowing invalid contents to disappear during discovery. It infers lifecycle state from directory presence and one canonical Package Identity: root-only is unpublished, stable-only is stable and inactive, differing root and stable copies are modified, and identical root and stable copies are promoted-clean. It may recommend cleaning a promoted-clean Development Skill but does not delete it by default.

## Considered Options

- Put unfinished skills under `skills/_drafts/`: rejected because active work should be visible directly at the repository root.
- Keep only one mutable copy under `skills/`: rejected because editing it would immediately alter installed symlink targets.
- Put the manager inside `skills/`: rejected because repository tooling is not an installable skill.
