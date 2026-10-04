# Getting started

LineScope supports Python 3.11–3.15. Install the package from PyPI:

```console
pip install linescope
```

## Your first report

Run the bundled script from the checkout, or save the same code as `demo.py`:

:: example: script_example.py

```console
uv run python examples/script_example.py
```

A report opens in a new browser tab. Choose a file and click `load_readings`,
`rolling_slow`, or `rolling_fast` to visit its definition. Time spent inside
`sleep` belongs to your source line; standard-library internals are hidden. A
Scalene run estimates line time by sampling and collects driver memory. Very
short lines may receive no samples. Use Python 3.11–3.14 for this demo. The
[shared memory collector](user_guide/backends.md#memory) needs no allocator
preload.

## Use the default collector

Trace is the default on Python 3.11–3.15. Enable process RAM and Python
allocation tracking without native allocator setup:

```console
linescope --memory -m examples.sample_package
```

Trace records line hits and wall intervals, plus net retained Python allocation
changes when requested. Python allocation peaks stay unavailable; observed
process RAM peaks are separate. See
[backends](user_guide/backends.md) for the feature comparison and memory scope.

## Use sampling

Scalene is installed with LineScope on Python 3.11–3.14:

```console
linescope --backend scalene --memory -m examples.sample_package
```

The [sample package](examples/package.md) has no explicit profiling block, so
the CLI controls collection. The adapter uses Scalene 2.3, including CPU
sampling on Windows. Memory uses the shared collector; see
[memory](user_guide/backends.md#memory) for its scope and limitations.

## Notebooks and Spark

```console
pip install "linescope[notebook]"
pip install "linescope[spark]"
pip install "linescope[databricks]"
```

In an existing Spark or Databricks environment, install LineScope without
replacing the environment's bundled PySpark. See
[notebooks](user_guide/notebooks.md),
[Databricks](user_guide/notebooks.md#databricks), and
[Spark](user_guide/spark.md).
