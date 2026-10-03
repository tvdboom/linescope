# Getting started

LineScope supports Python 3.11–3.14. Install from the checkout before the first package release:

```console
uv pip install -e .
```

Once published, the standard package installation is:

```console
pip install linescope
```

## Your first report

Save this as `demo.py`:

```python
from time import sleep
from linescope import profile


def load():
    sleep(0.025)
    return list(range(10_000))


def calculate(values):
    return sum(value * value for value in values)


with profile(backend="trace", display="none") as session:
    values = load()
    result = calculate(values)

session.save("linescope.html")
```

```console
python demo.py
```

Open `linescope.html` in a browser. Choose a file and click `load` or `calculate` to visit its
definition. Time spent inside `sleep` belongs to your source line; standard-library internals
are hidden. A trace run measures elapsed intervals and has more instrumentation overhead than
sampling, so treat very short timings accordingly.

## Use the default sampling engine

On a platform supported by Scalene:

```console
pip install "linescope[scalene]"
linescope --backend scalene --output linescope.html demo.py
```

For CLI profiling, remove the explicit profiling block from your target script to avoid starting
two profilers at once. Scalene is an optional dependency even though it is the default engine;
requesting an unavailable backend raises a clear installation/platform error. The adapter uses
Scalene 2.3, including CPU sampling on Windows. Memory collection has additional native startup
requirements; see [memory](user_guide/memory.md).
For Windows memory profiling, use Python 3.12 or newer with a supported Scalene binary wheel.

## Notebooks and Spark

```console
pip install "linescope[notebook]"
pip install "linescope[spark]"
```

In an existing Spark or Databricks environment, install LineScope without replacing the
environment's bundled PySpark. See [notebooks](user_guide/notebooks.md),
[Databricks](user_guide/databricks.md), and [Spark](user_guide/spark.md).
