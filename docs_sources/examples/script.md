# Profile a Python script

The runnable example in `examples/profile_script.py` simulates I/O, transforms a small dataset,
and saves a report. It uses the trace backend so it runs without optional dependencies.

```console
uv run python examples/profile_script.py
```

```python
from time import sleep
from linescope import profile


def load_values():
    sleep(0.02)
    return list(range(20_000))


def normalize(values):
    scale = max(values) or 1
    return [value / scale for value in values]


with profile(backend="trace", display="none") as session:
    values = load_values()
    normalized = normalize(values)

session.save("linescope.html")
```

The load's wait belongs to its calling source line. `normalize` links to its definition. The
saved file contains the entire script, including lines without measurements.
