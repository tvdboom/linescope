# Getting started

## Installation

### Latest release

Install or upgrade to the latest release from PyPI with
[uv](https://docs.astral.sh/uv/):

```console
uv pip install -U linescope
```

See [dependencies](dependencies.md) for package versions and platform support.

### Latest source

Install the latest source directly from the repository's `main` branch when
you want changes that have not reached a release yet.

```console
uv pip install -U git+https://github.com/tvdboom/linescope.git
```

### Optional dependencies

Choose the extras needed by your workload. Each command installs LineScope and
the dependencies for that integration. The base package remains sufficient for
Python source profiling and driver memory collection.

#### Scalene

The `scalene` extra installs the Scalene sampling backend for CPU profiling and
supported GPU measurements. It is available on Python 3.11–3.14. Select it
explicitly with `backend="scalene"` in the [profile]:

```console
uv pip install -U "linescope[scalene]"
```

On Python 3.15, use Trace or the built-in Tachyon sampling backend instead.
See [backends](user_guide/backends.md) for measurement and platform support.

#### Notebooks

The `notebook` extra installs IPython and ipykernel for cell magics, notebook
execution hooks, and inline reports. Use it in the environment running your
Jupyter kernel:

```console
uv pip install -U "linescope[notebook]"
```

See [notebooks](user_guide/notebooks.md) for single-cell and whole-session use.

#### All integrations

The `full` extra combines `scalene` and `notebook`:

```console
uv pip install -U "linescope[full]"
```

## Usage

### From Python

Wrap the code you want to measure in `profile`:

```python
from linescope import profile

with profile(memory=True):
    values = list(range(100_000))
    total = sum(value * value for value in values)
```

Run this in a Python script. When the block exits, LineScope opens an HTML
report in your browser. The default Trace backend records line timings and
exact execution counts. With `memory=True`, it also collects process RAM and
net retained Python allocation changes.

To save a report for later, set `display="none"` and keep the session:

```python
from linescope import profile

with profile(display="none") as session:
    total = sum(value * value for value in range(100_000))

session.save("linescope.html")
```

See [reports] for source navigation and report controls.

### From the CLI

Use the `linescope` command to run an existing Python script under the profiler
without changing its source:

```console
linescope your_script.py
```

The report opens in your browser when the script exits. To run an importable
module, use `-m` as you would with `python -m`:

```console
linescope -m your_package.your_module
```

Place LineScope options before the script path or `-m`. Arguments after the
script path or module name go to your program. For example, enable memory
collection, save the report, and pass `--rows` to your script:

```console
linescope --memory --output linescope.html your_script.py --rows 100000
```

Use `--display none` with `--output` when running without a browser:

```console
linescope --display none --output linescope.html your_script.py
```

Trace is the default backend. After installing the [Scalene extra](#scalene),
select sampling explicitly with `--backend`:

```console
linescope --backend scalene --memory your_script.py
```

Run `linescope --help` or see the [CLI reference](cli/linescope.md) for all
options. See [backends](user_guide/backends.md) for the available measurements.

### From a notebook

After installing the [notebook extra](#notebooks), load the extension in your
notebook:

```python
%load_ext linescope
```

Put `%%profile` on the first line of a cell and request an inline summary:

```python
%%profile --backend trace --display cell-summary

values = list(range(100_000))
total = sum(value * value for value in values)
```

After the cell finishes, a compact table appears inline with its source,
line timings, and execution counts. Add `--memory` to include memory changes.
Omit `--display cell-summary` to show the full report for that cell instead.

To show a summary after each cell across several cells, start a session:

```python
from linescope import profile

session = profile.start(backend="trace", display="cell-summary")
```

Run the cells you want to measure normally. Each cell displays its own summary
inline, while the session collects results for the complete notebook report.
Finish in a separate cell and display the full report:

```python
profile.stop()
session.show(inline=True)
```

You can also save it with `session.save("notebook.html")`. See
[notebooks](user_guide/notebooks.md) for notebook-wide profiling and Spark use.
