# Backends

Choose a collector with `backend=...` or `--backend`. Trace is the default.

## Choose a backend

| Feature | Trace | Scalene | Tachyon |
| --- | :---: | :---: | :---: |
| Python versions | 3.11–3.15 | 3.11–3.14 | 3.15 |
| Collection | Tracing | Sampling | External sampling |
| Line and function time | ✓ | ✓ | ✓ |
| Hits, calls, and per-call averages | ✓ | ✗ | ✗ |
| Sample counts | ✗ | ✓ | ✓ |
| Retained Python allocation changes | ✓ | ✓ | ✓ |
| Process RAM and timeline | ✓ | ✓ | ✓ |
| GPU time and memory | ✗ | ✓ | ✗ |
| Library cost attributed to project lines | ✓ | ✓ | ✓ |
| Multiple Python threads | ✗ | ✓ | ✗ |
| Configurable sampling rate | ✗ | ✓ | ✓ |

Enable memory with `memory=True`; GPU collection requires Scalene and
`gpu=True`.

## Scalene

Install `linescope[scalene]` and select `backend="scalene"`.
Scalene samples Python threads and attributes library work to the calling
project line. It also supports [GPU metrics](gpu.md). Short operations may
receive no samples; exact hit and call counts are unavailable.

## Trace

Select `backend="trace"` for line events and execution counts in the starting
thread. Tracing adds overhead, especially in tight loops, and shares hooks with
debuggers. GPU requests warn and continue without device metrics.

## Tachyon

Select `backend="tachyon"` on Python 3.15 to use `profiling.sampling` without
an extra dependency. A separate collector samples the starting thread.
Process-access restrictions can prevent attachment. Hit counts and GPU metrics
are unavailable.

## Profiling scope

`root`, `include`, and `exclude` select source files. Separate worker processes
are not profiled. A calling line can include the wait for workers; keep their
completion inside the session to measure it. Elapsed time is wall time, not
summed worker CPU time.

Spark profiling covers driver Python. Executors contribute only the
[metrics Spark exposes](spark.md#worker-scope-and-overall-time).

## Performance

### Sampling rate

```python
from linescope import profile

with profile(backend="scalene", sample_rate=250):
    run_pipeline()
```

Scalene and Tachyon default to 1000 samples per second. Set `sample_rate` or
`--sample-rate` to change the target; Trace ignores it. Higher rates add
overhead. The report shows the achieved rate.

### Collection overhead

Memory tracking adds allocation snapshots, RAM reads, and line tracing, even
with sampling. Live notebook reports add work after each cell. Use
`display="end"` for one report, or `display="none"` to save it yourself.

Compare runs with the same settings and inputs. Check improvements without the
profiler too.

## Memory

Enable process RAM and retained Python allocations with `memory=True` or
`--memory`. No allocator preload or kernel restart is needed.

```python
from linescope import profile

with profile(memory=True, display="none") as session:
    values = [bytearray(1024) for _ in range(10_000)]

session.save("memory.html")
```

### Python allocation changes

`memory.delta_bytes` compares `tracemalloc` snapshots and attributes retained
bytes to their allocation site. Freed bytes reduce that site's total.
Allocations freed between snapshots are not counted.

This covers Python threads but excludes untracked native allocations, device
memory, and separate processes. Per-line allocation peaks are unavailable.
Existing allocation tracing stays running; shallow tracebacks can limit library
attribution. If the workload stops tracing, allocation values become
unavailable.

### RAM readings and growth

Process RAM is RSS, or the working set on Windows. It includes Python objects,
native buffers, shared pages, and profiler overhead.

**Mem Change** accumulates RAM changes during a line's intervals. **Peak Mem**
is its highest observed reading. Other threads can affect both. Brief spikes
may be missed, particularly in native calls holding the GIL. Deleting an object
may not reduce RAM because allocators retain freed memory.

The **Memory** view plots readings over time. Long timelines may be compressed;
line statistics still use every observation.

### Driver and executor memory

Spark driver RAM excludes the JVM and executors. A lazy [DataFrame] can use
little Python RAM until an action such as `toPandas()` brings data into it.
[Spark memory and spill counters](spark.md#plans-and-metrics) measure other
scopes and should not be added to RAM.

Child processes have separate timelines. Shared source pages keep the largest
observed peak, but leave combined **Mem Change** unavailable.

## Custom backends

Implement [ProfilerBackend], declare [BackendCapabilities], and register a
factory with [register_backend]. Return [RawBackendResult] measurements with
`None` for unavailable values. Release owned resources on failure.
