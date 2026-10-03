# Backends and measurement

## Scalene (default)

Scalene supplies line-level samples and optional memory data. Install the `scalene` extra on a
supported platform, then use `backend="scalene"` or omit the backend setting. LineScope adapts
Scalene output into its own model; the renderer never consumes Scalene's raw JSON directly.
The current adapter targets Scalene 2.3 and supports CPU sampling on Windows. Native memory
profiling has additional startup requirements described in [memory](memory.md).

Sampling favors low perturbation over exact tiny-line measurements. Unsupported hit counts stay
unavailable. Native/library work is attributed to relevant user source, without exposing its
implementation as additional source pages.

## Trace (portable alternative)

`backend="trace"` selects the built-in `sys.settrace` collector. It is useful for deterministic
tests, small investigations, and environments where Scalene cannot run. It records line events
and wall intervals with higher overhead than sampling. It is an explicit choice; LineScope does
not silently change measurement semantics when Scalene is unavailable.

Tracing and debugging both use interpreter hooks. Avoid running multiple profilers/debuggers
simultaneously, and use shorter representative workloads when tracing heavily iterative code.
The trace collector profiles the current thread. It does not support memory collection;
requesting `memory=True` raises a capability error instead of fabricating allocation statistics.

## Capability declarations

Each backend declares support for line/function timing, hit counts, memory, scopes, notebook
collection, and Spark driver/executor measurement. The report adapts to those declarations and
the optional values actually recorded. See [backend API](../api/backends.md).

Tachyon/`profiling.sampling`, `sys.monitoring`, and attach-to-PID integration are extension
points for later releases. They are not implemented backends in version 0.1.0.
