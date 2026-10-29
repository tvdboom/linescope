# Introduction

Use `profile` as a context manager, or start and stop collection explicitly.
Reports show measurements beside the source that ran.

## Profile Python

### Context manager

```python
from linescope import profile

with profile() as session:
    run_pipeline()
```

The report displays when the block ends. Workload exceptions still propagate;
LineScope stops collection and restores its hooks.

### Explicit start and stop

```python
from linescope import profile

session = profile.start(display="none")
run_pipeline()
profile.stop()
session.save("pipeline.html")
```

Use this across notebook cells or separate steps. Results are available through
`session.result` or `profile.result`. `profiler` is an alias for `profile`.

### Headless execution

Set `display="none"` to suppress automatic display. Save with `session.save()`.

### Scripts and packages

```console
linescope --include mypackage -m mypackage.job --input data.csv
```

Arguments after the target go to your program. See the
[CLI reference](../cli/linescope.md) for options.

## Read the results

Open **Overview** and follow an expensive line to its source. A dash means
unavailable; sampling can miss short operations. See [Reports](reports.md) for
measurements and [Backends](backends.md) for collector differences.
