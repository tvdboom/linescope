# Notebooks

LineScope captures executed notebook cells as source snapshots and connects them
to the same report as imported project code. Use the extension for one cell or
the start/stop API for a notebook-wide session. Trace is the default backend;
select another backend explicitly when you want sampling.

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

The report displays inside the notebook after the cell finishes. The whole
input is captured as a virtual source unit; it remains
reproducible if the cell is later edited.

### Profile several cells

```python
from linescope import profile

session = profile.start(backend="trace", display="end")
```

Run the cells you want to investigate, then finish in another cell:

```python
profile.stop()
result = profile.result
```

`profile.stop()` returns None; use `profile.result` or `session.result` to
inspect measurements programmatically. The default generates one full report
at stop. `display="cell"` requests live updates of the full cumulative report;
`display="none"` keeps the session headless for later
`session.save("notebook.html")`.

Full reports display inside the notebook automatically. Choose a browser with
`session.show(inline=False)` when showing a report explicitly. The `inline`
option belongs only to `show()`, not `profile.start()` or configuration.
Repeated live rendering has a cost; see
[backend performance](backends.md#performance).

Cell source is attached to stable notebook identifiers. Links into imported
project functions use the same symbol resolution as Python files. Notebook
function definitions that are available to the session can also be represented
as virtual source snapshots.

Reports show only the notebook filename and extension, without its directory,
throughout Files, Functions, Memory, and source views. The full captured path
still identifies each notebook and its source links. If frontend metadata is
unavailable, LineScope checks local Jupyter sessions for the active kernel or
the input notebook of its owning nbconvert process. Unavailable or ambiguous
notebook identities keep the `interactive` label. Source tables place code in
the final column; select **Source** after sorting by a metric to restore line
order within each cell.

### Debug cell by cell

Use compact summaries to inspect each cell as you run it:

```python
from linescope import profile

session = profile.start(backend="trace", display="cell-summary")
```

Each cell gets a compact table in source order, including lines with zero
samples. Blank lines, docstrings, and comment-only rows are omitted. A thicker
divider marks gaps; hover over the next line number for the omitted count.
Original line numbers are preserved. Called project source snapshots also
appear, with red shading for measured time hotspots. The full report retains
complete source.
The narrow, unnamed first column contains line numbers, followed by Time and
the other metrics, with complete source text in the final column. The table
fits its content and keeps source lines unwrapped; narrow outputs scroll
horizontally. Hover over a line number to see the path of called project code.
Click a metric column's arrow to sort its values; click again to reverse the
order. Unknown measurements stay last. Click Source to sort by original line
order, ascending by default. Source-gap dividers return with that order. Sorting
works in trusted notebook HTML outputs and stays local to each cell table.
Compact summaries always display inside the notebook. Timings and hits belong
to that execution, including project
functions called from earlier cells or imported modules.
Rerunning a cell shows its new measurements, while the session retains all runs
for the full report. Failed cells show the available results and leave profiling
active so you can fix the cell and continue.

The summary contains only the table. Elapsed time, execution outcomes, and
collection diagnostics remain available in the full report and result object.
Cells without line measurements still show their captured source.

Sampling backends show samples when available. Short cells may have no sampled
line measurements. Known zero counts appear as `0`; unknown metrics stay
unavailable and appear as `—`. With `memory=True`, the
summary shows observed RAM changes and net retained Python allocation changes
separately. Freed allocations remain attributed to their original source line;
cumulative memory peaks cannot describe an individual cell's peak.

Finish collection when you are done:

```python
profile.stop()
result = profile.result
```

Compact mode does not automatically display a full report at stop. Open or save
the complete cumulative report explicitly:

```python
_ = session.show(inline=True)  # Display the full overview in this cell.
report_path = session.save("notebook.html")
```

Assign the return value of `show()` to suppress its HTML string in the cell's
text output. Use `session.show(inline=False)` to open a browser tab instead.

For a single cell, use `%%profile --backend trace --display cell-summary`.
Live summaries take collector snapshots before and after each cell, so they add
overhead, especially with allocation tracking enabled.

Download [Notebook Quick Start](../examples/notebooks/notebook_example.ipynb)
for a complete local walkthrough with cell summaries and a report shown at
the end.

## Databricks

Install `linescope[databricks]` as a cluster library so it is available in child
notebooks too, then load the
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

Calls made while profiling automatically include the child notebook's source
and, for Python notebooks, independently collected line measurements in the
parent HTML. No profiler cells or manual result merging are required in the
original child notebook. Nested child calls follow the same collection path.

LineScope exports all child cells, creates a temporary sibling notebook with
profiler startup and cleanup cells, and runs that copy with the original
arguments. The child writes a JSON profile to a reserved workspace file. The
parent merges it and deletes both temporary objects after success or failure.
`dbutils.notebook.exit` keeps its original value; a failed cell finalizes the
available child measurements. The child uses the parent's backend and metric
options, and its report source keeps the original notebook path.

Automatic collection uses the Databricks SDK's runtime authentication and needs
permission to export the original notebook and create/delete objects in its
folder. Relative calls keep their original folder. The running notebook's
context API exposes the temporary copy's path, and inserted cells change cell
positions. Use `child_notebooks=False` for workloads that depend on those
values.
Kernel restarts, hard termination, and unavailable child libraries can prevent
profile finalization. Non-Python notebooks include source with unknown line
measurements. Reports record collection and cleanup limitations as warnings.

If preparation is unavailable, the original notebook runs once and any captured
source remains readable. Parent wait time is kept separate from child line
measurements. Argument values, return content, and exception messages are
omitted from invocation metadata; reports still contain the user's source
snapshots.

Use `profile.start(child_notebooks=False)` to retain parent-side observation
only. Profiles collected through a separate deployment or artifact store can
also be merged through the notebook correlation interfaces.

Use the manual [Databricks checks](../development.md#databricks-checks) to
validate your runtime's supported hooks.
