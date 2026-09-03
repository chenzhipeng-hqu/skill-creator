# Skill Manager

The Skill Manager is the repository-owned interface for moving skills through development, stable release, and Agent installation states. Its accepted location is `tools/skill_manager.py`.

## Managed locations

```text
<repo>/<skill-name>/          development source
<repo>/skills/<skill-name>/   stable release snapshot
<agent-root>/<skill-name>     installed link to the stable snapshot
```

Development and stable directories with the same skill name may coexist while work is active. Edit only the development source; update the stable snapshot through Promotion; install only from `skills/`. Keep only active work at the repository root.

A valid Skill Name matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII letters and digits, optionally separated by single hyphens. It cannot start or end with a hyphen or contain consecutive hyphens. The directory name and the `name` field in `SKILL.md` must match exactly.

At the repository root, every non-reserved entry with a valid Skill Name is a Development Candidate even when it is not a directory or is a symbolic link. A real directory with an invalid name is also a Candidate when it contains `SKILL.md` or `agents/openai.yaml`, allowing validation to report the bad name instead of hiding the attempted Skill. Hidden entries and the reserved infrastructure names `skills`, `tools`, `tests`, and `docs` are excluded. Under the dedicated `skills/` namespace, every direct entry is a Stable Candidate regardless of its name or file type. Candidate discovery uses `lstat`-style presence checks and never follows a symbolic link before validation.

## Lifecycle states

| Development | Stable | Content relationship | State |
| --- | --- | --- | --- |
| present | absent | n/a | `unpublished` |
| absent | present | n/a | `stable` |
| present | present | different | `modified` |
| present | present | identical | `promoted-clean` |

`promoted-clean` means the root copy can leave the active workset when no further work is planned. The manager reports this condition and may recommend cleanup, but does not delete the Development Skill by default.

Lifecycle State, Validation State, and Installation State are orthogonal report dimensions. Discovery reports a present but invalid Development or Stable location with validation diagnostics; invalid contents must not be hidden or treated as absent. Lifecycle State is derived from Development and Stable presence plus their Package Identity, not from whether either copy currently validates.

## Package identity

Comparison, `diff`, Promotion planning, and Promotion verification use one canonical Package Identity implementation. It compares relative paths, file types, file bytes, and executable bits while ignoring incidental filesystem metadata such as timestamps, owners, and groups. Promotion includes every Development file except the repository-owned ephemeral denylist below; unknown package paths are reported but not silently omitted. Package selection never uses `.gitignore`, and Stable Skills reject all symbolic-link entries so an installed snapshot remains self-contained.

```text
__pycache__/
*.pyc
*.pyo
.DS_Store
Thumbs.db
*.swp
*.swo
*~
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
```

Nested `.git/`, other version-control metadata, and any symbolic-link entry are validation failures rather than silently excluded content. The denylist is repository-wide and has no per-Skill override.

The canonical manifest is sorted and hashed with SHA-256. Its public form is `sha256:<hex>` and is included in status, batch plans, recovery state, and JSON reports. Changing the identity algorithm requires a machine-output schema version change.

## Responsibilities

- Discover Development Skills at the repository root and Stable Skills under `skills/`.
- Discover candidate Development and Stable locations independently from validating their contents; exclude repository infrastructure such as `skills/`, `tools/`, `tests/`, and `docs/` from Development discovery.
- Report development, stable, and per-Agent installation state.
- Compare a Development Skill with its same-named Stable Skill.
- Validate required files, metadata, references, and executable scripts.
- Promote one or more validated Development Skills to stable snapshots.
- Install one or more Stable Skills into explicitly selected Agent Targets.
- Uninstall only links that still point to the exact Stable Skill managed by this repository.
- Provide a default multi-select TUI and non-interactive commands for automation.

The manager does not invent skill content, edit a Development Skill, install an unfinished root copy, overwrite destination conflicts, or directly modify unrelated Agent configuration.

Repository-only operations do not require an Agent Target to be detected. Agent detection is required only for Installation, uninstallation, and the installation-focused TUI. Promotion changes the filesystem contents observed through every installed link to that Stable Skill; whether an already-running Agent reloads those contents is target-specific and outside the manager's guarantees.

## Planning and execution

All mutating interfaces share one typed planning and execution core: repository scanning produces state, planning produces a complete batch plan without writing, and execution applies that plan and returns a structured operation report. The CLI and TUI are adapters over this same core. Presentation strings do not serve as the operation model.

Dry-run serializes the same plan that real execution would apply. A batch plan records every requested skill and Agent Target, including no-change, skipped, and conflicting results, so a failure can still produce a complete report.

One command invocation and one operation type define an Atomic Batch. Separate commands are separate transactions; the manager does not combine Promotion and Installation into one workflow transaction.

## Command contract

Run without a subcommand to start the TUI:

```bash
python3 tools/skill_manager.py
python3 tools/skill_manager.py tui --dry-run
```

The non-interactive interface is:

```bash
python3 tools/skill_manager.py list [--json]
python3 tools/skill_manager.py status [SKILL ...] [--agent AGENT] [--json]
python3 tools/skill_manager.py diff SKILL [SKILL ...] [--json]
python3 tools/skill_manager.py validate (SKILL [SKILL ...] | --all) [--stable] [--json]
python3 tools/skill_manager.py promote (SKILL [SKILL ...] | --all) [--record-release] [--dry-run] [--json]
python3 tools/skill_manager.py install (SKILL [SKILL ...] | --all) --agent AGENT [--agent AGENT ...] [--dry-run] [--json]
python3 tools/skill_manager.py uninstall (SKILL [SKILL ...] | --all) --agent AGENT [--agent AGENT ...] [--dry-run] [--json]
python3 tools/skill_manager.py recover [--dry-run] [--json]
```

Supported Agent keys are `codex`, `claude`, and `agents`. Reject a request that selects both `codex` and `agents` when Codex discovers both roots, because it would expose the same skill twice. A skill list and `--all` are mutually exclusive.

Exit status `0` means success or no change, `1` means validation, conflict, or operation failure, and `2` means invalid command-line usage. JSON output is one versioned JSON document per invocation, shared by every non-interactive command; stable diagnostic codes distinguish failures that share exit status `1`. With `--json`, expected validation, conflict, and operation diagnostics are fields in that document, and standard output contains no human-oriented text. JSON diff output contains structured file changes and may include a unified diff string.

## Release recording

`promote --record-release` explicitly adds local Git history to the Promotion Atomic Batch. Before invoking it, an Agent must follow the repository `git-commit` skill and obtain confirmation for the proposed commit scope and messages. Each promoted Skill receives one commit for traceability; the commits include only that Skill's Stable path, never its Development directory, tests, or unrelated worktree content. The construction that preserves full-batch atomicity across multiple per-Skill commits must be resolved before implementation.

Each Skill commit receives an immutable annotated tag. Existing tags are never deleted, moved, or force-updated. Release Recording never fetches, pushes, or mutates remote refs; remote publication is a separate explicit workflow.

Release Recording requires an existing HEAD attached to a local branch, a clean Git index, no merge, rebase, or cherry-pick in progress, and no pre-existing destination tags. It preserves unrelated unstaged and untracked worktree content. A repository without an initial commit must be bootstrapped manually before Release Recording.

## Safety contract

- Perform full-batch validation and destination preflight before any mutation.
- Treat the requested batch as one Atomic Batch. If one skill or destination conflicts, or execution fails, no requested mutation may remain applied.
- Update a stable snapshot atomically and retain the Development Skill by default; report whether it is now `promoted-clean`.
- Recommend removal of a `promoted-clean` Development Skill when no further work is planned, but never remove it implicitly.
- Make `--dry-run` use the same planning path as the real operation without writing.
- Never overwrite a regular file, real directory, or unrelated symbolic link in an Agent Target.
- Report the result for every requested skill and Agent Target.

Handled execution failures trigger immediate rollback of the entire Atomic Batch. Before the first mutation, the manager writes durable recovery state under `<repo>/.skill-manager/transactions/<transaction-id>/`, describing the verified pre-transaction state and planned mutations. Staging and Stable backups also live within this repository-owned transaction directory, which is excluded from Git.

A durably recorded `COMMITTED` journal phase is the only commit point. If the process is interrupted before that point, an intermediate filesystem state may remain temporarily and recovery restores the complete pre-transaction state. After that point, recovery preserves the new state and completes transaction cleanup. Recovery is idempotent and must tolerate interruption during recovery itself.

The durability sequence flushes staged file contents and directories before they become visible, flushes every journal phase before relying on it, and flushes the parent directories affected by link creation, unlink, and rename. This stronger path is required so recovery covers power loss, not only caught exceptions and process termination.

Read-only commands never initiate recovery; they report the unfinished transaction and Recovery Required condition. A mutating command must recover an unfinished transaction before it can scan and plan new work. `recover` exposes that action explicitly, including dry-run and JSON reporting. There is no force or accept-current recovery mode. If recovery cannot finish or the journal is invalid, the manager reports expected and actual identities and refuses new mutations; the operator repairs the identified paths and reruns recovery.

Mutating commands acquire exclusive repository and selected Agent Target locks in a deterministic order, failing without mutation when a cooperating manager already owns one. Read-only commands acquire the corresponding shared locks and return `MANAGER_BUSY` rather than reading an in-progress mutation. Locks do not replace optimistic checks: execution verifies Package Identity and destination preconditions again before mutation, and a non-cooperating external change aborts and rolls back the batch.

After `COMMITTED`, Stable backups are transaction cleanup rather than release history and are removed. If cleanup is interrupted, subsequent recovery preserves the committed Stable contents and resumes cleanup. Git, not the transaction directory, owns durable release history.

## Validation and tests

Structural validation is mandatory for every promoted Development Skill. A Skill containing executable scripts must provide the deterministic, language-independent Skill Test Entrypoint `tests/<skill-name>/run`; an instruction-only Skill may omit it, in which case its test outcome is `not-applicable`. When the entrypoint exists, it is always executed and must pass. Promotion validates and tests the full selected batch before copying any content.

Skill Test Entrypoints run sequentially in sorted Skill-name order. The manager executes the file directly without a shell, uses the repository root as the working directory, supplies `SKILL_NAME`, `SKILL_DEV_DIR`, and `SKILL_REPO_ROOT`, captures standard output and standard error in the operation report, and requires exit status `0` within 300 seconds. A timeout, signal, or nonzero exit status fails full-batch validation.

The manager compares the Development Package Identity before and after each test. A change to managed content fails validation and requires a fresh run; ephemeral denylisted output does not affect identity and is not deleted by the manager. The first implementation does not sandbox the test process, so each test owns cleanup of external resources and other side effects.

Validation State reports structural and test outcomes separately. `status` performs current read-only structural validation but does not execute tests and reports their outcome as `not-run`; `validate` and `promote` execute applicable tests. The first implementation does not cache validation results.

Every Skill explicitly named on the CLI, and every Skill selected by `--all`, belongs to the validation set even when its planned operation is no-change. Any invalid member blocks the Atomic Batch. In the TUI, only rows whose desired Installation State the user actually changed belong to the requested batch; untouched rows do not block unrelated changes.

An invalid Stable Skill remains visible to read-only `status`, `diff`, and `validate`. It cannot be installed, but an exact managed installation link may be uninstalled even when invalid or dangling. A valid, tested Development Skill may replace an invalid Stable Skill through Promotion. The TUI displays invalid Stable state but does not allow selecting it for Installation.

## Diff contract

Human-readable diff output uses unified diffs for text files. Binary files report path, size, and old/new SHA-256 rather than content. Both human and JSON output report additions, deletions, file-type changes, and executable-bit changes; output limits prevent unbounded text patches.

## Managed installation links

Installation creates symbolic links only. A destination is a Managed Installation only when it is itself a symbolic link whose raw target, interpreted relative to the destination when necessary and normalized lexically to an absolute path, directly names `<repo>/skills/<skill-name>`. The comparison does not recursively resolve intermediate symbolic links. An alias or link chain that merely resolves to the Stable Skill is a conflict and is never removed by uninstallation.

## TUI scope

The first implementation of the TUI is an installation interface: it selects Agent Targets, displays Stable Skills and Installation State, and applies the selected installation changes as one batch. Validation, diff, and Promotion remain explicit non-interactive commands until a separate lifecycle TUI is justified.

## Migration status

The current implementation remains at `skills/manage.py` and manages Stable Skill installation state only. It does not yet implement the accepted repository layout, Promotion, batch CLI, or the `tools/skill_manager.py` path; documentation must not treat those capabilities as implemented until the code and tests move together.
