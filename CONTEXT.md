# Personal Skill Repository

This context defines the lifecycle language used to develop, release, and install personal skills, along with terms owned by individual repository skills.

## Language

### Skill Lifecycle

**Development Skill**:
A Skill version under active development in its mapped Development Directory. It is the only editable source and is not an installation source.
_Avoid_: Draft Skill, installed skill

**Development Directory**:
The repository-root directory named `sk-<Skill Name>` that contains one Development Skill. The `sk-` prefix marks development location and is not part of the Skill Name.
_Avoid_: Skill Name, Stable Skill

**Skill Name**:
The canonical lowercase ASCII identifier shared by `SKILL.md`, Stable and test directories, CLI selection, and Agent installation, matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`. Names beginning with the reserved Development Directory prefix `sk-` are invalid.
_Avoid_: Display name, directory label

**Active Workset**:
The repository-root collection of Development Skills that currently have planned or ongoing work. It is not a permanent archive of every skill's development copy.
_Avoid_: Drafts directory, skill archive

**Stable Skill**:
A release snapshot created by a validated Promotion and the only repository source eligible for installation. Its current Validation State remains observable and may become invalid after Promotion.
_Avoid_: Finished Skill, development copy

**Stable Drift**:
A change in a Stable Skill's Package Identity after its last successful Promotion. It may affect every linked Agent immediately but is neither Development work nor a recorded release.
_Avoid_: Modified Development Skill, Promotion

**Promotion**:
The validated transition that updates a Stable Skill from its mapped Development Skill.
_Avoid_: Installation, deployment

**Promotion Receipt**:
The local durable record of the Package Identity produced by the latest successful Promotion. It establishes Stable provenance independently from optional Git Release Recording.
_Avoid_: Git tag, transaction journal

**Release Recording**:
An explicitly requested local Git record of a Promotion, consisting of one commit and immutable annotated tag per promoted Skill. It is part of the Atomic Batch but never includes remote publication.
_Avoid_: Promotion, push, transaction backup

**Release Backfill**:
An explicitly requested, recoverable local Git commit and immutable annotated tag created after a completed Promotion. It is bound to the exact current Promotion Receipt and labeled as a backfill rather than represented as the original Promotion's Release Recording.
_Avoid_: Release Recording, empty commit, arbitrary tag

**Installation**:
The exposure of a Stable Skill to an Agent Target without changing either the Development Skill or Stable Skill.
_Avoid_: Promotion, publication

**Agent Target**:
A supported local Agent environment in which Stable Skills can be made discoverable. A target belongs to the host on which Skill Manager runs and never implies remote installation.
_Avoid_: Development directory, skill repository

**Agent Target Adapter**:
The repository-owned implementation of one Agent Target family's detection, Skill-root, installation-state, locking, and duplicate-exposure rules.
_Avoid_: Arbitrary path, remote installer

**Skill Presence**:
The observed existence of a Development Skill or Stable Skill, independent of whether its contents currently validate. Invalid contents remain visible rather than being treated as absent.
_Avoid_: Validity, discovery success

**Package Identity**:
The canonical content identity shared by comparison, diff, Promotion planning, and Promotion verification for a Skill. It is derived from portable normalized paths, regular-file bytes, and executable bits; empty directories and incidental filesystem metadata are excluded, and symbolic links are never followed.
_Avoid_: Raw directory equality, timestamp equality

**Atomic Batch**:
All selected changes from one Skill Manager command and one operation type, treated as a single recoverable transaction. A failed batch leaves no requested mutation applied, and an interrupted batch must recover before another mutation starts.
_Avoid_: Partial batch, multi-command workflow

**Transaction Journal**:
The durable immutable plan and append-only event record that makes an Atomic Batch recoverable across interruption. It is temporary operational state, not release history.
_Avoid_: Promotion Receipt, Git history

**Operation Report**:
The versioned human or machine-readable account of one manager command, including its host, results, and diagnostics.
_Avoid_: Transaction Journal, test log

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
The deterministic repository-owned `tests/<skill-name>/run` command used to test one Development or Stable Skill. It is mandatory when a Skill contains managed script content and optional for instruction-only Skills.
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
The canonical skeleton selected to create a new Managed C File. Pair creation uses Component Structure by default, while explicit single-file creation uses the applicable source or header skeleton.
_Avoid_: Style sample

**Component Structure**:
The project-specific Pub/CB/Ops, static-instance, and state-machine organization available to Managed C Files. It is an architectural contract rather than mechanical formatting.
_Avoid_: Style Format, indentation

**Component Structure Check**:
A read-only assessment of whether a source/header pair satisfies the deterministic parts of Component Structure. Definite violations are errors, while semantic properties that cannot be proven are warnings.
_Avoid_: Style Check, compile check

**Component Migration**:
An Agent-guided, behavior-aware change that reshapes existing source and header files into Component Structure before mechanical formatting and checking.
_Avoid_: Style Format, automatic repair

**Module Public Handle**:
An internal pointer to another module's public interface, added only when that concrete dependency exists. Its field name is `p` plus the target module's Pascal-case symbol prefix plus `Pub`.
_Avoid_: Generic dependency placeholder, pXxxPub

**Register Structure**:
A C representation of an external chip's documented registers using structures, unions, constants, enumerations, and layout assertions independently of the transport used to access them. A command-only device without a register map has no Register Structure requirement.
_Avoid_: SPI register, I2C register, transport structure

**Style Format**:
The canonical mechanical layout of a Managed C File, applied without changing its program semantics.
_Avoid_: Rewrite, refactor

**Style Check**:
A read-only determination of whether a Managed C File already has the mechanically enforceable Style Format. It reports only differences that the formatter can automatically correct.
_Avoid_: Syntax check, compile check

**Golden Formatting Fixture**:
A representative C and header example whose expected layout makes the Style Format reviewable and regression-testable.
_Avoid_: Template, arbitrary sample
