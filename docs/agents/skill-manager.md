# Skill Manager

The Skill Manager is the repository-owned interface for moving skills through development, stable release, and Agent installation states. Its accepted location is `tools/skill_manager.py`.

## Managed locations

```text
<repo>/sk-<skill-name>/       development source
<repo>/skills/<skill-name>/   stable release snapshot
<agent-root>/<skill-name>     installed link to the stable snapshot
```

Mapped Development and Stable directories may coexist while work is active. Edit only `sk-<skill-name>/`; update the Stable snapshot through Promotion; install only from `skills/`. The `sk-` prefix identifies the Development Directory and is not part of the canonical Skill Name. Keep only active work at the repository root.

A valid Skill Name matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII letters and digits, optionally separated by single hyphens. It cannot start or end with a hyphen, contain consecutive hyphens, or begin with the reserved `sk-` Development Directory prefix. A Development Directory is named `sk-<skill-name>` while `SKILL.md`, the Stable directory, tests, CLI input, tags, and Agent installation use the unprefixed Skill Name.

At the repository root, every non-hidden entry beginning with `sk-` is a Development Candidate even when it is not a directory or is a symbolic link. A real root directory is also a Candidate when it contains `SKILL.md` or `agents/openai.yaml`; if it lacks the prefix, validation reports `MISSING_DEVELOPMENT_PREFIX` rather than hiding the attempted Skill. Other root entries are not Development Candidates. Under the dedicated `skills/` namespace, every direct entry is a Stable Candidate regardless of its name or file type. Candidate discovery uses `lstat`-style presence checks and never follows a symbolic link before validation.

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

The canonical manifest contains only managed regular files. Each entry records normalized relative path, executable boolean, byte size, and file SHA-256; entries are sorted by path, serialized as canonical JSON, and hashed again with SHA-256. Its public form is `sha256:<hex>` and is included in status, batch plans, recovery state, and JSON reports. File bytes are never rewritten for identity, writable permission bits are ignored, directory structure is implied by file paths, and empty directories are neither promoted nor hashed. Changing the identity algorithm requires a machine-output schema version change.

Managed paths must be UTF-8 representable, normalized to Unicode NFC, and serialized with `/` separators. Control characters, backslashes, empty segments, `.` segments, and `..` segments are invalid. Two source paths that collide after normalization fail validation. The first implementation does not add the complete Windows reserved-name rules because it supports Linux only.

## Responsibilities

- Discover Development Skills at the repository root and Stable Skills under `skills/`.
- Discover candidate Development and Stable locations independently from validating their contents; exclude repository infrastructure such as `skills/`, `tools/`, `tests/`, and `docs/` from Development discovery.
- Report development, stable, and per-Agent installation state.
- Compare a Development Skill with its same-named Stable Skill.
- Validate required files, metadata, references, and executable scripts.
- Promote one or more validated Development Skills to stable snapshots.
- Install one or more Stable Skills into explicitly selected Agent Targets.
- Uninstall only links that still point to the exact Stable Skill managed by this repository.
- Manage explicitly requested, adapter-supported hook integrations for installed Stable Skills.
- Provide a default multi-select TUI and non-interactive commands for automation.

The manager does not invent skill content, edit a Development Skill, install an unfinished root copy, overwrite destination conflicts, or directly modify unrelated Agent configuration. A Hook Adapter is the narrow exception: it may manage only its documented, exact configuration entry after explicit `hook install` selection.

Repository-only operations do not require an Agent Target to be detected. Agent detection is required only for Installation, uninstallation, and the installation-focused TUI. Promotion changes the filesystem contents observed through every installed link to that Stable Skill; whether an already-running Agent reloads those contents is target-specific and outside the manager's guarantees.

## Planning and execution

All mutating interfaces share one typed planning and execution core: repository scanning produces state, planning produces a complete batch plan without writing, and execution applies that plan and returns a structured operation report. The CLI and TUI are adapters over this same core. Presentation strings do not serve as the operation model.

Dry-run uses the same discovery, Package Identity, structural-validation, target-preflight, and planning paths as real execution, but has no persistent side effects. It does not run Skill Test Entrypoints or Git hooks, and reports applicable test outcomes as `not-run`. It creates no journal, receipt, log, directory, lock file, Git object, ref, or Agent link. It may open an already-existing lock file and hold a shared lock for the duration of inspection, but never creates a missing lock for dry-run. A dry-run plan is a point-in-time preview rather than an executable artifact: real execution repeats discovery, validation, testing, preflight, and planning while holding its exclusive locks.

A batch plan records every requested skill and Agent Target, including no-change, skipped, and conflicting results, so a failure can still produce a complete report.

One command invocation and one operation type define an Atomic Batch. Separate commands are separate transactions; the manager does not combine Promotion and Installation into one workflow transaction.

## Hook integrations

Hook registration is separate from Skill Installation and is always opt-in:

```text
python3 tools/skill_manager.py hook install git-commit --agent codex
python3 tools/skill_manager.py hook status git-commit --agent codex
python3 tools/skill_manager.py hook uninstall git-commit --agent codex
python3 tools/skill_manager.py install git-commit --agent codex --with-hooks
```

Skills declare optional integrations in root `skill-manager.yaml` (`schema_version: 1`), including an integration id, event, optional activation, package-relative script, and per-Agent matcher mapping. Ordinary `install` reports discovered integration ids but never enables them. `install --with-hooks` requires exactly one Skill and one Agent Target, installs the Stable link, then registers its declared optional hook. If the hook registration fails, the Manager removes a link that it created in that invocation; a pre-existing link is preserved. The initial Hook Adapter supports only the declared `git-commit`/Codex `commit-gate`: it requires the exact managed Codex link to an installed Stable Skill, then adds or removes one exact `PreToolUse` command entry in `~/.codex/hooks.json` pointing to the linked script. It never copies the script, edits `config.toml`, overwrites a differing entry, or treats configuration presence as evidence that a particular hook executed. Other Skill/Agent pairs fail with `HOOK_ADAPTER_UNSUPPORTED`.

Successful hook registration writes a local Receipt at `<agent-config-root>/.skill-manager/hook-receipts/<skill>.json`. It records the Agent, linked script, Stable Package Identity, and exact managed configuration entry. `hook uninstall` refuses to delete anything unless that Receipt, the current exact Stable link, and the active configuration entry all match; missing, edited, or stale state is `HOOK_RECEIPT_CONFLICT` or `HOOK_CONFIGURATION_CONFLICT` and remains untouched.

## Machine report contract

Every non-interactive command can emit one JSON document with this common envelope:

```json
{
  "schema_version": 1,
  "command": "promote",
  "dry_run": false,
  "status": "success",
  "changed": true,
  "transaction_id": "uuid-or-null",
  "started_at": "2026-09-03T10:30:00Z",
  "finished_at": "2026-09-03T10:30:02Z",
  "host": {
    "hostname": "orangepi4",
    "platform": "linux"
  },
  "results": [],
  "diagnostics": []
}
```

Envelope status is one of `success`, `no-change`, `failed`, `recovery-required`, or `busy`. Machine report, journal, and release-annotation timestamps are UTC RFC 3339 with `Z`; only the date embedded in a release tag name is derived from the batch's frozen `Asia/Shanghai` timestamp.

Each result shares `skill`, `source`, `target`, and `outcome`, with command-specific data under `details`; fields that do not apply are omitted rather than filled with meaningless null values. Results are ordered by canonical Skill Name, source, and Target key. Each resolved target detail includes its Adapter key, absolute configuration root, and absolute Skill root.

Diagnostics contain `severity`, stable `UPPER_SNAKE_CASE` code, human-readable `message`, optional Skill and Target identities, and a structured `context` object. `message` wording may evolve without changing the code contract. Any error diagnostic produces exit status `1`; warning-only reports retain exit status `0`.

`schema_version`, `journal_schema_version`, and manager-state schema versions are independent integers. An unknown journal version permits only read-only diagnostics and recovery guidance; an active journal is never migrated automatically. With no active transaction, a recognized older `state.json` may be migrated through validated temporary output and atomic replacement. An unknown newer manager-state version blocks mutation.

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
python3 tools/skill_manager.py diff SKILL [SKILL ...] [--full] [--json]
python3 tools/skill_manager.py validate (SKILL [SKILL ...] | --all) [--stable] [--json]
python3 tools/skill_manager.py promote (SKILL [SKILL ...] | --all) [--replace-stable-drift] [--record-release --commit-message-file SKILL=PATH ...] [--dry-run] [--json]
python3 tools/skill_manager.py record-release (SKILL [SKILL ...] | --all) --commit-message-file SKILL=PATH ... [--dry-run] [--json]
python3 tools/skill_manager.py install (SKILL [SKILL ...] | --all) --agent AGENT [--agent AGENT ...] [--dry-run] [--json]
python3 tools/skill_manager.py uninstall (SKILL [SKILL ...] | --all) --agent AGENT [--agent AGENT ...] [--dry-run] [--json]
python3 tools/skill_manager.py recover [--dry-run] [--json]
```

The initial built-in Agent keys are `codex`, `claude`, `agents`, `hermes`, and `opencode`; additional Agent families require an explicit Adapter contract rather than an assumed path. Hard-conflict selections are `codex + agents`, `opencode + agents`, and `opencode + claude`, because the corresponding consumer discovers both roots and the same Skill would appear twice. Other target pairs may emit topology warnings but are not hard conflicts without evidence that one consumer scans both roots. A skill list and `--all` are mutually exclusive.

Exit status `0` means success or no change, `1` means validation, conflict, or operation failure, and `2` means invalid command-line usage. JSON output is one versioned JSON document per invocation, shared by every non-interactive command; stable diagnostic codes distinguish failures that share exit status `1`. With `--json`, expected validation, conflict, and operation diagnostics are fields in that document, and standard output contains no human-oriented text. JSON diff output contains structured file changes and may include a unified diff string.

`list` performs Candidate discovery only. It reports names, Development/Stable presence, and paths without computing Package Identity, validating content, or scanning Agent Targets; only discovery I/O failures make it fail. `status` computes Package Identity, structural validation, Lifecycle State, Stable provenance, and Installation State without running tests. With no `--agent`, `status` scans every detected Target; repeated `--agent` restricts the scan. Repository status still succeeds when no Target is detected.

## Release recording

`promote --record-release` explicitly adds local Git history to the Promotion Atomic Batch. Before invoking it, an Agent must follow the repository `git-commit` skill and obtain confirmation for the proposed commit scope and one caller-supplied message per Skill. Each promoted Skill receives one normal Git commit for traceability; commits are created in canonical Skill Name order and include only that Skill's Stable path, never its Development Directory, tests, or unrelated worktree content. Normal Git hooks run. Other Git readers may temporarily observe the intermediate commit chain before the Atomic Batch reaches `COMMITTED`; a pre-commit-point failure restores the original branch ref and filesystem through journaled, compare-and-swap recovery.

The caller supplies confirmed messages through one `--commit-message-file SKILL=PATH` argument per changed Skill. Each referenced file must be readable UTF-8, non-empty after trimming, and contain no NUL byte. A changed Skill must have exactly one message file; a no-change Skill needs none. Release Recording uses the repository's configured `user.name` and `user.email` for commit and tag identities and fails preflight when either is unavailable; it does not generate or override identities.

Each Skill commit receives an immutable annotated tag named `<skill-name>-YYMMDD-vN`, using `Asia/Shanghai` and a per-Skill, per-day counter starting at `v1`. Batch planning freezes one release instant; every tag in that Atomic Batch uses the date obtained by rendering that instant in `Asia/Shanghai`, even when execution or recovery crosses midnight. The instant is stored in the transaction plan and reused by recovery. Release annotations render the same frozen instant as UTC RFC 3339 with `Z`. The counter advances from the greatest existing value and never reuses a missing number. Tags point to their corresponding per-Skill commits; a later commit and tag in the same batch therefore include earlier sorted Skill changes in their repository snapshot even though each commit diff remains Skill-local. Existing tags are never deleted, moved, or force-updated. Release Recording never fetches, pushes, or mutates remote refs; remote publication is a separate explicit workflow.

The first implementation creates unsigned annotated tags. Each annotation uses this stable, machine-readable body without absolute paths:

```text
Release <skill-name>

Skill: <skill-name>
Package-Identity: sha256:<hex>
Atomic-Batch: <transaction-id>
Recorded-At: <UTC RFC3339 timestamp with Z>
```

The tag object itself records the corresponding commit and tagger, so the annotation does not duplicate them.

Release Recording requires an existing HEAD attached to a local branch, a clean Git index, no merge, rebase, or cherry-pick in progress, and no pre-existing destination tags. It preserves unrelated unstaged and untracked worktree content. A repository without an initial commit must be bootstrapped manually before Release Recording.

A `promoted-clean` or otherwise no-change Skill does not create an empty commit or new tag. Release Recording reports no-change for that Skill.

## Release Backfill

`record-release` creates a later, explicitly identified Git record for a completed Promotion that omitted Release Recording. It selects Stable Skills rather than Development Skills and requires the current Stable Package Identity to match the latest Promotion Receipt exactly. Each selected Skill requires one caller-confirmed `--commit-message-file SKILL=PATH`.

Release Backfill is a separate Git-only Atomic Batch. It runs Stable validation and applicable Skill tests, commits only `skills/<skill-name>/`, and creates the next immutable `<skill-name>-YYMMDD-vN` annotated tag. Its annotation adds `Recording-Mode: backfill`, `Promotion-Transaction`, and `Promoted-At`; it does not alter the original Promotion Receipt or claim to be part of the earlier Promotion transaction.

If a tag already matches the current Receipt identity and Promotion transaction and its commit contains the exact current Stable content, the result is `no-change`. A missing or mismatched Receipt fails closed. When the Stable path has no Git change to commit, the command fails with `RELEASE_BACKFILL_NO_GIT_CHANGES`; it never creates an empty commit or attaches a new tag to an arbitrary existing commit. Dry-run reports a provisional tag without writing the index, refs, journal, or manager state.

Active pre-commit and commit-message hooks run as part of normal Git commit processing, but they may validate only. After each hook, the manager verifies the staged scope and Package Identity; a hook mutation fails and rolls back the Atomic Batch. Release Recording refuses to run when an active post-commit hook is configured because arbitrary post-commit side effects cannot be recovered. Unrelated untracked files created by a hook are preserved and reported rather than deleted.

Dry-run validates the message-file mapping and computes the candidate tag names without creating or reserving refs. It marks tag names as provisional because real execution recomputes the per-day counter under the exclusive repository lock.

## Safety contract

- Perform full-batch validation and destination preflight before any mutation.
- Treat the requested batch as one Atomic Batch. If one skill or destination conflicts, or execution fails, no requested mutation may remain applied.
- Update a stable snapshot atomically and retain the Development Skill by default; report whether it is now `promoted-clean`.
- Recommend removal of a `promoted-clean` Development Skill when no further work is planned, but never remove it implicitly.
- Make `--dry-run` use the same planning path as the real operation without writing.
- Never overwrite a regular file, real directory, or unrelated symbolic link in an Agent Target.
- Report the result for every requested skill and Agent Target.

Handled execution failures trigger immediate rollback of the entire Atomic Batch. Each transaction uses a UUIDv4 directory under `<repo>/.skill-manager/transactions/<transaction-id>/` containing immutable `plan.json`, append-only `events.jsonl`, `staging/`, and `backup/`. The complete plan is durably written before the first mutation; each event is appended and flushed before later logic relies on it. Staging and Stable backups live within this repository-owned transaction directory, which is excluded from Git.

The stable transaction phases are `PREPARED`, `APPLYING`, `ROLLING_BACK`, `ROLLED_BACK`, `COMMITTED`, `CLEANING`, and `CLEANED`. Individual operation events use `PENDING`, `APPLIED`, and `UNDONE`. Only one transaction may exist short of `CLEANED`; multiple active transaction directories are `JOURNAL_CONFLICT` and block automatic recovery.

`plan.json` contains `journal_schema_version: 1` and its canonical SHA-256. Every event contains a monotonic sequence, the prior event hash, its own canonical event hash, type, and details; the first event links to the plan hash. Recovery discards only a syntactically incomplete final event line, then verifies the full hash chain and reconciles every operation against its planned before and after identities. A sequence or hash mismatch, corruption before the final record, or actual state matching neither identity enters Recovery Required rather than guessing.

A durably recorded `COMMITTED` journal phase is the only commit point. If the process is interrupted before that point, an intermediate filesystem state may remain temporarily and recovery restores the complete pre-transaction state. After that point, recovery preserves the new state and completes transaction cleanup. Recovery is idempotent and must tolerate interruption during recovery itself.

The durability sequence flushes staged file contents and directories before they become visible, flushes every journal phase before relying on it, and flushes the parent directories affected by link creation, unlink, and rename. This stronger path is required so recovery covers power loss, not only caught exceptions and process termination.

Read-only commands never initiate recovery; they report the unfinished transaction and Recovery Required condition. A mutating command must recover an unfinished transaction before it can scan and plan new work. `recover` exposes that action explicitly, including dry-run and JSON reporting. There is no force or accept-current recovery mode. If recovery cannot finish or the journal is invalid, the manager reports expected and actual identities and refuses new mutations; the operator repairs the identified paths and reruns recovery.

Mutating commands acquire exclusive repository and selected Agent Target locks in a deterministic order, failing without mutation when a cooperating manager already owns one. Read-only commands acquire the corresponding shared locks and return `MANAGER_BUSY` rather than reading an in-progress mutation. A strict dry-run opens and shares only locks that already exist; it does not create missing lock files, so its report remains a best-effort point-in-time snapshot. Locks do not replace optimistic checks: execution verifies Package Identity and destination preconditions again before mutation, and a non-cooperating external change aborts and rolls back the batch.

The manager handles `SIGINT` and `SIGTERM` by terminating the active test or hook subprocess group, then rolling back an uncommitted Atomic Batch. Test Entrypoints and recoverable Git subprocesses run in their own process groups. A timeout or first termination signal sends `SIGTERM` to the whole group, waits five seconds, and then sends `SIGKILL` if needed. A second termination signal exits immediately and may leave Recovery Required; an uncatchable manager `SIGKILL` is handled by the same next-run recovery path.

After `COMMITTED`, Stable backups are transaction cleanup rather than release history and are removed. Cleanup durably records `CLEANED` and then immediately deletes the complete transaction directory. If deletion or earlier cleanup is interrupted, subsequent recovery preserves the committed state and resumes cleanup or deletion idempotently. Transaction journals and backups are not permanent audit history; Git Release Recording, Promotion Receipts, emitted Operation Reports, and retained logs provide the applicable durable evidence.

Manager-private directories under `.skill-manager/` use mode `0700`; manager-created state, journal, log, and message-metadata files use mode `0600`. The manager never widens existing permissions. A Release Recording journal records only the commit-message file path and SHA-256 plus the resulting commit identity; it never copies the message body into manager state.

## Stable provenance and drift

Stable Skills remain writable; the manager does not apply read-only modes. Development remains the only supported editing source. Every successful Promotion atomically updates the local Promotion Receipt in `<repo>/.skill-manager/state.json` with the Skill Name, produced Package Identity, Atomic Batch ID, and timestamp. The receipt is persistent manager state, not transaction scratch state or Git history.

The current Stable identity matching its latest receipt is `stable_provenance=clean`; a mismatch is `stable_provenance=drifted`. When no receipt exists, the manager may reconstruct it from the newest valid annotated tag whose recorded Package Identity matches the corresponding committed Stable tree. If neither source proves provenance, it reports `stable_provenance=unknown` rather than assuming clean.

Stable Drift may be inspected with `status`, `diff`, and `validate --stable`. A structurally valid drifted Stable may be installed, with `INSTALLING_DRIFTED_STABLE` reported; a structurally valid Stable with unknown provenance may also be installed, with `INSTALLING_UNPROVEN_STABLE` reported. Exact managed links may still be uninstalled. Promotion gives `unknown` provenance the same overwrite protection as drift: it refuses either case unless the caller explicitly supplies `--replace-stable-drift`, and its plan displays the content that will be discarded. Unknown contents are never silently adopted or given a new receipt. Release Recording commits only the Stable content generated from the selected Development Skill in the current batch; runtime edits are never adopted implicitly.

## Validation and tests

Structural validation is mandatory for every promoted Development Skill. A Skill whose `scripts/` tree contains any managed regular file must provide the deterministic, language-independent Skill Test Entrypoint `tests/<skill-name>/run`; an instruction-only Skill may omit it, in which case its test outcome is `not-applicable`. When the entrypoint exists, it is always executed and must pass. Promotion validates and tests the full selected batch before copying any content.

Skill Test Entrypoints run sequentially in sorted Skill-name order and in independent subprocess groups. The manager executes the file directly without a shell, uses the repository root as the working directory, supplies `SKILL_NAME`, `SKILL_UNDER_TEST`, `SKILL_SOURCE_KIND=development|stable`, and `SKILL_REPO_ROOT`, captures standard output and standard error in the operation report, and requires exit status `0` within 300 seconds. On timeout it sends `SIGTERM` to the entire test process group, waits five seconds, and then sends `SIGKILL` if needed. A timeout, signal, or nonzero exit status fails full-batch validation.

The manager compares the Development Package Identity before and after each test. A change to managed content fails validation and requires a fresh run; ephemeral denylisted output does not affect identity and is not deleted by the manager. The first implementation does not sandbox the test process, so each test owns cleanup of external resources and other side effects.

Validation State reports structural and test outcomes separately. `status` performs current read-only structural validation but does not execute tests and reports their outcome as `not-run`; `validate` and `promote` execute applicable tests. The first implementation does not cache validation results.

`validate SKILL` selects only the Development Candidate; `validate SKILL --stable` selects only its Stable Candidate. A missing selected source is an error, and validation never silently substitutes or validates both. `--all` expands all Candidates in the selected namespace, including invalid Candidates.

Structural validation checks only deterministic repository contracts: Candidate type; Skill Name and Development Directory mapping; UTF-8 and YAML/frontmatter parsing; required `SKILL.md` name and non-empty description; required `agents/openai.yaml` structure; internal relative-reference existence and containment; allowed file types; absence of symbolic links and version-control metadata; the Skill Test Entrypoint rule; and valid shebangs on executable scripts. It does not judge prose quality, reject generic TODO text, or fetch external URLs.

All managed Markdown files are parsed for links and images. Fenced and inline code are ignored; external URI schemes are not fetched; local link paths must exist and remain inside the Skill. For `file.md#anchor`, validation checks the file but not the heading anchor in the first implementation. Plain code-formatted paths are not treated as references.

The repository requires `agents/openai.yaml` even though the general Codex Skill format treats it as optional. The file must be a UTF-8 YAML mapping with an `interface` mapping containing non-empty string `display_name` and `short_description`. Optional `icon_small` and `icon_large` are contained relative paths to existing regular files; optional `brand_color` matches `#RRGGBB`; optional `default_prompt` is a non-empty string; and optional `policy.allow_implicit_invocation` is boolean.

Optional `dependencies.tools` is a list of mappings. Each item has `type` equal to `mcp` or `app`, a non-empty string `value`, and only correctly typed public optional fields such as `description`, `transport`, `command`, and `url`; URLs are not fetched. Unknown metadata fields produce `UNKNOWN_OPENAI_METADATA_FIELD` warnings for forward compatibility, while known-field type errors are errors. `dependencies.tools[].optional` is an error because current Codex dependency semantics do not support optional declarations.

Every Skill explicitly named on the CLI, and every Skill selected by `--all`, belongs to the validation set even when its planned operation is no-change. Any invalid member blocks the Atomic Batch. In the TUI, only rows whose desired Installation State the user actually changed belong to the requested batch; untouched rows do not block unrelated changes.

An invalid Stable Skill remains visible to read-only `status`, `diff`, and `validate`. It cannot be installed, but an exact managed installation link may be uninstalled even when invalid or dangling. A valid, tested Development Skill may replace an invalid Stable Skill through Promotion. The TUI displays invalid Stable state but does not allow selecting it for Installation.

## Diff contract

Human-readable diff output uses unified diffs for text files. Binary files report path, size, and old/new SHA-256 rather than content. Both human and JSON output report additions, deletions, file-type changes, and executable-bit changes; output limits prevent unbounded text patches.

Text hunks are limited to 1 MiB or 10,000 lines in total, whichever is reached first. Truncation retains every file summary and hash while omitting later text hunks. Explicit `--full` removes the text-content limit; binary bytes are never emitted.

## Managed installation links

Each Agent Target family is implemented by a repository-owned Adapter that resolves detection, the effective configuration and Skill roots, the target lock, installation inspection, and duplicate-exposure checks. A generic arbitrary-path target is not supported. The built-in contracts are:

```text
codex     ~/.codex                     ~/.codex/skills
claude    ~/.claude                    ~/.claude/skills
agents    ~/.agents                    ~/.agents/skills
hermes    <active HERMES_HOME>          <active HERMES_HOME>/skills
opencode  <effective OpenCode config>   <effective OpenCode config>/skills
```

Hermes uses a valid `HERMES_HOME` for the current process and otherwise defaults to `~/.hermes`; the Adapter reports the resolved active home and does not enumerate other profiles. The manager installs a direct link into that active Skill root and does not modify Hermes Hub, bundled-Skill, or external-directory metadata. Runtime edits through a Hermes link are reported as Stable Drift.

OpenCode installs to `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` and also accounts for its automatic discovery of global `.agents/skills`, `.claude/skills`, and any valid `OPENCODE_CONFIG_DIR` Skill source. Selecting OpenCode together with either compatibility root is a hard conflict. When only OpenCode is selected, an existing same-named Skill in any additional discovery root is `DUPLICATE_EXPOSURE` and blocks creation of a native duplicate; additional roots are inspection sources, not automatic installation destinations.

WorkBuddy is not a built-in target in the first implementation. Its public documentation confirms GUI package import but not a filesystem root, symlink contract, CLI, or installation registry; no path or automatic installation behavior is inferred.

Agent Targets are local to the host running the manager. A Stable version may be synchronized to another device through an explicit external workflow such as Git, but installation, locking, rollback, and recovery run separately on that target device and are never included in the originating host's Atomic Batch. Git synchronization is not wrapped by the manager: target hosts use an explicit linear update such as `git pull --ff-only`, never during an active manager transaction, and then run `status` or `validate --stable` before relying on the result.

An Agent Target is detected only when its Adapter-resolved configuration root exists as a real directory; finding only an executable is insufficient. CLI selection of an undetected target fails with `TARGET_NOT_DETECTED`, and the first implementation has no arbitrary target-root override. The fixed-root Adapter paths are:

```text
codex   ~/.codex          ~/.codex/skills
claude  ~/.claude         ~/.claude/skills
agents  ~/.agents         ~/.agents/skills
```

The Skill root may be created beneath an existing real configuration root. Rollback removes it only when the current transaction created it and it remains empty; successful Installation leaves it in place, and ordinary uninstallation never removes the root. A symbolic-link configuration or Skill root is rejected.

Installation creates only absolute symbolic links that point directly to the selected Stable Skill. A destination is a Managed Installation only when it is itself a symbolic link whose raw target, interpreted relative to the destination when necessary and normalized lexically to an absolute path, directly names `<repo>/skills/<skill-name>`. The comparison does not recursively resolve intermediate symbolic links. An alias or link chain that merely resolves to the Stable Skill is a conflict and is never removed by uninstallation.

The manager never writes third-party installation registries such as `~/.agents/.skill-lock.json`. It owns only exact links it creates. Cooperating managers place their shared/exclusive lock at `<agent-config-root>/.skill-manager.lock`; each selected Target is locked and preflighted independently even when installed entries resolve to common content.

Machine reports and journals identify the local host and resolved target instance. The report envelope includes hostname and platform; each target result includes its Adapter key, absolute configuration root, and absolute Skill root. Absolute host paths never enter release tag annotations.

## Test logs

Operation reports inline at most the final 64 KiB of each Skill Test Entrypoint's standard output and standard error. Complete streams are stored under `.skill-manager/logs/<run-id>/<skill>.stdout` and `.stderr`; report details include the local path, total byte count, SHA-256, truncation flag, and inline tail. Logs are manager-owned local diagnostics and never enter Stable contents, Package Identity, transaction journals, or Git release commits.

Completed logs are retained for at most 30 days and pruned oldest-first when total retained size exceeds 100 MiB. Cleanup preserves the current run, logs referenced by a Recovery Required transaction, and each Skill's most recent failed run. This manager-owned housekeeping never deletes Skill or user-authored files.

## TUI scope

The first implementation of the TUI is an installation interface with three screens: select detected Agent Targets and view their resolved paths; change desired Stable Skill Installation State; then inspect the complete Batch Plan including links, no-change items, warnings, and errors. Errors disable apply. Warnings require the user to type `y`; Enter only advances between screens and never performs mutation by itself. `q` cancels from any screen without writing. Validation, diff, and Promotion remain explicit non-interactive commands until a separate lifecycle TUI is justified.

When Recovery Required exists, the TUI displays the transaction ID, current phase, and the appropriate `recover --dry-run` and `recover` commands, then exits without recovery. Recovery is CLI-only.

## Runtime and implementation structure

Version 1 supports Linux only. It depends on POSIX symbolic links, `fcntl.flock`, directory `fsync`, and Linux-tested interruption semantics; another platform returns `UNSUPPORTED_PLATFORM` rather than silently weakening the Atomic Batch contract.

Before mutation, the manager verifies that the repository, transaction storage, and every selected Agent Target root use a supported local filesystem. Version 1 allows known local Linux filesystems such as `ext4`, `xfs`, `btrfs`, and `zfs`; it rejects NFS, CIFS, SSHFS, other FUSE filesystems, 9p, and unknown types with `UNSUPPORTED_FILESYSTEM`. There is no force option that weakens the locking, durability, or atomic-replacement guarantees.

YAML and Markdown parsing use declared `PyYAML` and `markdown-it-py` dependencies from the repository `pyproject.toml`. The manager uses `yaml.safe_load` and Markdown tokens rather than hand-written YAML or Markdown regex parsers. Missing dependencies produce `MISSING_RUNTIME_DEPENDENCY`; the manager never installs packages or accesses the network automatically.

`tools/skill_manager.py` is a thin entrypoint over `tools/skill_manager_core/`, whose modules separate model, discovery, identity, validation, planning, transaction, Git release, reporting, CLI, TUI, and Agent Target Adapters. The external core interface remains `scan -> plan -> apply`. Transaction code receives internal `FileSystem` and `GitRepository` interfaces; production Adapters use the real OS and Git, while test Adapters inject failures before and after every mutation primitive. These seams are internal and are not user plugin interfaces.

## Verification contract

Completion requires unit tests for manifest identity, validation, planning, and JSON; temporary repository and Target integration tests for Promotion, Installation, Recovery, and Release Recording; failure injection before and after every filesystem and Git mutation; subprocess-group timeout and signal escalation; manager `SIGKILL` recovery and recovery-during-recovery tests; hooks, tag collision, dirty index, Stable Drift, unknown Stable provenance, frozen release timestamps across midnight, state permissions, unsupported filesystems, Agent Target paths, duplicate exposure, strict dry-run, completed-journal deletion, and TUI cancellation tests. Tests must prove that unrelated worktree and destination content remains unchanged.

When two filesystem devices are available, the suite must exercise a real cross-filesystem Atomic Batch and recovery. Environments with only one device report that case as an explicit skip; same-directory temporary tests are not reported as cross-filesystem evidence.

## Implementation status

The current implementation lives at `tools/skill_manager.py` with its core under `tools/skill_manager_core/`; the legacy `skills/manage.py` entrypoint has been removed. The repository test suite currently exercises the implemented lifecycle, reporting, recovery, release, and Agent Target behavior, while environment-dependent verification such as a true cross-filesystem run remains subject to the explicit evidence reported by the test environment.
