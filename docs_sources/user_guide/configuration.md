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
| `display` | `"end"` | Also `"cell"`, `"cell-summary"`, or `"none"` |
| `inline` | `False` | Display in a cell; otherwise open a browser tab |
| `include` | automatic | Project packages/paths to include |
| `exclude` | empty | Project packages/paths to exclude |
| `root` | discovered | Project source boundary |
| `output` | `None` | Explicit report path; otherwise use a temporary file |

`output` controls the destination used when displaying in a browser. With
`display="none"`, save explicitly with `session.save(...)`.

See the [configuration API](../api/configuration/config.md) for the definitive
signatures.

For a compact inline overview after each notebook cell, set
`display="cell-summary"`. This mode shows only the latest cell's measurements;
open the cumulative report explicitly with `session.show()` after stopping.
See [cell-by-cell debugging](notebooks.md#debug-cell-by-cell) for an example.
