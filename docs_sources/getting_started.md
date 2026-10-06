# Getting started

LineScope supports Python 3.11–3.15. Install it in the same environment as the
script or notebook you want to profile.

## Installation

### Latest release

Install or upgrade to the latest release from PyPI with
[uv](https://docs.astral.sh/uv/):

```console
uv pip install --upgrade linescope
```

Use an existing virtual environment, or create one with `uv venv` first.
Activate it before running the usage examples: `.venv\Scripts\Activate.ps1` in
PowerShell, or `source .venv/bin/activate` on Linux and macOS. If you use pip,
the equivalent installation command is:

```console
python -m pip install --upgrade linescope
```

Replace `uv pip` with `python -m pip` in the installation commands below if
you use pip.
The base package includes the default Trace backend, the CLI, self-contained
HTML reports, and driver memory collection. Trace needs no optional extras.
See [dependencies](dependencies.md) for package versions and platform support.

### Latest source

Install the latest source directly from the repository's `main` branch when
you want changes that have not reached a release yet. This requires Git:

```console
uv pip install --upgrade "git+https://github.com/tvdboom/linescope.git@main"
```

You can also request extras directly from Git. For example, install the latest
source with all optional integrations from the repository's default branch:

```console
uv pip install "linescope[full] @ git+https://github.com/tvdboom/linescope"
```

For a reproducible source installation, replace `main` with a specific tag or
commit in the first command.

### Optional dependencies

Choose the extras needed by your workload. Each command installs LineScope and
the dependencies for that integration; the base package remains sufficient for
Python source profiling and driver memory collection.

#### Scalene

The `scalene` extra installs the Scalene sampling backend for CPU profiling and
supported GPU measurements. It is available on Python 3.11–3.14; the dependency
is omitted on Python 3.15. Select it explicitly with `backend="scalene"` or
`--backend scalene` after installation:

```console
uv pip install --upgrade "linescope[scalene]"
```

On Python 3.15, use Trace or the built-in Tachyon sampling backend instead.
See [backends](user_guide/backends.md) for measurement and platform support.

#### Notebooks

The `notebook` extra installs IPython and ipykernel for cell magics, notebook
execution hooks, and inline reports. Use it in the environment running your
Jupyter kernel:

```console
uv pip install --upgrade "linescope[notebook]"
```

See [notebooks](user_guide/notebooks.md) for single-cell and whole-session use.

#### Spark

The `spark` extra installs PySpark for profiling driver source alongside Spark
actions, executed plans, and available task metrics. A local Spark runtime also
needs a compatible [Java] installation; see the [local Spark
example](examples/notebooks/spark_example.ipynb) for setup:

```console
uv pip install --upgrade "linescope[spark]"
```

In an existing managed Spark environment, reuse its bundled PySpark and install
the base package or the notebook integration you need.

#### Databricks

The `databricks` extra includes notebook support and installs `databricks-sdk`
for workspace notebook source capture and child notebook report retrieval:

```console
uv pip install --upgrade "linescope[databricks]"
```

Install it as a cluster library when child notebooks also need LineScope. This
extra reuses the runtime's PySpark; see
[Databricks](user_guide/notebooks.md#databricks) for setup and authentication.

#### All integrations

The `full` extra combines `scalene`, `notebook`, `spark`, and `databricks`:

```console
uv pip install --upgrade "linescope[full]"
```

Scalene is still omitted on Python 3.15. The extra includes PySpark, so choose
`linescope[databricks]` in Databricks to reuse its bundled Spark runtime.
Development tools and the GPU demo workload are separate dependency groups.

You can combine selected extras instead of installing all of them:

```console
uv pip install --upgrade "linescope[scalene,notebook]"
```

### Contributing

Clone the repository and install its locked development environment with uv:

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
uv sync --locked
```

This installs LineScope in editable mode and includes the default `dev` group
for documentation, linting, tests, and notebook demos. Add `--extra full` to
`uv sync --locked` when you need all runtime integrations. See
[development](development.md) for the complete setup and test matrix.

## Usage

### Your first report

Save this example as `demo.py`:

:: example: script_example.py

Run it in the environment where you installed LineScope:

```console
python demo.py
```

From a repository checkout, you can run the bundled copy instead:

```console
uv run python examples/script_example.py
```

A report opens in a new browser tab. Choose a file and click `load_readings`,
`rolling_slow`, or `rolling_fast` to visit its definition. Time spent inside
`sleep` belongs to your source line; standard-library internals are hidden. The
default Trace backend records line timings and exact execution counts on Python
3.11–3.15. This demo also collects driver memory. The
[shared memory collector](user_guide/backends.md#memory) needs no allocator
preload.

### Profile a script or module

Run your own script under the default Trace backend without changing its
source. Enable process RAM and Python allocation tracking with `--memory`:

```console
linescope --memory --output linescope.html your_script.py
```

The CLI saves the report and opens it in a browser. Place LineScope options
before the script path; arguments after it go to your script. Use `-m` for an
importable module, or `--display none` to save a report without opening it:

```console
linescope --memory -m your_package.your_module
linescope --display none --output linescope.html your_script.py
```

Trace records line hits and wall intervals, plus net retained Python allocation
changes when requested. Python allocation peaks stay unavailable; observed
process RAM peaks are separate. See
[backends](user_guide/backends.md) for the feature comparison and memory scope.

### Use sampling

After installing the [Scalene extra](#scalene) on Python 3.11–3.14, select the
backend explicitly. From the checkout, profile the bundled sample package:

```console
uv run linescope --backend scalene --memory -m examples.sample_package
```

The bundled sample package has no explicit profiling block, so the CLI controls
collection. The adapter uses Scalene 2.3, including CPU
sampling on Windows. Memory uses the shared collector; see
[memory](user_guide/backends.md#memory) for its scope and limitations.

### Notebooks and Spark

After installing the [notebook extra](#notebooks), load the extension in your
notebook:

```python
%load_ext linescope
```

Then profile one cell:

```python
%%profile --backend trace

values = list(range(100_000))
total = sum(value * value for value in values)
```

The report appears inline after the cell finishes. Spark integration observes
the actions your workload already performs and never forces additional work.
See [notebooks](user_guide/notebooks.md),
[Databricks](user_guide/notebooks.md#databricks), and
[Spark](user_guide/spark.md).
