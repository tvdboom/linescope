# Profiling Python

## Context manager

```python
from linescope import profile

with profile(backend="trace") as session:
    run_pipeline()
```

Collection stops and the final report is generated when the block exits. Cleanup also runs
when the profiled code raises an exception; the original exception remains visible.

## Explicit start and stop

```python
from linescope import profile

session = profile.start(backend="trace", display="none")
run_pipeline()
profile.stop()
session.save("pipeline.html")
```

`profiler` is an alias for the same convenient controller. Keep a reference to a session when
working with several runs. Call `stop()` before inspecting its normalized result.

## Headless execution

Set `display="none"` to collect and save without opening a browser or rendering notebook output.
The saved report embeds its source snapshots and assets. It can be opened later without the
original project files or a running server.

## Scripts and packages

The CLI can run a script or module and forward arguments to it. For package work, run from the
project root and include your package explicitly when useful:

```console
linescope --backend trace --include mypackage -m mypackage.job --input data.csv
```

See [CLI options](../cli/options.md) for argument ordering and output behavior.
