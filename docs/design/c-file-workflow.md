# C File Workflow Design

Status: accepted

## Purpose

`c-file-workflow` creates, formats, checks, and helps migrate self-authored C source and header files. It owns mechanical presentation plus the project's Pub/CB/Ops component conventions, while keeping formatting, structural checks, and behavior-aware migration distinct.

Vendor, third-party, generated, and externally maintained sources are excluded unless an exact file is explicitly selected and treated as locally owned.

## Repository layout

```text
sk-c-file-workflow/
├── SKILL.md
├── agents/openai.yaml
├── assets/
│   ├── template.c
│   ├── template.h
│   ├── plain.c
│   ├── plain.h
│   └── clang-format.yaml
├── references/
│   ├── component-patterns.md
│   └── register-structures.md
└── scripts/
    └── c_file_workflow.py

tests/c-file-workflow/
├── run
└── fixtures/
    ├── formatting/
    └── creation/
```

Development begins in `sk-c-file-workflow/` and remains unpublished until the current Skill Manager validates, tests, and promotes it to `skills/c-file-workflow/`. Stable contents are never edited or populated manually.

## Command dispatch

- A bare basename creates a `.c`/`.h` pair with Component Structure.
- `--plain` changes bare-basename creation to a generic `.c`/`.h` pair.
- `--new <basename>.c` or `--new <basename>.h` creates one generic file explicitly.
- An existing `.c` or `.h` path selects in-place Style Format.
- A nonexistent suffixed path without `--new` is an error.
- `--check` changes existing-file processing to read-only Style Check.
- `--check --component` additionally performs Component Structure Check on a same-directory pair with the same basename. Supplying either member automatically discovers its mate; supplying both is allowed and deduplicates to one pair.
- `--check --component --register` performs Style, Component Structure, and Register Structure checks in that order.
- Directory input requires `--recursive`.
- Version 1 does not support standard input, line ranges, editor integration, or JSON output.

One invocation performs exactly one operation: creation, formatting, or checking. Creation inputs cannot be mixed with existing-file inputs. `--plain` applies only to bare-name pair creation; `--new` applies only to explicit single-file creation; and `--check` is mutually exclusive with `--plain`, `--new`, and `--force`. The `--component`, `--register`, and `--full` options require `--check`, while `--recursive` requires directory input. Invalid combinations fail as usage errors with exit status `2`.

Creation names match `^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$`, contain no directory component, and use `--output` for their destination. Snake-case filenames map mechanically to a lower-camel C symbol prefix, such as `board_cfg` to `boardCfg`, while macro prefixes remain upper snake case; no acronym dictionary changes that mapping. Missing output directories are created automatically. `--force` replaces conflicting requested targets without a second interactive prompt and reports each replacement.

Only lowercase `.c` and `.h` suffixes are Managed C File inputs. Uppercase `.C` is not accepted as C because common toolchains interpret it as C++.

## Creation templates

Default pair creation supplies one static instance; Pub/CB/Ops types; callback registration; public access, status, init, and routine functions; and these minimum states:

```c
enum {
    ST_IDLE = 0,
    ST_INIT,
    ST_END,
    ST_FINISH,
    ST_TIMEOUT,
    ST_ERROR,
    ST_MAX,
};
```

The default public API retains the established shape:

```c
xxxPub_t *xxx_getPub(uint8_t id);
int xxx_setCallback(xxxPub_t *pub, const xxxCB_t *cb);
int xxx_status(xxxPub_t *pub);
int xxx_init(void);
int xxx_routine(void);
```

Every public function in the generated pair has this Doxygen shape even when a tag has no applicable value:

```c
/**
  * @brief
  * @param
  * @retval
  */
```

The default concrete callback and operations members retain the established shape:

```c
struct _xxxCB {
    int (*start)(xxxPub_t *pub);
    int (*done)(xxxPub_t *pub);
};

struct _xxxOps {
    int (*init)(xxx_t *me);
};
```

The internal component struct presets `pub`, `cb`, `ops`, `start` and `busy` flags, `tick`, a component-specific work tick such as `uartTick`, `state`, `id`, `cmd`, and `loglvl`. The header supplies the corresponding `XXX_CMD_NONE` and `XXX_CMD_MAX` values. It does not invent a pointer or accessor for another module whose identity is not known at creation time.

The state enum is contiguous, starts with `ST_IDLE = 0`, and ends with the non-dispatched `ST_MAX` sentinel. `ST_END` is an ordinary dispatched state. Additional states may be inserted between existing non-sentinel states when their handlers are inserted at the same positions. Dispatch uses the file-local function-pointer type and ordered table:

```c
typedef int (*handler_t)(xxx_t *me);

static const handler_t xxx_handler[ST_MAX] = {
    st_idle,
    st_init,
    st_end,
    st_finish,
    st_timeout,
    st_error,
};
```

The table uses positional entries rather than designated initializers. After case folding, each handler function name must exactly equal its corresponding state enumerator name, including underscores: for example, `ST_WAIT_RX` maps to `st_wait_rx`. The table contains exactly `ST_MAX` entries in enum order. The round-robin routine retains the established increment-before-selection order. Explicit single-file creation uses a generic source or header skeleton and does not invent partial Component Structure. Templates carry no unrelated example declarations, functions, business commands, or hardware operations.

The public accessor returns `NULL` for an out-of-range instance ID. Callback registration returns an error for a null public pointer and accepts a null callback pointer to unregister callbacks. Initialization returns immediately when an operations initializer fails; it does not attempt generic hardware rollback.

System headers use angle brackets and precede quoted local headers, with one blank line between the groups. Pair creation includes its generated header after applicable system headers. A generic source-only template leaves its include section empty. A header template includes `<stdint.h>` and `<stdbool.h>` and uses a double-underscore guard such as `__UART_H__`.

This include convention is mandatory for newly created files. Formatting and Style Check do not convert delimiters or reorder existing includes because doing so can change compilation semantics. Component Structure Check reports deviations as warnings rather than definite structural violations.

New headers use `@addtogroup Project`. Creation fills `@file`, `@version V1.0.0`, the current Asia/Shanghai date in `DD-Mon-YYYY` form, an explicitly provided author or `chenzhipeng3472`, an empty `@brief`, and a copyright footer containing the creation year.

Generated templates are self-contained and compile cleanly as C11 with host `gcc -std=c11 -Wall -Wextra -Werror -c`; they contain no platform tick calls, other-module accessors, or unresolved example symbols. Target builds and hardware tests remain separate evidence.

Cross-module dependencies are added only when known. A target symbol prefix such as `source` produces `sourcePub_t *pSourcePub`, while `powerMeter` produces `powerMeterPub_t *pPowerMeterPub`. Architecture examples involving GPIO, callbacks, or other modules live in `references/component-patterns.md` rather than generated templates.

## Formatting

The default formatter configuration is `assets/clang-format.yaml`; only an explicit style path overrides it. Version 1 requires clang-format 17.x and supports Linux. A missing or incompatible executable fails with installation guidance.

The default executable is found through `PATH`; `--clang-format PATH` may select another executable for multi-device use and test injection, but the selected binary must still report major version 17.

The default layout:

- uses four spaces and no tabs;
- limits lines to 100 columns;
- binds pointer stars to variables, for example `const uartCfg_t *cfg`;
- expands short functions and control-flow blocks;
- retains at most one consecutive blank line;
- aligns consecutive declarations, assignments, and initializer entries without crossing blank lines;
- keeps preprocessor `#` characters in column zero with `IndentPPDirectives: None`;
- does not sort includes or reflow comments.

Templates and Golden Formatting Fixtures must be idempotent under clang-format 17. Formatting uses clang-format directly rather than Python text post-processing.

An explicit `--style PATH` overrides the clang-format configuration for the current Style Format or Style Check only. It never changes Component Structure or Register Structure rules. A successful formatting operation proves only mechanical layout; it does not prove C syntax, compilation, target behavior, or hardware behavior. Errors reported by clang-format itself terminate processing with exit status `2`.

## Checks and migration

Style Check reports only mechanically correctable differences, emits a concise unified diff, and never writes. The default diff content limit is 1 MiB or 10,000 lines while still listing every nonconforming file; `--full` removes the content limit. Exit status is `0` for clean, `1` for formatting or definite structural violations, and `2` for usage, dependency, or I/O errors.

Human diagnostics, command help, warning labels, and diff headings are English. A file whose formatted bytes are unchanged is not rewritten, preserving its modification time and inode, and is reported as `unchanged`.

After successful input and dependency preflight, checks collect and report every determinable finding rather than stopping at the first violation. Dependency, encoding, I/O, and analysis-blocking failures terminate the affected operation because later findings would not be trustworthy.

A check that produces warnings but no formatting or definite structural violations exits with status `0`. Warnings remain visible and never silently upgrade the claimed proof level.

Component Structure Check requires the public struct as the internal struct's first member; callback and operations types; a static instance array; a state enum; init, routine, status, public-accessor, and callback-registration functions; and matching pair types. Component identifiers are derived mechanically from the file basename, so `power_meter.c/.h` requires names such as `powerMeter_t`, `powerMeterPub_t`, `powerMeterCB_t`, `powerMeter_handler`, and `powerMeter_getPub()`. Extra states, callback and operations members, internal fields, and public APIs are allowed beyond the required minimum contract.

For an explicit component-file input, the checker discovers the same-directory mate automatically. When both members are supplied, they identify one deduplicated pair. During recursive Component Structure Check, an unpaired `.c` or `.h` file is a definite violation; checking continues for other discoverable pairs and the aggregate result exits with status `1`.

The source major sections are `include`, `define`, `typedef`, `function prototypes`, `variable`, and `function`, in that order. Within `function`, the required ordered subsections are `ops function`, `static function`, and `public function`. The header major sections are `include`, `define`, `typedef`, `function prototypes`, `inline`, and `variable`, in that order. Sections may be empty, but missing or out-of-order sections are definite violations.

Every public function requires a Doxygen block containing `@brief`, `@param`, and `@retval`, including empty tags for functions with no parameters or no return value. Static functions and state handlers do not require Doxygen. Comment accuracy and include-convention deviations are warnings for Agent review.

The checker also verifies that state enumerators are contiguous from `ST_IDLE = 0` through a final `ST_MAX` sentinel, that the ordered handler array follows the `<symbol-prefix>_handler` name, that it is declared `static const` with the file-local `handler_t`, that its declared bound and entry count equal `ST_MAX`, and that every non-sentinel enumerator has an exactly case-insensitive matching handler in the same position. It uses a Python-standard-library token scanner that skips comments, strings, character literals, and preprocessor lines rather than depending on `pycparser`. If requested structure cannot be analyzed reliably, the command reports `unable to analyze component structure` and exits with status `2`; warnings are reserved for recognized structure whose semantics cannot be proven.

`--check --register` accepts one or more individual C or header files without requiring a pair and adds deterministic Register Structure checks for one-byte value/bit unions, one-byte size assertions, eight-bit field width, and explicit handling of multi-byte encoding. Facts requiring a datasheet remain warnings for Agent review rather than claimed validation. Only `--component` requires a matched source/header pair. When both structure options are present, Style Check runs first, Component Structure Check runs on the pair second, and Register Structure Check runs on applicable definitions last; any definite violation makes the aggregate result exit with status `1`.

Neither formatting nor checking inserts Component Structure. Existing files are converted only through an Agent-guided Component Migration that reads and preserves behavior, then runs Style Format, Style Check, Component Structure Check, relevant focused and aggregate host tests, and an available target build. Host and target-build evidence is never reported as hardware validation.

Default `status()` returns `-1` for a null public pointer, `1` when command, state, or busy flags indicate activity, and `0` otherwise. Empty generated state handlers explicitly cast their parameter to void before returning success so strict unused-parameter warnings remain enabled. The implementation includes `<stddef.h>` before its quoted local header when it uses `NULL`.

## Register Structure

Register Structure rules apply to any documented external-chip register map independently of SPI, I2C, UART, parallel, or another transport. Command-only devices without registers are exempt. Python does not invent register contents; an Agent uses the device documentation and `references/register-structures.md`.

The retained rules require one-byte value/bit unions for 8-bit registers, LSB-to-MSB fields, preserved reserved bits, unshifted field values, reset-value initialization, `.value` bus transfers, type reuse for shared layouts, explicit multi-byte encoding, size assertions, and known-value tests. Host layout checks do not replace target-toolchain or hardware evidence.

## Filesystem safety

Formatting first computes every requested result. Only after all inputs succeed does it atomically replace each regular file while preserving mode, UTF-8 BOM choice, and a consistently used LF or CRLF convention. Mixed-newline inputs and non-UTF-8 inputs fail with exit status `2` without transcoding. Formatted files end with exactly one newline. New files use UTF-8 without BOM, LF, and exactly one final newline.

Handled failures do not leave partial requested changes, but the formatter does not claim journaled power-loss recovery. Newly created output directories are removed after failure only when they remain empty; pre-existing and nonempty directories are preserved.

Symbolic links are rejected unless `--follow-symlinks` is explicit. That option preserves the link and updates its resolved regular-file target; dangling, cyclic, and non-regular targets fail without modification.

Repeated inputs and symbolic-link aliases that resolve to the same regular file are deduplicated by file identity. The file is processed once and the command emits a warning. A regular file with a hard-link count greater than one is rejected with `hard-linked file is not supported` and exit status `2`, because atomic replacement cannot preserve unseen hard-link aliases.

Recursive scans skip all hidden directories and directories whose complete basename is `.git`, `.hg`, `.svn`, `build`, `out`, `dist`, `third`, `third_party`, `third-party`, `vendor`, `generated`, or `gen`. Complete-name matching does not exclude names such as `builder` or `vendor_app`.

Repeatable `--exclude` patterns use POSIX `/` paths and glob syntax relative to each scan root. Patterns may match files or directories; a matching directory is not traversed. User exclusions may add exclusions but never re-include built-in exclusions. An explicitly selected file under an excluded or hidden directory is processed with a warning.

Recursive scanning never descends into a symbolic-link directory, even with `--follow-symlinks`; that option applies only to symbolic-link files selected explicitly or encountered within an otherwise traversed directory. This prevents directory cycles and unrequested scope expansion.

## Invocation boundary

The Skill is implicitly selected only for explicit requests to create C/H files, create or inspect Pub/CB/Ops components, create or review external-chip drivers, format or check C/H layout, or migrate existing files to Component Structure. Ordinary logic edits and bug fixes do not select it merely because they touch C.

The executable Python entrypoint serves humans and Agents directly from the installed Skill directory without changing shell configuration or `PATH`.

The Skill metadata uses display name `C File Workflow`, short description `Create, format, and check self-authored C/H files.`, and a default prompt that asks it to create or process self-authored C/H files and, when requested, check Pub/CB/Ops Component Structure and Register Structure.

Normal results, `unchanged` messages, and diffs go to standard output. Warnings and errors go to standard error. Version 1 emits no color codes, and displayed file paths are relative to the current working directory whenever representable.

## Testing

`tests/c-file-workflow/run` is the deterministic repository test entrypoint. Golden fixtures live outside the installed Skill under `tests/c-file-workflow/fixtures/`, with formatting input/expected pairs and expected creation outputs. Tests cover formatting idempotence, check immutability and exit statuses, creation, conflicts, recursive exclusions, hidden directories, symlink and alias policy, encoding and newline preservation, mixed-newline rejection, multi-file failure, component structure diagnostics, custom style selection, and clang-format version errors.

Creation does not invoke a compiler. The Skill's own tests compile generated templates as C11, while an Agent runs appropriate host and target builds in the destination project when those environments are available.

## Migration

The existing ICT template links remain unchanged until `c-file-workflow` has been created, tested, and promoted. They then move to `skills/c-file-workflow/assets/template.c` and `.h`. Retirement and deletion of `gd32-component-creator` are manual user actions outside this workflow.

## Open decisions

None. Shared understanding was explicitly confirmed before implementation.
