---
notebook: examples/notebooks/quickstart.ipynb
---

# Notebook session

[Download the notebook](notebooks/quickstart.ipynb). It contains a cell-magic example and a
multi-cell session, with no external data or network dependency.

```python
%load_ext linescope
```

```python
%%profile --backend trace
values = list(range(20_000))
total = sum(value * value for value in values)
```

Then start a session, run two separate cells, and stop it:

```python
from linescope import profile

session = profile.start(backend="trace", display="end")
```

```python
squares = [value * value for value in values]
```

```python
average = sum(squares) / len(squares)
```

```python
profile.stop()
```

The full report appears at the final stop rather than after every intermediate cell.
