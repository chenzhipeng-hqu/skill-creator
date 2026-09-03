# Personal Skill Repository

This context defines the lifecycle language used to develop, release, and install personal skills, along with terms owned by individual repository skills.

## Language

### Skill Lifecycle

**Development Skill**:
A skill version under active development in a repository-root directory named after the skill. It is not an installation source.
_Avoid_: Draft Skill, installed skill

**Skill Name**:
The canonical lowercase ASCII identifier shared by a Skill directory and its `SKILL.md` name, matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
_Avoid_: Display name, directory label

**Active Workset**:
The repository-root collection of Development Skills that currently have planned or ongoing work. It is not a permanent archive of every skill's development copy.
_Avoid_: Drafts directory, skill archive

**Stable Skill**:
A release snapshot created by a validated Promotion and the only repository source eligible for installation. Its current Validation State remains observable and may become invalid after Promotion.
_Avoid_: Finished Skill, development copy

**Promotion**:
The validated transition that updates a Stable Skill from its same-named Development Skill.
_Avoid_: Installation, deployment

**Release Recording**:
An explicitly requested local Git record of a Promotion, consisting of one commit and immutable annotated tag per promoted Skill. It is part of the Atomic Batch but never includes remote publication.
_Avoid_: Promotion, push, transaction backup

**Installation**:
The exposure of a Stable Skill to an Agent Target without changing either the Development Skill or Stable Skill.
_Avoid_: Promotion, publication

**Agent Target**:
A supported Agent environment in which Stable Skills can be made discoverable.
_Avoid_: Development directory, skill repository

**Skill Presence**:
The observed existence of a Development Skill or Stable Skill, independent of whether its contents currently validate. Invalid contents remain visible rather than being treated as absent.
_Avoid_: Validity, discovery success

**Package Identity**:
The canonical content identity shared by comparison, diff, Promotion planning, and Promotion verification for a Skill. It includes all non-ephemeral package content and executable bits, excludes incidental filesystem metadata, and never follows symbolic links.
_Avoid_: Raw directory equality, timestamp equality

**Atomic Batch**:
All selected changes from one Skill Manager command and one operation type, treated as a single recoverable transaction. A failed batch leaves no requested mutation applied, and an interrupted batch must recover before another mutation starts.
_Avoid_: Partial batch, multi-command workflow

**Recovery Required**:
The condition in which an interrupted Atomic Batch has not yet returned to its complete pre-transaction state. Read-only inspection remains available, but new mutations are forbidden until recovery succeeds.
_Avoid_: Partial success, completed batch

**Lifecycle State**:
The relationship between Development and Stable presence and, when both exist, their Package Identity. It is reported independently from Validation State and Installation State.
_Avoid_: Validity, installation status

**Validation State**:
The current structural and test outcomes for a Development Skill or Stable Skill. It does not change or conceal Lifecycle State; a test outcome may be not-run or not-applicable.
_Avoid_: Lifecycle State, Installation State

**Skill Test Entrypoint**:
The deterministic repository-owned `tests/<skill-name>/run` command used to test one Development Skill before Promotion. It is mandatory for Skills with executable scripts and optional for instruction-only Skills.
_Avoid_: Ad hoc test command, installed test

**Installation State**:
The relationship between a Stable Skill and its destination in one or more Agent Targets. It is reported independently from Lifecycle State and Validation State.
_Avoid_: Promotion status, validity

**Managed Installation**:
A direct symbolic link from an Agent Target's Skill Name to the exact Stable Skill path owned by this repository. Resolution through an alias or intermediate symbolic link is not repository-managed.
_Avoid_: Equivalent resolved path, copied installation

### C File Formatting

**Managed C File**:
A self-authored `.c` or `.h` file that is eligible for the project's shared layout rules. Vendor, third-party, and generated files are not Managed C Files unless explicitly brought under local ownership.
_Avoid_: Component file, arbitrary C file

**C File Template**:
The canonical, non-component-specific skeleton used to create a new Managed C File.
_Avoid_: Component template, style sample

**Style Format**:
The canonical mechanical layout of a Managed C File, applied without changing its program semantics.
_Avoid_: Rewrite, refactor

**Style Check**:
A read-only determination of whether a Managed C File already has the canonical Style Format.
_Avoid_: Syntax check, compile check
