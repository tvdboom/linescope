# Configuration

Place project defaults in the nearest `pyproject.toml`:

```toml
[tool.linescope]
backend = "scalene"
sample_rate = 250
include = ["mypackage"]
exclude = ["tests", "generated"]
display = "end"
output = "linescope.html"
```

Override project settings for an individual session through [`profile`]:

```python
from linescope import profile

with profile(backend="trace", display="none") as session:
    run_pipeline()
```

Explicit Python and CLI options override project settings, which override
built-in defaults. Each session reads the project settings again; overrides
apply only to that session.

Use `root` to set the project boundary when profiling outside the package root.
Use `include` and `exclude` to narrow the source to your investigation. See the
[configuration API](../api/configuration/config.md) for all settings and
defaults.

Choose `display="cell-summary"` for compact notebook output; see
[cell-by-cell debugging](notebooks.md#debug-cell-by-cell) for the workflow.
