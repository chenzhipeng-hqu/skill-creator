# Component Patterns

Read this reference only when extending a generated Pub/CB/Ops component or migrating existing behavior into that structure. The generated template deliberately omits hardware calls and unknown cross-module dependencies.

## Core contract

- The public structure is the first internal-structure member so a public pointer can identify its owning component instance.
- CB contains callbacks supplied by a caller. Passing `NULL` to the generated setter unregisters the callbacks.
- Ops contains internal platform operations and permits test substitution.
- The static instance array owns component lifetime; the public accessor checks its ID before returning a public pointer.
- The routine processes one instance per call and dispatches through the ordered component handler table.

## Module Public Handles

Add a Module Public Handle only when a concrete dependency exists. Name it `p` plus the target module's Pascal-case symbol prefix plus `Pub`:

```c
sourcePub_t *pSourcePub;
powerMeterPub_t *pPowerMeterPub;
ads131e04Pub_t *pAds131e04Pub;
```

Acquire and validate the handle during initialization according to the target module's real API. Do not generate placeholder calls such as `xxxx_getPub()`.

## Callbacks

Extend the callback type for real events rather than retaining unused demonstrations:

```c
struct _uartCB {
    int (*start)(uartPub_t *pub);
    int (*done)(uartPub_t *pub);
    int (*error)(uartPub_t *pub, int status);
};
```

Check a callback and its member before invoking it. Decide whether callback failures affect state transitions as part of the component's behavior; the mechanical workflow cannot infer that policy.

## Platform operations

Put GPIO, bus, timer, and platform-specific operations behind Ops when substitution or layering benefits from the seam:

```c
struct _uartOps {
    int (*init)(uart_t *me);
    int (*enable)(uart_t *me, bool enable);
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
