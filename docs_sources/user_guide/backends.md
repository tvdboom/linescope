# Backends
----------

The backend determines how line time is collected, which metrics are available,
and how much instrumentation affects the workload. Choose it with `backend=...`
in the Python API or `--backend` on the CLI.

## Choose a backend

Compare the features of LineScope's built-in adapters below. ✓ means supported;
✗ means unavailable.

| Feature | Scalene | Trace | Tachyon |
| --- | :---: | :---: | :---: |
| Python versions | 3.11–3.14 | 3.11–3.15 | 3.15 |
| Collection | Sampling | Tracing | External sampling |
| Line and function time | ✓ | ✓ | ✓ |
| Recorded line hits | ✗ | ✓ | ✗ |
| Recorded function calls | ✗ | ✓ | ✗ |
| Per-hit average time | ✗ | ✓ | ✗ |
| Observed sample counts | ✓ | ✗ | ✓ |
| Retained Python allocation delta | ✓ | ✓ | ✓ |
| Per-line Python allocation peak | ✗ | ✗ | ✗ |
| Process RAM, changes, observed peak | ✓ | ✓ | ✓ |
| Process RAM timeline | ✓ | ✓ | ✓ |
| GPU time and memory | ✓ | ✗ | ✗ |
| Native/library cost on project lines | ✓ | ✓ | ✓ |
| Multiple Python threads | ✓ | ✗ | ✗ |
| Automatic worker-process profiling | ✗ | ✗ | ✗ |
| Configurable sampling rate | ✓ | ✗ | ✓ |
| Best suited to | Bottleneck searches | Small runs and tests | 3.15 sampling |

Enable the shared memory collector with `memory=True` on any built-in backend.
GPU collection uses `gpu=True` with Scalene and requires a supported platform
and device. See [memory](#memory) and [GPU](gpu.md) for their scope. The thread
coverage row describes timing and execution counts; memory covers the profiled
process. Custom backend features depend on their declared [BackendCapabilities].

Choose Scalene for representative workloads or GPU investigations. Choose Trace
for short investigations, recorded execution counts, and deterministic tests.
Choose Tachyon for sampling on Python 3.15.
Sampling can miss brief operations; Trace's wall intervals include tracing
overhead. See [performance](#performance) for the tradeoffs.

Trace is the default on every supported Python version (3.11–3.15).
Scalene is included with LineScope on Python 3.11–3.14. Collectors must be
supported by the runtime; an unavailable collector fails explicitly instead of
silently changing backends. See [GPU](gpu.md) for device collection support.

## Scalene

`backend="scalene"` selects the sampling collector on Python 3.11–3.14.
It collects line time and optional driver memory or GPU metrics. Sampling can
miss short lines; hits and averages remain unavailable. Native and library work
stays on the nearest project line. See [memory](#memory) and [GPU](gpu.md) for
optional collection.

## Trace

`backend="trace"` uses Python's built-in tracing hooks on Python 3.11–3.15. It
records current-thread line events, hit counts and wall intervals, making it
useful for small investigations, tests and portable environments. Tracing adds
overhead and shares hooks with debuggers. Enable `memory=True` for process RAM
and retained Python allocation changes; no native preload is needed. Python
allocation peaks and GPU measurements are unavailable. Requesting GPU
collection raises a clear capability error.

## Tachyon

`backend="tachyon"` uses Python 3.15's `profiling.sampling` stack sampler in a
separate collector process. It estimates line time from samples and attributes
third-party frames to their nearest project caller. Python 3.15 defaults to
Trace; select Tachyon explicitly for sampling. Tachyon supplies no line hit
counts or GPU measurements. `memory=True` adds the shared memory collector.
Operating-system restrictions on reading
process memory can prevent attachment; failures provide diagnostics instead of
changing backends.

## Profiling scope

The source boundary (`root`, `include`, and `exclude`) selects project files for
collection and display. It does not attach the profiler to additional worker
processes.

| Backend | Python thread coverage | Separate worker processes |
| --- | --- | --- |
| Scalene | Samples threads in the profiled process | Not collected |
| Trace | Thread that starts the session | Not collected |
| Tachyon | Thread that starts the session | Not collected |
| Custom | Defined by the registered backend | Defined by that backend |

Scalene attributes native library work to the relevant project caller where
available. Its line estimates are not individual worker CPU counters. Trace and
Tachyon can show time waiting for a thread or process pool on the calling line,
even though those workers' Python lines are outside their collection scope.

Elapsed wall time describes the session's duration. It includes waits inside the
session, but does not represent the sum of CPU time consumed by every worker.
For example, if four processes run concurrently while the parent waits five
seconds for results, the parent can show about five seconds of waiting without
reporting each process's work. Worker activity can continue after collection if
the application does not wait for it before leaving the session.

In Spark, the profiled Python process is the driver. Executor JVMs and Python
UDF workers contribute only the separate [Spark runtime
metrics](spark.md#worker-scope-and-overall-time) that Spark exposes. GPU kernels
also have [separate measurements](gpu.md); synchronizing within the session
keeps queued device work inside the observation period.

## Performance

### Sampling rate

Set `sample_rate` to a positive integer to configure the target number of
samples per second for Scalene or Tachyon:

```python
from linescope import profile

with profile(backend="scalene", sample_rate=250) as session:
    run_pipeline()
```

For scripts, use `linescope --sample-rate 250 application.py`. You can also set
`sample_rate = 250` in `[tool.linescope]` or call `configure(sample_rate=250)`.
When omitted or set to None in Python, the defaults remain 100 samples per
second for Scalene and 1000 for Tachyon. Trace ignores this setting.

This is a target rate: stack capture, workload behavior, and scheduler delays
affect the actual number of observations. Scalene randomizes intervals on
POSIX and uses fixed intervals on Windows. Higher rates can capture more short
lines but increase profiling overhead. The report shows the target rate and
observed sample counts separately from hits or function calls.

### Collection overhead

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
collection adds allocation tracking, RAM observations, and line-boundary
tracing, including when timing uses a sampling backend. Live notebook reports
with `display="cell"` repeat rendering after each cell; use `display="end"` for
one final display or `display="none"` to save explicitly after collection.

Compare results using the same backend, options, inputs, and environment. Check
an optimization with separate unprofiled runs as well: profiler timings help
locate costs, while instrumentation can change the workload's execution time.
LineScope does not promise a fixed overhead percentage for any backend.

## Memory

Enable process RAM and retained Python allocation collection with
`profile(memory=True)` or `--memory`.
This works with every timing backend. No native allocator preload or notebook
kernel restart is required.

```python
from linescope import profile

with profile(backend="trace", memory=True, display="none") as session:
    values = [bytearray(1024) for _ in range(10_000)]

session.save("memory.html")
```

### Python allocation changes

The shared memory collector uses `tracemalloc` to compare snapshots at startup,
at stop, and on live report requests. Per-line `memory.delta_bytes` in the
collected profile data records the change in tracked bytes still allocated at
each allocation site. Library allocations go
to the nearest project frame in their captured traceback. Freed bytes belong
to the original allocation site; allocations created and freed between
snapshots leave no retained change. Per-line Python allocation peaks remain
unavailable.

These snapshots include all Python threads, even when timing and hits cover
only the starting thread. They exclude untracked native allocations, GPU
memory, and separate processes. RAM and allocation deltas have different
meanings and must not be added together.

New allocation tracers capture up to 25 frames and are stopped during cleanup.
Existing tracers keep their depth, history, and peak and remain running. A
shallow existing traceback can prevent library attribution to a project line.
If the workload stops allocation tracing, values remain unknown and a warning
explains the missing data. The CLI retains script/module globals through the
final snapshot so their allocations can be reported.

### RAM readings and growth

LineScope observes resident process memory (RSS), the memory currently in RAM.
On Windows this is the process working set. It includes Python objects, native
library buffers, shared pages, and profiler overhead. It excludes swapped-out
pages, separate worker processes, and GPU memory.

The memory observer traces the starting thread's project line boundaries,
even when the timing backend uses sampling. It reads RAM after each completed
line interval and attributes external calls to their nearest project caller.
Project child calls have their own intervals rather than charging the same
increase to both caller and child. Other threads can still change process RAM,
so these observations identify where growth occurred, not allocation ownership.

The source columns show Mem Change, the accumulated process-memory change over
all intervals of that line, and Peak Mem, the highest reading during its
intervals. Zero change is a measured value; unexecuted lines and failed
readings stay unavailable. Repeated loop iterations keep their history in the
Memory view, rather than being reduced to the final line reading alone.

A background observer also samples every 10 ms during long calls when the
scheduler and GIL permit. Peaks are observed rather than guaranteed: brief
spikes between readings, or native calls holding the GIL, can escape sampling.
Tracing and RAM reads add overhead. Python and native allocators can retain
freed memory for reuse, so deleting an object need not immediately reduce RSS.

Each run keeps at most 4096 timeline readings. When compression is needed,
chronological bucket peaks and troughs, missing readings, and the first and last
readings are retained. A notice explains compression in the report. Source-line
RAM statistics continue to incorporate every observation.

### Driver and executor memory

RAM columns concern the profiled Python process. In Spark that is normally the
Python driver, not its JVM or executors. A lazy [DataFrame] can describe a large
distributed dataset while occupying little Python RAM. Conversely, `toPandas()`
may move substantial data into the driver.

Child notebook processes have separate timelines. Their RAM is never added to
parent RAM. If a source line has RAM measurements from multiple runs, the shared
source page retains the largest observed peak and leaves its combined Mem
Change unavailable. Open each run's Memory timeline for its own readings.

Spark operator peak memory and spill, when available, appear with the
[Spark execution](spark.md#plans-and-metrics). They have different semantics and
must not be added to process RAM.

## Custom backends

Implement [ProfilerBackend] with `start()`, `stop()` and `result()`, declare
[BackendCapabilities], and register your factory with [register_backend]. Return
[RawBackendResult] measurements independently of source discovery and rendering.
Keep unavailable metrics as `None`, restore resources after failures, and use
sampling counts only to estimate time. See the
[custom backend example](../examples/backend.md) for a complete collector.
