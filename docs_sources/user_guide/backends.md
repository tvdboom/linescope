# Backends
----------

The backend determines how line time is collected, which metrics are available,
and how much instrumentation affects the workload. Choose it with `backend=...`
in the Python API or `--backend` on the CLI.

## Choose a backend

| Backend | Python | Collection | Available measurements |
| --- | --- | --- | --- |
| Scalene | 3.11–3.14 | Sampling | Estimated time; memory and GPU |
| Trace | 3.11–3.15 | Tracing | Line wall intervals and hits |
| Tachyon | 3.15 | External sampling | Estimated line time |

Scalene is the default on Python 3.11–3.14; Trace is the default on Python 3.15.
Scalene is included with LineScope on Python 3.11–3.14. Collectors must be
supported by the runtime; an unavailable collector fails explicitly instead of
silently changing backends. See [GPU](gpu.md) for device collection support.

## Scalene

`backend="scalene"` selects the default sampling collector on Python 3.11–3.14.
It collects line time and optional driver memory or GPU metrics. Sampling can
miss short lines; hits and averages remain unavailable. Native and library work
stays on the nearest project line. See [memory](#memory) and [GPU](gpu.md) for
optional collection.

## Trace

`backend="trace"` uses Python's built-in tracing hooks on Python 3.11–3.15. It
records current-thread line events, hit counts and wall intervals, making it
useful for small investigations, tests and portable environments. Tracing adds
overhead and shares hooks with debuggers. Memory and GPU measurements are
unsupported; requesting them raises a clear capability error.

## Tachyon

`backend="tachyon"` uses Python 3.15's `profiling.sampling` stack sampler in a
separate collector process. It estimates line time from samples and attributes
third-party frames to their nearest project caller. Python 3.15 defaults to
Trace; select Tachyon explicitly for sampling. Tachyon supplies no line hit
counts, memory or GPU measurements. Operating-system restrictions on reading
process memory can prevent attachment; failures provide diagnostics instead of
changing backends.

## Performance

Trace observes line events and records wall intervals while the workload runs.
Its per-event bookkeeping adds overhead, particularly in Python loops with many
short lines. Hit counts help explain how often a line runs, but the measured
intervals include the effects of instrumentation.

Scalene and Tachyon estimate line time from periodic samples. Sampling avoids
recording every line event, but short runs or brief operations may receive too
few samples to represent their cost. Use a representative workload that runs
long enough to expose repeated expensive work. Samples never become hit counts
or per-hit averages.

Collector startup, source discovery, notebook hooks, Spark observation, and
report generation also contribute to the overall cost of a profiling run. Memory
collection adds native allocation instrumentation. Live notebook reports with
`display="cell"` repeat rendering after each cell; use `display="end"` for one
final display or `display="none"` to save explicitly after collection.

Compare results using the same backend, options, inputs, and environment. Check
an optimization with separate unprofiled runs as well: profiler timings help
locate costs, while instrumentation can change the workload's execution time.
LineScope does not promise a fixed overhead percentage for any backend.

## Memory

Request optional memory collection with
`profile(backend="scalene", memory=True)` or `--backend scalene --memory`.
Scalene exposes allocation metrics that LineScope normalizes into simple
delta/peak fields when available. Capabilities and missing values remain
explicit; not every backend/platform exposes the same memory information.

On Unix, native allocator interception must be loaded when Python starts. The
CLI memory mode prepares that environment for its target process. For an
existing notebook kernel or a direct API session, prepare the native environment
before launching Python; enabling memory after the kernel has started cannot
retroactively preload its allocator. Unsupported initialization fails explicitly
instead of producing invented memory values.

Scalene 2.3 also provides a native Windows memory collector, initialized by the
adapter. Native allocation metrics are sampled and may not equal the exact size
of every short-lived allocation. The native collector requires a supported
Scalene binary wheel. Prefer Python 3.12 or newer for Windows memory profiling:
a Python 3.11 source build can provide CPU sampling without shipping the
required memory DLL, in which case the adapter reports the missing capability
explicitly.

```python
from linescope import profile

with profile(backend="scalene", memory=True, display="none") as session:
    values = [bytearray(1024) for _ in range(10_000)]

session.save("memory.html")
```

The [memory preload helper](../api/backends/memory_preload_environment.md)
provides the startup environment for an API or kernel launcher.

### Driver and executor memory

Python memory columns concern the process being profiled. In Spark that is
normally the driver. A lazy [DataFrame] can describe a large distributed dataset
while occupying little Python memory. Conversely, `toPandas()` may move
substantial data into the driver.

Spark operator peak memory and spill, when exposed by Spark, appear with the
[Spark execution](spark.md#plans-and-metrics). They are separate measurements
with different semantics. Do not add them to the driver's Python memory or
interpret distributed operator peaks as one process-wide peak.

## Custom backends

Implement [ProfilerBackend] with `start()`, `stop()` and `result()`, declare
[BackendCapabilities], and register your factory with [register_backend]. Return
[RawBackendResult] measurements independently of source discovery and rendering.
Keep unavailable metrics as `None`, restore resources after failures, and use
sampling counts only to estimate time. See the
[custom backend example](../examples/backend.md) for a complete collector.
