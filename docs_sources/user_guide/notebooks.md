# Jupyter notebooks

Install the `notebook` extra and load the extension:

```python
%load_ext linescope
```

## Profile one cell

```python
%%profile --backend trace

values = list(range(100_000))
total = sum(value * value for value in values)
```

The report appears after the cell finishes. The whole input is captured as a virtual source
unit; it remains reproducible if the cell is later edited.

## Profile several cells

```python
from linescope import profile

session = profile.start(backend="trace", display="end")
```

Run the cells you want to investigate, then finish in another cell:

```python
profile.stop()
```

The default generates one full report at stop. `display="cell"` requests live per-cell updates;
`display="none"` keeps the session headless for later `session.save("notebook.html")`.

Cell source is attached to stable notebook identifiers. Links into imported project functions
use the same symbol resolution as Python files. Notebook function definitions that are available
to the session can also be represented as virtual source snapshots.

Download the [notebook example](../examples/notebook.md) for a complete local walkthrough.
