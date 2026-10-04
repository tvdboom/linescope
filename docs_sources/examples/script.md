# Profile a Python script

The runnable example in `examples/script.py` generates sensor data, simulates
I/O, and compares two implementations of a rolling average. It uses the trace
backend so it runs without optional dependencies, then opens a report in a new
browser tab.

```console
uv run python examples/script.py
```

From a repository checkout with Just installed, `just demo-script` runs this
example and opens its report in your browser. `just demo` is an alias for the
same recipe.

:: example: script.py

The input wait belongs to its calling source line. Follow `load_readings`,
`rolling_slow`, and `rolling_fast` to their definitions and compare their line
measurements. The report contains the entire script, including unexecuted lines.
