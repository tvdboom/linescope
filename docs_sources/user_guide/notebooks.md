# Notebooks

## Jupyter notebooks

Install `linescope[notebook]` in the kernel's environment, then load the
extension:

```python
%load_ext linescope
```

### Profile one cell

```python
%%profile

total = sum(value * value for value in range(100_000))
```

The report displays inline when the cell finishes.

### Profile several cells

```python
from linescope import profile

session = profile.start(display="end")
```

Run your cells, then stop in another cell:

```python
profile.stop()
```

| Display mode | Output |
| --- | --- |
| `end` | One report at stop; the default |
| `cell` | A cumulative report after each cell |
| `cell-summary` | Each cell's own compact summary |
| `none` | No automatic display |

Save with `session.save("notebook.html")`. Use `session.show(inline=False)` to
open a browser; `inline` is an option of `show()`, not session configuration.

Reports keep executed cells in notebook order. Earlier definition cells appear
when their code runs during collection. Cell positions count from one, including
markdown cells. Save notebook edits before profiling so positions can be found.
Ambiguous positions use execution numbers or cell IDs; unknown notebooks use
`interactive`.

Rerunning unchanged source accumulates its measurements. Edited cells keep
separate source versions with their own line links.

### Debug cell by cell

```python
session = profile.start(display="cell-summary")
```

Summaries omit blank lines, comments, and docstrings while keeping original line
numbers. The full report keeps complete source. Column sorting requires trusted
notebook HTML output.

Reruns show new measurements and retain earlier runs in the cumulative report.
A failed cell leaves profiling active. To finish and show the full report:

```python
profile.stop()
session.show(inline=True)
```

Compact mode does not show a full report automatically. Live modes add overhead,
especially with memory tracking. See the
[Quick Start notebook](../examples/notebooks/notebook_example.ipynb).

## Databricks

Install LineScope as a cluster library so child notebooks can import it. Use
the same cell magic and start/stop API. Spark observation needs the runtime's
PySpark 3.5 or later and `spark=True` or `--spark`.

### Workspace source

Captured cells keep their workspace path when available, or a stable virtual
notebook ID otherwise.

### Inline `%run`

`%run ./common` executes in the current environment. LineScope records source
and references exposed by the shell. Inaccessible source remains a reference;
it is not fetched remotely.

### `dbutils.notebook.run`

Python child notebooks are profiled automatically and merged into the parent
report, including nested calls. This requires SDK authentication and permission
to export the notebook and create/delete objects in its folder.

LineScope runs a temporary sibling copy with profiling cells. The copy inherits
the parent's backend and metric options, receives the original arguments, and
keeps report links to the original source. Temporary notebooks and result files
are removed after success or failure. `dbutils.notebook.exit` keeps its return
value.

The copy has a different context API path and cell positions. For code that
depends on those values, disable child collection:

```python
session = profile.start(child_notebooks=False)
```

If preparation fails, the original notebook runs once. Available source is
still included; non-Python notebooks have no line measurements. Hard termination
can prevent finalization or cleanup. Check report warnings for limitations.

Parent wait time and child measurements stay separate. Argument values, return
content, and exception messages are excluded from invocation metadata. Source
snapshots may still contain private data.

See [Databricks checks](../development.md#databricks-checks) for runtime
testing.
