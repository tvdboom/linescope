# Backends
----------

Trace is the default. Choose a backend with `backend=...` in Python or
`--backend` on the CLI.

## Choose a backend

Use **Trace** for short runs and execution counts, **Scalene** for
representative workloads or GPU investigations, and **Tachyon** for sampling
on Python 3.15.

| Feature | Scalene | Trace | Tachyon |
| --- | :---: | :---: | :---: |
| Python versions | 3.11–3.14 | 3.11–3.15 | 3.15 |
| Collection | Sampling | Tracing | External sampling |
| Line and function time | ✓ | ✓ | ✓ |
| Line hits, function calls, per-hit averages | ✗ | ✓ | ✗ |
| Observed sample counts | ✓ | ✗ | ✓ |
| Retained Python allocation changes | ✓ | ✓ | ✓ |
| Process RAM, changes, observed peak, timeline | ✓ | ✓ | ✓ |
| GPU time and memory | ✓ | ✗ | ✗ |
| Native/library cost on project lines | ✓ | ✓ | ✓ |
| Multiple Python threads | ✓ | ✗ | ✗ |
| Configurable sampling rate | ✓ | ✗ | ✓ |

Enable [memory](#memory) with `memory=True` on any built-in backend. Enable
[GPU](gpu.md) collection with `gpu=True` and Scalene on a supported device.
Per-line Python allocation peaks are unavailable. Custom backend features
follow their declared [BackendCapabilities].

## Scalene

Select `backend="scalene"` on Python 3.11–3.14. Install the optional dependency
with `uv pip install "linescope[scalene]"`.

Scalene samples Python threads and attributes native and library work to the
nearest project line. It can collect GPU metrics on supported devices. Brief
operations may receive no samples; hit counts and per-hit averages are
unavailable.

## Trace

Select `backend="trace"` on Python 3.11–3.15. It uses Python's tracing hooks to
record line events, hit counts, and wall intervals in the thread that starts
the session.

Trace adds overhead, especially in loops with many short lines, and shares hooks
with debuggers. It does not collect GPU metrics: `gpu=True` emits a
`RuntimeWarning` and records it in `session.result.warnings` while Python
profiling continues.

## Tachyon

Select `backend="tachyon"` on Python 3.15. It uses the runtime's
`profiling.sampling` stack sampler in a separate collector process, attributing
third-party frames to their nearest project caller.

Tachyon provides sampling without an extra dependency. It cannot collect hit
counts or GPU measurements. Operating-system process-access restrictions can
prevent attachment; collection failures report diagnostics.

## Profiling scope

`root`, `include`, and `exclude` select project source. They do not attach the
profiler to worker processes.

| Backend | Python thread coverage | Separate worker processes |
| --- | --- | --- |
| Scalene | Threads in the profiled process | Not collected |
| Trace | Thread that starts the session | Not collected |
| Tachyon | Thread that starts the session | Not collected |
| Custom | Defined by the backend | Defined by the backend |

A calling line can include time waiting for a thread or process pool without
recording the workers' Python lines. Keep the workload's completion inside the
session to include that wait. Elapsed wall time measures the session's duration,
rather than total CPU time across workers.

In Spark, Python profiling covers the driver. Executors and Python UDF workers
contribute only the [runtime metrics](spark.md#worker-scope-and-overall-time)
that Spark exposes.

## Performance

### Sampling rate

Set `sample_rate` to the target number of samples per second for Scalene or
Tachyon:

```python
from linescope import profile

with profile(backend="scalene", sample_rate=250) as session:
    run_pipeline()
```

The CLI equivalent is `linescope --backend scalene --sample-rate 250 app.py`.
You can also set `sample_rate = 250` in `[tool.linescope]`. Both sampling
backends default to 1000 samples per second; Trace ignores this setting.

Higher rates can capture more brief operations but add overhead. Scheduling,
timer resolution, and workload behavior affect the achieved rate. The report
shows observed samples and the measured rate.

### Collection overhead

Use representative workloads long enough for sampling to capture repeated
expensive work. Trace records every line event, which can noticeably slow
Python loops. Its wall intervals include that overhead.

Memory collection adds allocation snapshots, RAM reads, and line-boundary
tracing, even with a sampling backend. Live notebook modes also take snapshots
and render after each cell. Use `display="end"` for one final report or
`display="none"` to save it yourself.

Compare runs with the same backend, options, inputs, and environment. Check an
optimization with unprofiled runs too, since instrumentation changes execution
time.

## Memory

Enable process RAM and retained Python allocation measurements with
`memory=True` or `--memory`. This works with every built-in timing backend
without a native allocator preload or kernel restart.

```python
from linescope import profile

with profile(backend="trace", memory=True, display="none") as session:
    values = [bytearray(1024) for _ in range(10_000)]

session.save("memory.html")
```

### Python allocation changes

The collector compares `tracemalloc` snapshots at startup, stop, and live report
requests. `memory.delta_bytes` records the change in tracked bytes still
allocated at each source line. Library allocations are attributed to the
nearest project frame; freed bytes belong to their original allocation site.
Allocations created and freed between snapshots leave no retained change.

These measurements include Python threads but exclude untracked native
allocations, device memory, and separate processes. They differ from process
RAM and should not be added to it. Per-line allocation peaks are unavailable.

Existing allocation tracing remains running after collection. Shallow existing
tracebacks can limit library attribution. If the workload stops tracing,
allocation values become unavailable and a warning explains why.

### RAM readings and growth

Process RAM is resident memory (RSS), or the working set on Windows. It includes
Python objects, native buffers, shared pages, and profiler overhead, while
excluding separate processes and GPU memory.

LineScope reads RAM at project line boundaries in the starting thread and
during long calls when scheduling permits. **Mem Change** accumulates changes
across a line's intervals; **Peak Mem** is its highest observed reading. Other
threads can change process RAM, so an active line does not necessarily own the
allocations.

Brief spikes can escape observation, especially during native calls holding
the GIL. Deleting an object need not reduce RAM immediately because allocators
can keep freed memory for reuse. Unexecuted lines and failed readings remain
unavailable; measured zero changes stay zero.

The **Memory** view preserves readings across repeated iterations. Long
timelines may be compressed, with a notice in the report; source-line
statistics still incorporate every observation.

### Driver and executor memory

In Spark, RAM columns measure the Python driver, excluding its JVM and
executors. A lazy [DataFrame] can describe a large dataset with little Python
RAM, while `toPandas()` can bring substantial data into the driver.

Child notebook processes have separate timelines. Shared source pages retain
the largest observed peak across runs but leave combined **Mem Change**
unavailable; inspect each run for its own readings.

[Spark operator memory and spill](spark.md#plans-and-metrics) have different
scopes and should not be added to process RAM.

## Custom backends

Implement [ProfilerBackend] with `start()`, `stop()`, and `result()`, declare
[BackendCapabilities], and register a factory with [register_backend]. Return
[RawBackendResult] measurements independently of source discovery and rendering.
Use `None` for unavailable metrics and restore resources after failures.
