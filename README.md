<div align="center">
<img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/logo.png" alt="LineScope logo" width="280" />

## Your code. In the spotlight.

### A source profiler for Python, notebooks, and Spark
</div>

<br>

📜 Overview
-----------

**General information** | |
--- | ---
**Repository** | [![Status: alpha](https://img.shields.io/badge/status-alpha-0f766e)](https://github.com/tvdboom/linescope) [![License: MIT](https://img.shields.io/badge/license-MIT-0f766e)](https://github.com/tvdboom/linescope/blob/main/LICENSE)
**Build** | [![Linting and tests](https://github.com/tvdboom/linescope/actions/workflows/test.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/test.yml) [![Publish](https://github.com/tvdboom/linescope/actions/workflows/publish.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/publish.yml) [![codecov](https://codecov.io/gh/tvdboom/linescope/branch/main/graph/badge.svg)](https://codecov.io/gh/tvdboom/linescope)
**Code** | [![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776ab?logo=python&logoColor=white)](https://www.python.org) [![uv](https://img.shields.io/badge/uv-managed-de5fe9)](https://docs.astral.sh/uv/) [![Ruff](https://img.shields.io/badge/lint-Ruff-d7ff64)](https://docs.astral.sh/ruff/) [![ty](https://img.shields.io/badge/types-ty-261230)](https://docs.astral.sh/ty/)

[Documentation](https://tvdboom.github.io/linescope/) ·
[Getting started](https://tvdboom.github.io/linescope/latest/getting_started/) ·
[User guide](https://tvdboom.github.io/linescope/latest/user_guide/introduction/) ·
[API](https://tvdboom.github.io/linescope/latest/api/) · [CLI](https://tvdboom.github.io/linescope/latest/cli/options/) · [Examples](https://github.com/tvdboom/linescope/tree/main/examples)

> Initial development release: hosted documentation, package publication, and CI badges become
> available after the first repository deployment. Build all documentation locally now.

<br>

<table>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/report.jpg" alt="LineScope overview with the most expensive source lines" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/source.jpg" alt="LineScope full-source heatmap with clickable function calls" width="100%" /></td>
</tr>
<tr><td align="center">Find the expensive line</td><td align="center">Follow the source</td></tr>
</table>

<br>

💡 Introduction
---------------

LineScope shows where time is spent in **your Python source, line by line**. Browse full files
and notebook cells, follow function and class links, and inspect Spark executions beside the
driver code that triggered them. Work inside pandas, native libraries, I/O, and other dependencies
stays attributed to the relevant source line instead of filling the report with their internals.

The result is a single HTML file with its own source snapshots, styles, and scripts. Open it
offline, revisit it after code changes, or share it after reviewing the embedded source.

<br>

❗ Why use LineScope?
-------------------

- **Source first.** Full files and cells, timing heatmaps, and unexecuted lines in context.
- **Follow your code.** Click individual function, class, and reliably resolved method calls.
- **Several ways to work.** Context managers, explicit start/stop, CLI scripts/modules, and cell magic.
- **Notebook sessions.** Capture multiple cells and display one report when you stop.
- **Spark aware.** Observe real actions and available executed plans without forcing lazy work.
- **Honest measurements.** Keep missing metrics unknown and driver time separate from executor work.
- **Portable reports.** No server or CDN needed to view the generated HTML.
- **Pluggable collection.** Scalene sampling by default; an explicit trace backend for portable use.
- **Python only.** Universal wheels, no Rust or frontend compilation, MIT licensed.

<br>

Installation
------------

From this checkout:

```console
uv pip install -e .
```

After the first PyPI release:

```console
pip install linescope
```

The base installation has no runtime dependencies and includes the trace collector. For the
default Scalene engine, install the optional extra on a supported platform:

```console
pip install "linescope[scalene]"
```

Scalene 2.3 CPU sampling supports Windows as well as supported Linux/macOS environments. Native
memory collection has additional platform/startup requirements. Notebook and local Spark
dependencies are available as `notebook` and `spark` extras. Databricks users should keep their runtime's existing PySpark installation.

<br>

🚀 Getting started
------------------

Save a profile without opening a browser:

```python
from time import sleep
from linescope import profile


def load_values():
    sleep(0.02)
    return list(range(20_000))


with profile(backend="trace", display="none") as session:
    values = load_values()
    total = sum(value * value for value in values)

session.save("linescope.html")
```

Profile an existing script or module:

```console
linescope --backend trace --output linescope.html script.py
linescope --backend trace --include mypackage -m mypackage.job
```

In a notebook, load `%load_ext linescope`, then run:

```python
%%profile --backend trace
total = sum(value * value for value in range(20_000))
```

Use `profile.start()` and `profile.stop()` for several cells, or `spark=True` to observe supported
Spark driver actions. See the [notebook examples](https://github.com/tvdboom/linescope/tree/main/examples/notebooks) and
[local Spark example](https://github.com/tvdboom/linescope/blob/main/examples/local_spark.py).

<br>

📚 Documentation
----------------

| Topic | What you will find |
| --- | --- |
| [Getting started](https://tvdboom.github.io/linescope/latest/getting_started/) | Installation and your first report |
| [Configuration](https://tvdboom.github.io/linescope/latest/user_guide/configuration/) | Project defaults, scopes, display behavior |
| [Reports](https://tvdboom.github.io/linescope/latest/user_guide/reports/) | Heatmaps, source navigation, timing semantics |
| [Backends](https://tvdboom.github.io/linescope/latest/user_guide/backends/) | Sampling, tracing, capability limitations |
| [Notebooks](https://tvdboom.github.io/linescope/latest/user_guide/notebooks/) | Cell magic and notebook-wide sessions |
| [Databricks](https://tvdboom.github.io/linescope/latest/user_guide/databricks/) | Workspace source and child-run relationships |
| [Spark](https://tvdboom.github.io/linescope/latest/user_guide/spark/) | Lazy actions, plans, distributed metrics |
| [API](https://tvdboom.github.io/linescope/latest/api/) / [CLI](https://tvdboom.github.io/linescope/latest/cli/options/) | Reference and invocation details |
| [Development](https://tvdboom.github.io/linescope/latest/contributing/) | Setup, architecture, testing, docs, releases |
| [Dependencies](https://tvdboom.github.io/linescope/latest/dependencies/) | Runtime extras and development groups |

<br>

Scope and limitations
---------------------

This initial release profiles the current Python process. The trace backend instruments the
current thread and does not collect memory. Spark support covers classic PySpark driver
DataFrame/writer actions and available plan metrics; executor UDFs, RDD/streaming actions, and
Spark Connect plans are outside this release. Separate Databricks child line measurements
require explicit child instrumentation and result correlation. Tachyon and attach-to-PID remain
future backends. Sampling results are estimates, not exact nanosecond measurements.

<br>

🛠 Development
--------------

```console
uv sync --locked
uv run pytest
uv run tox -e lint,docs
uv run tox -m unit
uv run python -m mkdocs serve
uv build
```

The Backtide-style workflow includes Ruff, ty, pre-commit, pytest coverage, tox-uv, a cross-platform
Python 3.11–3.14 CI matrix, strict Material documentation, and versioned release workflows. Real
Spark checks run in optional local mode; normal tests mock Spark/Databricks boundaries and need
no cluster. See [TESTING.md](https://github.com/tvdboom/linescope/blob/main/TESTING.md), [AGENTS.md](https://github.com/tvdboom/linescope/blob/main/AGENTS.md), and the
[Code of Conduct](https://github.com/tvdboom/linescope/blob/main/CODE_OF_CONDUCT.md).

Documentation helpers and base styling are adapted from [Backtide](https://github.com/tvdboom/backtide).
LineScope and those adaptations are covered by the [MIT license](https://github.com/tvdboom/linescope/blob/main/LICENSE).
