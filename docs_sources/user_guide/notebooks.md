# Notebooks

LineScope captures executed notebook cells as source snapshots and connects them
to the same report as imported project code. Use the extension for one cell or
the start/stop API for a notebook-wide session.

## Jupyter notebooks

Install the `notebook` extra and load the extension:

```python
%load_ext linescope
```

### Profile one cell

```python
%%profile --backend trace

values = list(range(100_000))
total = sum(value * value for value in values)
```

The report opens after the cell finishes. Add `--inline` to display it inside
the notebook. The whole input is captured as a virtual source unit; it remains
reproducible if the cell is later edited.

### Profile several cells

```python
from linescope import profile

session = profile.start(backend="trace", display="end")
```

Run the cells you want to investigate, then finish in another cell:

```python
profile.stop()
```

The default generates one full report at stop. `display="cell"` requests live
per-cell updates; `display="none"` keeps the session headless for later
`session.save("notebook.html")`.

Set `inline=True` to display reports inside the notebook instead of opening
browser tabs. Repeated live rendering has a cost; see
[backend performance](backends.md#performance).

Cell source is attached to stable notebook identifiers. Links into imported
project functions use the same symbol resolution as Python files. Notebook
function definitions that are available to the session can also be represented
as virtual source snapshots.

Download the [notebook example](../examples/notebook.md) for a complete local
walkthrough.

## Databricks

Install LineScope into the cluster or notebook environment, then load the
IPython extension and use the cell magic or explicit start/stop API. Keep the
Databricks-provided PySpark version; the LineScope `spark` extra is intended for
environments that need their own local installation.

[Databricks Runtime 16.4 LTS][databricks-16-4-lts] includes Apache Spark 3.5.2,
which meets LineScope's PySpark 3.5 minimum. Later PySpark releases are accepted
without an upper version cap. Runtime permissions still determine which
execution plans and metrics are accessible.

### Workspace source

Notebook adapters preserve a workspace path when the runtime exposes it.
Otherwise, a stable virtual notebook identifier is used. Source is captured from
executed cells instead of assuming that a workspace notebook is a normal Python
file.

### Inline `%run`

Databricks `%run ./common` executes another notebook within the current notebook
environment. LineScope records resolvable notebook references and captured
source when the environment exposes it. Later calls can link to definitions from
that source. Paths with no accessible source remain references; LineScope does
not invent the child notebook's code. Capture uses source exposed by the running
shell; LineScope does not fetch remote notebook source.

### `dbutils.notebook.run`

This API creates a separate child execution. The parent can observe its path,
calling location, elapsed wait, success/failure, and safe correlation metadata.
It cannot directly trace line events in another process. Child runs appear under
the parent run; sensitive arguments are redacted at the integration boundary.
All argument values and returned content are omitted; the return type/length can
be retained without storing the result itself.

The [correlation API](../api/notebooks/childcontext.md) supports explicitly
merging a separately collected child result. Automatic remote
installation/bootstrap and transport are deployment concerns in this release.
Parent wait time is never presented as child line-level profiling.

Use the [Databricks example](../examples/databricks.md) and the manual
acceptance checklist in [testing](../development.md#testing) to validate your
runtime's supported hooks.
