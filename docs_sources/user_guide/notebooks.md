# Notebooks

Profile one cell with the IPython extension or several cells with the
start/stop API. Captured cells and imported project code appear in the same
report.

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

The report displays inline when the cell finishes and retains the source as it
was executed.

### Profile several cells

```python
from linescope import profile

session = profile.start(backend="trace", display="end")
```

Run the cells you want to investigate, then stop in another cell:

```python
profile.stop()
result = session.result
```

The default `display="end"` shows one report at stop. Use `display="cell"` for
live cumulative reports, or `display="none"` and
`session.save("notebook.html")` to save without displaying.

Reports display inline automatically. To open one in a browser, call
`session.show(inline=False)`. The `inline` option belongs to `show()`, rather
than session configuration.

Captured cells are numbered from **Cell 1** in capture order, independently of
kernel execution counts. Reports preserve revised snapshots and link to exact
cells and lines. If the notebook's identity is unavailable or ambiguous, its
label is `interactive`.

### Debug cell by cell

Use compact summaries to inspect each cell as you run it:

```python
from linescope import profile

session = profile.start(backend="trace", display="cell-summary")
```

Each summary shows that cell's measurements and called project code. Blank
lines, docstrings, and comments are omitted while original line numbers remain.
The full report keeps complete source. Select a metric heading to sort, or
**Source** to restore line order; sorting requires trusted notebook HTML output.

Rerunning a cell displays its new measurements while retaining previous runs
in the cumulative report. Failed cells show available results and leave
profiling active so you can fix the code and continue.

Sampling can miss short cells. Zero samples and unavailable measurements are
different: unknown values display as a dash. With `memory=True`, summaries show
process RAM changes and retained Python allocation changes separately.

Stop collection, then open or save the full report explicitly:

```python
profile.stop()
result = session.result
session.show(inline=True)
report_path = session.save("notebook.html")
```

Compact mode does not automatically show a full report at stop. For one cell,
use `%%profile --backend trace --display cell-summary`.

Live modes take collector snapshots at cell boundaries, which can add
substantial overhead with memory tracking. See
[backend performance](backends.md#performance) and the
[Quick Start notebook](../examples/notebooks/notebook_example.ipynb).

## Databricks

Install `linescope` as a cluster library so child notebooks can import it too.
Load the extension and use the same cell magic or start/stop API. LineScope uses
the runtime's PySpark and Databricks SDK; it does not install them. Spark
observation requires PySpark 3.5 or later and `spark=True` or `--spark`.

### Workspace source

LineScope captures executed cells and keeps the workspace path when available.
Otherwise, it uses a stable virtual notebook identifier.

### Inline `%run`

`%run ./common` executes another notebook in the current environment. LineScope
records notebook references and source exposed by the running shell, allowing
later calls to link to captured definitions. Inaccessible source remains a
reference; LineScope does not fetch it remotely.

### `dbutils.notebook.run`

Calls made while profiling automatically include child source and, for Python
notebooks, independently collected line measurements in the parent report.
Nested child calls are included too. No profiler cells or manual merging are
needed in the original child notebook.

LineScope runs a temporary sibling copy with profiling cells, passes the
original arguments, and merges the child result. The child inherits the
parent's backend and metric options, and report links retain the original
notebook path. Temporary notebooks and profile files are deleted after success
or failure. `dbutils.notebook.exit` preserves its return value; failed cells
finalize available measurements.

Automatic collection requires runtime SDK authentication and permission to
export the notebook and create/delete objects in its folder. The temporary
copy changes the context API's notebook path and cell positions. For workloads
that depend on those values, disable child collection:

```python
session = profile.start(child_notebooks=False)
```

Parent-side observation still records the call. If preparation is unavailable,
the original notebook runs once and any captured source remains readable.
Non-Python notebooks have source but no line measurements. Kernel restarts or
hard termination can prevent finalization; collection and cleanup limitations
appear as warnings.

Parent wait time stays separate from child line measurements. Invocation
metadata omits argument values, return content, and exception messages, but
reports still embed source snapshots. Review them before sharing.

See [Databricks checks](../development.md#databricks-checks) to validate support
in your runtime.
