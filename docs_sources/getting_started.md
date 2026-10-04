# Getting started

LineScope supports Python 3.11–3.15. Install the package from PyPI:

```console
pip install linescope
```

## Your first report

Run the bundled script from the checkout, or save the same code as `demo.py`:

:: example: script.py

```console
uv run python examples/script.py
```

A report opens in a new browser tab. Choose a file and click `load_readings`,
`rolling_slow`, or `rolling_fast` to visit its definition. Time spent inside
`sleep` belongs to your source line; standard-library internals are hidden. A
trace run measures elapsed intervals and has more instrumentation overhead than
sampling, so treat very short timings accordingly.

## Use the default sampling engine

Scalene is installed with LineScope on Python 3.11–3.14:

```console
linescope --backend scalene --include examples -m examples.sample_package
```

The [sample package](examples/package.md) has no explicit profiling block, so
the CLI controls collection. The adapter uses Scalene 2.3, including CPU
sampling on Windows. Memory collection has additional native startup
requirements; see [memory](user_guide/backends.md#memory). For Windows memory
profiling, use Python 3.12 or newer with a supported Scalene binary wheel.

## Notebooks and Spark

```console
pip install "linescope[notebook]"
pip install "linescope[spark]"
```

In an existing Spark or Databricks environment, install LineScope without
replacing the environment's bundled PySpark. See
[notebooks](user_guide/notebooks.md),
[Databricks](user_guide/notebooks.md#databricks), and
[Spark](user_guide/spark.md).
