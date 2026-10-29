<div align="center">
<a href="#" draggable="false" style="pointer-events: none; user-select: none;"><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/logo.png" alt="LineScope logo" width="280" draggable="false" style="pointer-events: none; user-select: none;" /></a>

## Your code. In the spotlight.

### A source profiler for Python and Spark
</div>

<br>

📜 Overview
-----------

| **General Information** | |
| --- | --- |
| **Repository** | [![Project Status: Active](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active) [![License: MIT](https://img.shields.io/github/license/tvdboom/linescope)](https://opensource.org/licenses/MIT) [![Downloads](https://img.shields.io/pepy/dt/linescope)](https://pepy.tech/project/linescope) [![PyPI version](https://img.shields.io/pypi/v/linescope)](https://pypi.org/project/linescope/) |
| **Build** | [![Publish](https://github.com/tvdboom/linescope/actions/workflows/publish.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/publish.yml) [![Linting and tests](https://github.com/tvdboom/linescope/actions/workflows/test.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/tvdboom/linescope/branch/master/graph/badge.svg)](https://codecov.io/gh/tvdboom/linescope) |
| **Code** | [![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue?logo=python)](https://www.python.org) [![uv-managed](https://img.shields.io/badge/uv-managed-blueviolet)](https://docs.astral.sh/uv/) [![PEP8](https://img.shields.io/badge/code%20style-pep8-orange.svg)](https://www.python.org/dev/peps/pep-0008/) [![ruff](https://custom-icon-badges.demolab.com/badge/Ruff-261230.svg?logo=ruff-logo)](https://docs.astral.sh/ruff/) [![ty](https://custom-icon-badges.demolab.com/badge/ty-261230.svg?logo=ty-astral-logo)](https://docs.astral.sh/ty/) |

<br>

<table>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/report.jpg" alt="LineScope overview with the most expensive source lines" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/source.jpg" alt="LineScope full-source heatmap with clickable function calls" width="100%" /></td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/functions.jpg" alt="LineScope function timings and source links" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/notebooks.jpg" alt="LineScope notebook session with captured cells" width="100%" /></td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/spark.jpg" alt="LineScope Spark actions and operator costs" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/master/images/files.jpg" alt="LineScope project source files and measured timings" width="100%" /></td>
</tr>
</table>

<br>

## LineScope

Profile Python source line by line, including notebook cells and Spark actions.
Reports contain source snapshots, timings, memory measurements, and definition
links in one HTML file. Library work is attributed to the calling project line.

## Install

Python 3.11–3.15 is supported. Install in your script or notebook's environment:

```console
uv pip install -U linescope
```

| Extra | Adds |
| --- | --- |
| `linescope[notebook]` | IPython cell magics and inline reports |
| `linescope[scalene]` | CPU sampling and GPU metrics on Python 3.11–3.14 |
| `linescope[full]` | Both extras |

For unreleased changes:

```console
uv pip install -U git+https://github.com/tvdboom/linescope.git@main
```

Spark and Databricks use the runtime's PySpark and SDK. Enable Spark observation
with `spark=True` or `--spark`.

## Use

### Terminal

```console
linescope --memory --output report.html your_script.py
linescope --output report.html -m your_package.your_module
```

The report opens in a browser. Add `--display none` to save without opening it.
Put profiler options before the target; subsequent arguments go to your program.

### Python

```python
from linescope import profile

with profile(memory=True, display="none") as session:
    total = sum(value * value for value in range(100_000))

session.save("report.html")
```

Trace is the default. Select `backend="scalene"` for sampling or GPU collection,
or `backend="tachyon"` for sampling on Python 3.15.

### Notebook

```python
%load_ext linescope
```

```python
%%profile --display cell-summary

total = sum(value * value for value in range(100_000))
```

## Documentation

- [Getting started](https://tvdboom.github.io/linescope/latest/getting_started/)
- [User guide](https://tvdboom.github.io/linescope/latest/user_guide/introduction/)
- [Notebooks and Databricks](https://tvdboom.github.io/linescope/latest/user_guide/notebooks/)
- [Spark](https://tvdboom.github.io/linescope/latest/user_guide/spark/)
- [AI suggestions](https://tvdboom.github.io/linescope/latest/user_guide/ai/)
- [API reference](https://tvdboom.github.io/linescope/latest/api/configuration/config/)
- [CLI reference](https://tvdboom.github.io/linescope/latest/cli/linescope/)

## Contribute

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
uv sync --locked
```

See the [development guide](https://tvdboom.github.io/linescope/latest/development/)
for tests and contribution instructions. LineScope is [MIT licensed](LICENSE).
