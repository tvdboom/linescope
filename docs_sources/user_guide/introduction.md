# Introduction

LineScope combines measurements and source context. A backend records timing or
allocation information, then LineScope discovers project-owned sources,
snapshots them, resolves local symbols, correlates integrations, and writes an
HTML report.

## In this guide

| Section | What you will find |
| --- | --- |
| [Backends](backends.md) | Collectors, metrics, memory, overhead |
| [Notebooks](notebooks.md) | Cells, sessions, Databricks child runs |
| [Spark](spark.md) | Lazy actions, executed plans, and distributed metrics |
| [GPU](gpu.md) | GPU measurement support and interpretation |
| [Reports](reports.md) | Views, columns, snapshots, navigation |

For installation and a runnable first example, start with
[Getting started](../getting_started.md).

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
session.save("pipeline.html")
```

`profiler` is an alias for the same convenient controller. Keep a reference to a
session when working with several runs. Call `stop()` before inspecting its
normalized result.

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

## Configuration

Place project defaults in the nearest `pyproject.toml`:

```toml
[tool.linescope]
backend = "scalene"
sample_rate = 250
include = ["mypackage"]
exclude = ["tests", "generated"]
memory = false
gpu = false
spark = true
notebooks = true
display = "end"
inline = false
output = "linescope.html"
```

Set process defaults with `configure`, or override settings for an individual
session:

```python
from linescope import configure, profile

configure(backend="trace", display="none")

with profile(include=["mypackage"], memory=False) as session:
    run_pipeline()
```

Explicit session/CLI values override process defaults set by `configure`, which
override project settings and built-in defaults. Settings are validated before
collection starts. Use `root` when working outside the package root, and include
only the code that belongs to your investigation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `backend` | `"trace"` | Trace on every supported Python version |
| `sample_rate` | `None` | Target samples/sec; Scalene 100, Tachyon 1000 |
| `memory` | `False` | Request Python-driver memory metrics |
| `gpu` | `False` | Request supported [GPU](gpu.md) metrics |
| `spark` | `True` | Observe Spark lazily; `False` disables integration |
| `notebooks` | `True` | Capture supported notebook sources |
| `display` | `"end"` | Show at stop; also `"cell"` or `"none"` |
| `inline` | `False` | Display in a cell; otherwise open a browser tab |
| `include` | automatic | Project packages/paths to include |
| `exclude` | empty | Project packages/paths to exclude |
| `root` | discovered | Project source boundary |
| `output` | `None` | Explicit report path; otherwise use a temporary file |

`output` controls the destination used when displaying in a browser. With
`display="none"`, save explicitly with `session.save(...)`.

See the [configuration API](../api/configuration/config.md) for the definitive
signatures.
