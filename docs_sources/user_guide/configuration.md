# Configuration

Set project defaults in `pyproject.toml`:

```toml
[tool.linescope]
backend = "scalene"
sample_rate = 250
include = ["mypackage"]
exclude = ["tests", "generated"]
display = "end"
output = "linescope.html"
```

Python and CLI options override project settings:

```python
from linescope import profile

with profile(backend="trace", display="none") as session:
    run_pipeline()
```

Each session rereads the project settings. Use `root` to set the project
boundary and `include` or `exclude` to limit source collection. See
[Config](../api/configuration/config.md) for all options and defaults.
