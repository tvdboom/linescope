# Profile a Python script

The runnable example in `examples/script_example.py` generates sensor data,
simulates I/O, and compares two implementations of a rolling average. It uses
Scalene with driver memory collection enabled, then opens a report in a new
browser tab.
The demo processes 96,000 readings so sampling can observe repeated work and
repeated operations. It takes a few seconds; brief lines can still be
unsampled. The shared memory collector shows process RAM separately from
retained Python allocations. Use Python 3.11–3.14 for the Scalene demos; no
[allocator preload](../user_guide/backends.md#memory) is needed.

```console
uv run python examples/script_example.py
```

From a repository checkout with Just installed, `just demo-script` runs this
example and opens its report in your browser. `just demo` is an alias for the
same recipe.

:: example: script_example.py

The input wait belongs to its calling source line. Follow `load_readings`,
`rolling_slow`, and `rolling_fast` to their definitions and compare their line
measurements. The report contains the entire script, including unexecuted lines.
