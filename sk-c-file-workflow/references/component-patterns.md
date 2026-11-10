# Component Patterns

Read this reference only when extending a generated Pub/CB/Ops component or migrating existing behavior into that structure. The generated template deliberately omits hardware calls and unknown cross-module dependencies.

## Core contract

- The public structure is the first internal-structure member so a public pointer can identify its owning component instance.
- CB contains callbacks supplied by a caller. Passing `NULL` to the generated setter unregisters the callbacks.
- Ops contains internal platform operations and permits test substitution.
- The static instance array owns component lifetime; the public accessor checks its ID before returning a public pointer.
- The routine processes one instance per call and dispatches through the ordered component handler table.

## Command ownership

- Pass a stable, non-NULL caller identity, such as the caller's state-machine address, as `owner` to `cmd()`, `result()`, and `resultPoint()`. The module compares this address but does not dereference it; keep it unique and valid throughout the transaction.
- Only `cmd() == 0` accepts a command and records its owner. A busy or invalid submission must not replace the current owner. Only poll a result after successful submission; `status()` reports instance occupancy, not ownership.
- Both result functions check ownership before execution state. A mismatched owner returns an error without changing the transaction or output arguments. The submitting caller sees `0` while executing and a positive byte length when consuming the result. Invalid result arguments or an undersized copy buffer do not consume it.
- Keep ownership while the result is finished but unconsumed. Clear it on successful consumption, result expiration, execution timeout, error termination, or successful initialization. The next routine call returns a consumed transaction to idle.
- `resultPoint()` consumes the transaction but borrows its internal buffer. Use the data before another command is accepted or the module is reinitialized; ownership does not extend pointer lifetime.
- This contract assumes serialized calls, such as cooperative state machines. Use project-specific synchronization for task/interrupt concurrency. Allow only one outstanding transaction per caller per instance; `owner` does not distinguish old and new transactions from the same caller.

### Calling a command-driven module

- Ensure the module's `routine()` runs regularly to advance execution and expire unclaimed results. The caller may use a switch, a handler table, or another state-machine form; do not prescribe state names.
- `status()` is an optional occupancy query. Busy covers both execution and a finished result awaiting consumption; it does not identify the owner. `cmd()` is authoritative for submission.
- Submit with `cmd(pub, owner, cmd, data)`. Only `0` accepts the transaction; `1` means busy and the command was not accepted, so retry submission later. On `<0`, handle the submission error. Never poll a result for a rejected command.
- After acceptance, poll either `result()` (copy into a caller buffer whose capacity is in bytes) or `resultPoint()` (borrow the internal buffer), using the same owner. A return of `0` means still executing, `<0` means error, and `>0` gives the result length in bytes and consumes it. A too-small copy buffer may be corrected and retried with the same owner while the result remains available.
- A finished but unconsumed result still occupies the instance. After consumption, the next `routine()` call returns it to idle. Use a borrowed result pointer before another command is accepted or the module is reinitialized.
- In a concrete module, write a nonzero result and its byte length within the result buffer's capacity before clearing busy and entering the finish state. The template's `st_xxx()` is a placeholder; `0` cannot represent a successful empty result under this return-value contract.

## Module Public Handles

Add a Module Public Handle only when a concrete dependency exists. Name it `p` plus the target module's Pascal-case symbol prefix plus `Pub`:

```c
sourcePub_t *pSourcePub;
powerMeterPub_t *pPowerMeterPub;
ads131e04Pub_t *pAds131e04Pub;
```

Acquire and validate the handle during initialization according to the target module's real API. Do not generate placeholder calls such as `xxxx_getPub()`.

## Callbacks

The `module` names below are illustrative; use the actual module name and only the callbacks and operations it needs.

Extend the callback type for real events rather than retaining unused demonstrations:

```c
struct _moduleCB {
    int (*start)(modulePub_t *pub);
    int (*done)(modulePub_t *pub);
    int (*error)(modulePub_t *pub, int status);
};
```

Check a callback and its member before invoking it. Decide whether callback failures affect state transitions as part of the component's behavior; the mechanical workflow cannot infer that policy.

## Platform operations

Put GPIO, bus, timer, and platform-specific operations behind Ops when substitution or layering benefits from the seam:

```c
struct _moduleOps {
    int (*init)(module_t *me);
    int (*enable)(module_t *me, bool enable);
};
```

The concrete implementation may call MCU-library functions such as GPIO configuration, but those calls do not belong in the reusable template. Propagate initialization failure immediately unless the real component defines and tests a rollback contract.

## Migration

Component Migration is behavior-aware Agent work, not a Python rewrite. Before changing structure:

1. Identify public callers, callbacks, state transitions, timing, side effects, and build integration.
2. Preserve observable behavior while introducing Pub/CB/Ops, static instances, and the ordered handler table.
3. Run Style Format and both Style and Component Structure checks.
4. Run relevant focused and aggregate host tests, then an available target build.
5. Report host tests, target build, flashing, and hardware validation separately.
