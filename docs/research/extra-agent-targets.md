# Extra Agent Targets: Hermes Agent, OpenCode, and WorkBuddy

Research date: 2026-09-03 (Asia/Shanghai)

This note answers one narrow question for Skill Manager design: whether each
runtime can consume a Stable Skill through a local directory/symlink target,
which local paths and package contracts are public, and where a target crosses
a device boundary. Claims below use first-party documentation or first-party
source. The inspected source revisions were Hermes Agent
`05f548f35dd3242bf2ff74743e9112acde251f77` and OpenCode
`f12e14cf1640cbf0dfb6b1ff425b2daaef459eec`.

## Summary

| Runtime | Agent Skills / `SKILL.md` | Public local target | Directory symlink | Extra registry for a manually placed Skill | Recommended Skill Manager treatment |
| --- | --- | --- | --- | --- | --- |
| Hermes Agent | Yes; explicitly declares agentskills.io compatibility | Default `<HERMES_HOME>/skills/`, normally `~/.hermes/skills/`; configurable external/create/project roots also exist | Yes at discovery level; source walks with `followlinks=True` | No for an ordinary local/external Skill; Hermes' bundled and Hub flows have their own metadata and must remain Hermes-owned | Adapter-specific local target; account for active `HERMES_HOME`/profile and protect Stable files from Hermes writes |
| OpenCode | Yes; official docs call it Agent Skills and define the standard `SKILL.md` frontmatter/name rules | Linux global native root `~/.config/opencode/skills/`; also scans global `~/.agents/skills/` and `~/.claude/skills/` | Yes; first-party scanner sets `symlink: true` | No; one Skill directory plus `SKILL.md` is sufficient | Local symlink target is supported, but avoid duplicate installation when `.agents` already supplies the Skill |
| Tencent WorkBuddy desktop (probable product) | Unresolved; marketing compatibility is not a public package contract | No first-party filesystem/config/Skill path found | Unresolved | Unresolved; GUI import performs automatic configuration and security checks | Do not model as a Managed Symlink Target yet; use package export plus manual per-device import |

## Hermes Agent

### Confirmed contracts

- Hermes' official Skills documentation says its Skills are compatible with
  the [agentskills.io specification](https://agentskills.io/specification) and
  describes `<HERMES_HOME>/skills/` as the primary local Skill tree. The default
  profile path is `~/.hermes/skills/`; `HERMES_HOME` and named profiles mean a
  target adapter must resolve the active profile instead of assuming one fixed
  home. Sources: [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills),
  [configuration layout](https://github.com/NousResearch/hermes-agent/blob/05f548f35dd3242bf2ff74743e9112acde251f77/CONTRIBUTING.md#L285-L297),
  and [`get_skills_dir()`](https://github.com/NousResearch/hermes-agent/blob/05f548f35dd3242bf2ff74743e9112acde251f77/hermes_constants.py#L1702-L1704).
- A Skill is a directory containing `SKILL.md`. The documented format has
  required `name` and `description` frontmatter and optional Hermes extensions.
  A Skill Manager package may carry extra support files; Hermes does not require
  this repository's `agents/openai.yaml`. Source:
  [SKILL.md Format](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#skillmd-format).
- Hermes also supports `skills.external_dirs` in `~/.hermes/config.yaml`,
  `skills.create_dir`, and trusted project-local `.hermes/skills/` and
  `.agents/skills/` roots. External paths are expanded and resolved, and no
  separate registration file is required merely to scan them. Source:
  [External Skill Directories and Project-Local Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#external-skill-directories).
- Directory links are discoverable in the inspected implementation: the Skill
  index uses `os.walk(..., followlinks=True)` when searching for `SKILL.md`.
  Therefore a direct link such as
  `~/.hermes/skills/foo -> /repo/skills/foo` works at the discovery layer.
  Source:
  [`iter_skill_index_files`](https://github.com/NousResearch/hermes-agent/blob/05f548f35dd3242bf2ff74743e9112acde251f77/agent/skill_utils.py#L1285-L1320).
- Hermes' own installation channels are richer than manual placement:
  `hermes skills install` supports official, skills.sh, well-known, URL, and
  GitHub sources. Hub state and bundled-skill origin hashes live below the
  local Skill tree (`.hub/...` and `.bundled_manifest`). Those files belong to
  Hermes' installer/update lifecycle; an external Skill Manager should not
  forge or edit them. Source:
  [Skills Hub and bundled updates](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#skills-hub).
- Hermes plugins are a different package type: user plugins live in
  `~/.hermes/plugins/<name>/`, require `plugin.yaml` plus Python registration
  code, and third-party plugins are enabled through Hermes' plugin controls.
  A Skill is not a plugin manifest. Source:
  [Hermes Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins).

### Design consequence

Hermes explicitly warns that local and writable external Skills can be changed
in place by its `skill_manage` operations. A symlink into this repository's
Stable tree therefore makes those Stable files writable through Hermes whenever
the repository permissions allow it. That conflicts with “Stable must not be
edited directly.” A Hermes adapter should either expose the Stable tree
read-only to the Hermes process or treat such a write as external corruption;
using `external_dirs` avoids per-Skill link churn but does not itself provide
write protection. Do not update Hermes' Hub/bundled manifests for Manager-owned
Skills.

## OpenCode

### Confirmed contracts

- OpenCode's official documentation defines Agent Skills as one directory per
  name with a `SKILL.md`. It publishes the same canonical name regex used by
  this repository, `^[a-z0-9]+(-[a-z0-9]+)*$`, and requires the frontmatter name
  to equal the containing directory name. Source:
  [OpenCode Agent Skills](https://opencode.ai/docs/skills/).
- On Linux, its native global Skill location is
  `~/.config/opencode/skills/<name>/SKILL.md`. It also scans
  `~/.agents/skills/<name>/SKILL.md` and
  `~/.claude/skills/<name>/SKILL.md`, plus the corresponding project-local
  roots. A plain `SKILL.md` package needs no additional manifest or registry.
  Source: [OpenCode Agent Skills](https://opencode.ai/docs/skills/).
- `OPENCODE_CONFIG_DIR` can select an additional config directory, while the
  normal Linux global config is `~/.config/opencode/`. Target detection should
  therefore record the effective config root instead of assuming the default
  in every process. Source: [OpenCode Config](https://opencode.ai/docs/config/).
- Symlink traversal is explicit in first-party source: the Skill glob scanner
  passes `symlink: true`, and discovers native OpenCode, `.agents`, `.claude`,
  configured path, and configured URL sources. This is direct evidence that a
  linked Skill directory is supported by discovery, not merely an inference
  from generic filesystem behavior. Sources:
  [scanner](https://github.com/anomalyco/opencode/blob/f12e14cf1640cbf0dfb6b1ff425b2daaef459eec/packages/opencode/src/skill/index.ts#L142-L171)
  and
  [source selection](https://github.com/anomalyco/opencode/blob/f12e14cf1640cbf0dfb6b1ff425b2daaef459eec/packages/opencode/src/skill/index.ts#L173-L233).
- Custom slash commands and plugins are separate OpenCode mechanisms. Commands
  are Markdown files under `~/.config/opencode/commands/`; local JavaScript or
  TypeScript plugins live under `~/.config/opencode/plugins/`, while npm plugins
  are declared in `opencode.json` and installed/cached by Bun. Neither is
  required for an Agent Skill. Sources:
  [Commands](https://opencode.ai/docs/commands/) and
  [Plugins](https://opencode.ai/docs/plugins/).

### Design consequence

OpenCode is a valid local Managed Symlink Target at
`~/.config/opencode/skills/<name>`. However, a Skill already installed in the
same host's `~/.agents/skills/` is already visible to OpenCode. Installing the
same canonical name into both roots produces duplicate discovery (the source
logs a duplicate-name warning and one definition wins). The topology model
should therefore treat the OpenCode-native target and its `.agents`/`.claude`
compatibility roots as overlapping exposure, not as three independent copies.

## Tencent WorkBuddy desktop: unresolved filesystem contract

“WorkBuddy” is ambiguous. The most likely product is Tencent WorkBuddy, which
Tencent describes as a desktop AI-agent workbench with authorized local-file
access. The user should confirm that this is the product running at home.
Source: [official overview](https://www.workbuddy.ai/docs/zh/workbuddy/Overview).

The first-party product documentation confirms only a GUI lifecycle:

- the Skills UI lists installed local Skills and can upload/import a local
  Skill package; the product then completes configuration automatically;
- installation can run a security check, with manual confirmation for high-risk
  results;
- Skills can be created, enabled, disabled, and fully uninstalled on that
  machine.

Sources: [Skills Market](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market),
[Settings and Skill security](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Setting),
and [Create Skills](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Practice-Cases/Create-Skills).

No first-party product contract was found for the imported package's internal
schema, an Agent Skills conformance level, a default Skill/config filesystem
path, a CLI/API installer, a registry/manifest, or directory-symlink traversal.
Marketing text that says it is compatible with “龙虾 Skills” and a UI example
showing `SKILL.md` are insufficient to turn any particular path or package
layout into a supported contract.

Tencent's first-party
[`workbuddy-bench`](https://github.com/Tencent/workbuddy-bench) repository uses
`.agents/skills/.../SKILL.md` and links those fixtures into `.claude/skills/`,
but its README says the benchmark targets skill-aware agents such as Claude
Code and CodeBuddy Code. It is a benchmark, not the WorkBuddy desktop
installer, so its paths cannot be projected onto the product.

The official desktop install guides cover
[Windows 10+](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Installation-Win-Guide)
and
[macOS 12+](https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Installation-Mac-Guide),
not Linux/NAS. Until a public path contract or an inspected local installation
proves otherwise, Skill Manager should export a package and require manual GUI
import on the WorkBuddy device. It must not include a different home device in
the current host's filesystem-atomic batch.

## Cross-device boundary

A filesystem target is local to the host on which Skill Manager runs. Hermes
and OpenCode running on a NAS can use the contracts above only when Skill
Manager (and the Stable repository or a checked-out release) also runs on that
NAS. A link created on the development machine cannot install a Skill on the
NAS or home computer. Cross-device publication is therefore two operations:

1. publish/synchronize a versioned Stable package through an explicit transport
   such as Git; and
2. run the target adapter locally on each device (or manually import into
   WorkBuddy).

Remote transport, remote locks, and remote rollback must not be implied by the
local full-batch atomicity guarantee.
