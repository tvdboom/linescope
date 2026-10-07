# Configuration

Place project defaults in the nearest `pyproject.toml`:

```toml
[tool.linescope]
backend = "scalene"
sample_rate = 250
include = ["mypackage"]
exclude = ["tests", "generated"]
memory = false
gpu = false
spark = false
notebooks = true
display = "end"
output = "linescope.html"
```

Override project settings for an individual session through `profile`:

```python
from linescope import profile

with profile(
    backend="trace",
    display="none",
    include=["mypackage"],
    memory=False,
) as session:
    run_pipeline()
```

Explicit profile arguments and CLI values override project settings, which
override built-in defaults. Each new session reads project TOML; explicit
options apply only to that session. Settings are validated before collection
starts. Use `root` when working outside the package root, and include only the
code that belongs to your investigation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `backend` | `"trace"` | Trace on every supported Python version |
| `sample_rate` | `None` | Target samples/sec; Scalene and Tachyon 1000 |
| `memory` | `False` | Request Python-driver memory metrics |
| `gpu` | `False` | Request [GPU](gpu.md) metrics with Scalene; Trace warns |
| `spark` | `False` | Enable lazy Spark observation; requires existing PySpark |
| `notebooks` | `True` | Capture supported notebook sources |
| `display` | `"end"` | Also `"cell"`, `"cell-summary"`, or `"none"` |
| `include` | automatic | Project packages/paths to include |
| `exclude` | empty | Project packages/paths to exclude |
| `root` | discovered | Project source boundary |
| `output` | `None` | Explicit report path; otherwise use a temporary file |

`sample_rate` sets a target. The achieved samples per second can differ
depending on operating-system timer resolution and scheduling settings, as
well as workload and collection overhead. The report shows the measured rate.

`output` controls the destination used when displaying in a browser. With
`display="none"`, save explicitly with `session.save(...)`.

Full reports display inline automatically in notebooks and open a browser
elsewhere. Choose a destination when showing a report with
`session.show(inline=True)` or `session.show(inline=False)`; `inline` is not a
session or project configuration option.

See the [configuration API](../api/configuration/config.md) for the definitive
signatures.

For a compact inline overview after each notebook cell, set
`display="cell-summary"`. This mode shows only the latest cell's measurements;
open the cumulative report explicitly with `session.show()` after stopping.
See [cell-by-cell debugging](notebooks.md#debug-cell-by-cell) for an example.
