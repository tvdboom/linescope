# Backends

Collection engines implement `start()`, `stop()`, and `result()`, and declare the metrics they
can provide. They collect measurements without owning source discovery, notebook/Spark
correlation, or HTML rendering. LineScope's backend adapter normalizes those measurements.

## Built-in engines

| Engine | Use |
| --- | --- |
| `scalene` | Default line sampling and optional memory on supported runtimes |
| `trace` | Portable tracing with line hits for small runs and testing |

## Capability record

See [BackendCapabilities](model.md#BackendCapabilities). Respect `hit_counts`, `memory`,
`sampled`, `scoped`, and executor support when consuming a result. A collector must not claim
values it did not measure. A sample count must never become a fabricated line execution count.

## Custom engines

Use backend registration to add a collector. Follow the [custom backend example](../examples/backend.md)
and the source protocol for the exact factory/constructor contract. A future sampling or external
attach engine can reuse the same result model and report renderer.

## ProfilerBackend

:: linescope.backends.base:ProfilerBackend
    :: signature
    :: head
    :: methods

## RawLine

:: linescope.backends.base:RawLine
    :: signature
    :: head

## RawBackendResult

:: linescope.backends.base:RawBackendResult
    :: signature
    :: head

## register_backend

:: linescope.backends.base:register_backend
    :: signature
    :: head
    :: table:
        - parameters
        - raises

## TraceBackend

:: linescope.backends.trace:TraceBackend
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: methods

## ScaleneBackend

:: linescope.backends.scalene:ScaleneBackend
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: see also
    :: examples
    :: methods

## normalize_scalene

:: linescope.backends.scalene:normalize_scalene
    :: signature
    :: head
    :: table:
        - parameters
        - returns

## memory_preload_environment

:: linescope.backends.scalene:memory_preload_environment
    :: signature
    :: head
    :: table:
        - parameters
        - returns
