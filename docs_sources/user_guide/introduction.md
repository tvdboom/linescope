# Introduction

LineScope combines measurements and source context. A backend records timing or
allocation information, then LineScope discovers project-owned sources,
snapshots them, resolves local symbols, correlates integrations, and writes an
HTML report.

## Three kinds of information

**Measured** values come from the backend or runtime. **Derived** values, such
as a function summary, aggregate measurements. **References** explain
relationships, such as a source line participating in a Spark plan. A reference
is not a claim that Spark spent an exact amount of wall time on that
transformation.

Missing hits, memory, or plan metrics remain unavailable. In particular, a
sample count is not an execution count. A very fast line may receive no samples
while still having executed.

## Profile Python

### Context manager

```python
from linescope import profile

with profile(backend="trace") as session:
    run_pipeline()
```

Collection stops and the final report is displayed when the block exits. Cleanup
also runs when the profiled code raises an exception; the original exception
remains visible.

Choose the collection engine using the [backend guide](backends.md).

### Explicit start and stop

```python
from linescope import profile

session = profile.start(backend="trace", display="none")
run_pipeline()
profile.stop()
result = profile.result
session.save("pipeline.html")
```

`profiler` is an alias for the same convenient controller. Keep a reference to a
session when working with several runs. `profile.stop()` and `session.stop()`
return None, so a notebook displays only the configured report. Retrieve the
complete normalized result through `profile.result` or `session.result` after
stopping collection, including source snapshots, measurements, the run tree,
capabilities, and diagnostics.

### Headless execution

Set `display="none"` to collect without opening a browser or rendering notebook
output, then call `session.save(...)` to write the report. The saved HTML embeds
its source snapshots and assets. It can be opened later without the original
project files or a running server.

### Scripts and packages

The CLI can run a script or module and forward arguments to it. For package
work, run from the project root and include your package explicitly when useful:

```console
linescope --backend trace --include mypackage -m mypackage.job --input data.csv
```

See [CLI options](../cli/linescope.md) for argument ordering and output
behavior.
