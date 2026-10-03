# Configuration

Place project defaults in the nearest `pyproject.toml`:

```toml
[tool.linescope]
backend = "scalene"
include = ["mypackage"]
exclude = ["tests", "generated"]
memory = false
spark = "auto"
notebooks = true
display = "end"
output = "linescope.html"
```

Set process defaults with `configure`, or override settings for an individual session:

```python
from linescope import configure, profile

configure(backend="trace", display="none")

with profile(include=["mypackage"], memory=False) as session:
    run_pipeline()
```

Explicit session/CLI values override configured/project defaults. Settings are validated before
collection starts. Use `root` when working outside the package root, and include only the code
that belongs to your investigation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `backend` | `"scalene"` | Collection engine name |
| `memory` | `False` | Request Python memory metrics |
| `spark` | `"auto"` | Detect Spark; `True` requests integration explicitly |
| `notebooks` | `True` | Capture supported notebook sources |
| `display` | `"end"` | Show at stop; also `"cell"` or `"none"` |
| `include` | automatic | Project packages/paths to include |
| `exclude` | empty | Project packages/paths to exclude |
| `root` | discovered | Project source boundary |
| `output` | `"linescope.html"` | Generated report path |

See the [configuration API](../api/configuration.md) for the definitive signatures.
