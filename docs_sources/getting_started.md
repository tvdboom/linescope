# Getting started

## Installation

Install LineScope in your script or notebook's environment. Python 3.11–3.15
is supported.

### Latest release

```console
uv pip install -U linescope
```

### Latest source

For unreleased changes from `main`:

```console
uv pip install -U git+https://github.com/tvdboom/linescope.git@main
```

### Optional dependencies

| Extra | Adds |
| --- | --- |
| `scalene` | CPU sampling and supported GPU metrics on Python 3.11–3.14 |
| `notebook` | IPython cell magics and inline reports |
| `full` | Both extras |

```console
uv pip install -U "linescope[full]"
```

Scalene is omitted on Python 3.15. See [dependencies](dependencies.md) for
platform support.

## Usage

### From Python

Wrap the code to measure in `profile`:

```python
from linescope import profile

with profile(memory=True):
    values = list(range(100_000))
    total = sum(value * value for value in values)
```

The report opens when the block ends. Trace is the default backend;
`memory=True` adds process RAM and retained Python allocation measurements.

To save without opening a browser:

```python
from linescope import profile

with profile(display="none") as session:
    total = sum(value * value for value in range(100_000))

session.save("linescope.html")
```

### From the CLI

```console
linescope your_script.py
linescope -m your_package.your_module
```

The report opens when the program exits. Put profiler options before the
target; arguments after it go to your program:

```console
linescope --memory --output report.html your_script.py --rows 100000
```

Use `--display none` to suppress the browser, or `--backend scalene` to select
sampling. See the [CLI reference](cli/linescope.md) for all options.

### From a notebook

Install the `notebook` extra, then load the extension:

```python
%load_ext linescope
```

Profile a cell:

```python
%%profile --display cell-summary

total = sum(value * value for value in range(100_000))
```

For several cells, start a session, run your cells, then stop:

```python
from linescope import profile

session = profile.start(display="end")
```

```python
profile.stop()
```

See [notebooks](user_guide/notebooks.md) for display modes and Databricks.
