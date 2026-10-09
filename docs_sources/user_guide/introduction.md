# Introduction

LineScope profiles Python code and presents the measurements alongside captured
source in an HTML report. Start with a block of code, a script, or a notebook
session, then use the report to find expensive lines and follow their context.

## Profile Python

### Context manager

```python
from linescope import profile

with profile(backend="trace") as session:
    run_pipeline()
```

The report displays when the block exits. If the code fails, LineScope stops
collection and restores its hooks while preserving the original exception.
Trace is the default; see [Backends](backends.md) to choose another collector.

### Explicit start and stop

```python
from linescope import profile

session = profile.start(backend="trace", display="none")
run_pipeline()
profile.stop()
result = session.result
session.save("pipeline.html")
```

Use this form when collection spans several steps or notebook cells. Keep the
session reference to inspect an earlier run after starting another.
`profile.result` also exposes the latest result; `profiler` is an alias for
`profile`. See [Session](../api/profiling/session.md) for the full API.

### Headless execution

Set `display="none"` to collect without displaying a report, then use
`session.save(...)` to write it. Saved reports work offline and include their
source snapshots.

### Scripts and packages

Run from your project root and pass a script or module to the CLI. Arguments
after the target are forwarded to it:

```console
linescope --backend trace --include mypackage -m mypackage.job --input data.csv
```

See [CLI options](../cli/linescope.md) for output and scope settings, or
[Notebooks](notebooks.md) to profile cells.

## Read the results

Start with **Overview**, then follow a hotspot to its source. Sampling counts
are observations, not execution counts; fast lines can run without receiving
samples. A dash means a measurement is unavailable.

Spark links explain which transformations participate in an action. They do not
assign the action's wall time to each transformation. See [Reports](reports.md)
for column definitions and navigation.
