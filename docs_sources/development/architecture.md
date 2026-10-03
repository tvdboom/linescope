# Architecture

```text
Backend collection
    ↓
Backend adaptation / normalized measurements
    ↓
Project filtering and source snapshots
    ↓
Symbol index + notebook correlation + Spark observation
    ↓
ProfileResult / ProfileRun tree
    ↓
Self-contained HTML renderer
```

## Boundaries

`backends` collects measurements. `source` discovers project files, snapshots text, and resolves
navigation. `notebooks` captures virtual source and child relationships. `spark` observes actions
and normalizes plans/metrics. The normalized `model` joins these concerns without depending on
a backend's raw result format. Rendering owns presentation only.

## Invariants

1. Full source is primary; functions are an index over source measurements.
2. Third-party implementation details stay outside the default source browser.
3. Missing metrics stay unknown, and sampling never fabricates hit counts.
4. Source links target individual resolvable symbols, not arbitrary whole lines.
5. Spark transformations remain lazy; observer code never adds an action.
6. Python wall time, distributed executor metrics, and memory domains stay separate.
7. Notebook source is snapshotted; separate child runs form a tree.
8. Hooks and patched methods are restored after errors as well as successful runs.
9. Reports escape source/metadata and work without external scripts or stylesheets.

## Future engines

Attach-to-PID collection, Tachyon, and executor-side Python measurements can extend the capability
model. The current implementation does not claim those measurements. Avoid making the renderer
depend on assumptions specific to one collector or Spark runtime.
