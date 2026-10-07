<div align="center">
<a href="#" draggable="false" style="pointer-events: none; user-select: none;"><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/logo.png" alt="LineScope logo" width="280" draggable="false" style="pointer-events: none; user-select: none;" /></a>

## Your code. In the spotlight.

### A refreshingly simple source profiler for Python and Spark.
</div>

<br>

📜 Overview
-----------

| **General Information** | |
| --- | --- |
| **Repository** | [![Project Status: Active](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active) [![License: MIT](https://img.shields.io/github/license/tvdboom/linescope)](https://opensource.org/licenses/MIT) [![Downloads](https://static.pepy.tech/badge/linescope)](https://pepy.tech/project/linescope) [![PyPI version](https://img.shields.io/pypi/v/linescope)](https://pypi.org/project/linescope/) |
| **Build** | [![Publish](https://github.com/tvdboom/linescope/actions/workflows/publish.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/publish.yml) [![Linting and tests](https://github.com/tvdboom/linescope/actions/workflows/test.yml/badge.svg)](https://github.com/tvdboom/linescope/actions/workflows/test.yml) [![codecov](https://codecov.io/gh/tvdboom/linescope/branch/main/graph/badge.svg)](https://codecov.io/gh/tvdboom/linescope) |
| **Code** | [![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue?logo=python)](https://www.python.org) [![uv-managed](https://img.shields.io/badge/uv-managed-blueviolet)](https://docs.astral.sh/uv/) [![PEP8](https://img.shields.io/badge/code%20style-pep8-orange.svg)](https://www.python.org/dev/peps/pep-0008/) [![ruff](https://custom-icon-badges.demolab.com/badge/Ruff-261230.svg?logo=ruff-logo)](https://docs.astral.sh/ruff/) [![ty](https://custom-icon-badges.demolab.com/badge/ty-261230.svg?logo=ty-astral-logo)](https://docs.astral.sh/ty/) |

<br>

<table>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/report.jpg" alt="LineScope overview with the most expensive source lines" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/source.jpg" alt="LineScope full-source heatmap with clickable function calls" width="100%" /></td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/functions.jpg" alt="LineScope function timings and source links" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/notebooks.jpg" alt="LineScope notebook session with captured cells" width="100%" /></td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/spark.jpg" alt="LineScope Spark actions and operator costs" width="100%" /></td>
<td><img src="https://raw.githubusercontent.com/tvdboom/linescope/main/images/files.jpg" alt="LineScope project source files and measured timings" width="100%" /></td>
</tr>
</table>

<br>

💡 Introduction
---------------

LineScope shows where time is spent in **your Python source, line by line**.
Browse full files and notebook cells, follow function and class links, and
inspect Spark executions beside the driver code that triggered them. Work inside
pandas, native libraries, I/O, and other dependencies stays attributed to the
relevant source line instead of filling the report with their internals.

The result is a single HTML file with its own source snapshots, styles, and
scripts. Open it offline, revisit it after code changes, or share it after
reviewing the embedded source.

<br>

❗ Why use LineScope?
-------------------

- **Source first.** Full files and cells, timing heatmaps, and unexecuted lines
  in context.
- **Follow your code.** Click individual function, class, and resolved method calls.
- **Several ways to work.** Context managers, explicit start/stop, CLI scripts/modules,
  and cell magic.
- **Spark aware.** Observe real actions and available executed plans without
  forcing lazy work.
- **Honest measurements.** Keep missing metrics unknown and driver time separate
  from executor work.
- **Portable reports.** No server or CDN needed to view the generated HTML.
- **Pluggable collection.** Choose from 3 different profiling backends: trace, scalene
  or trachyon.

<br>

🚀 Getting started
------------------

## Installation

Install or upgrade LineScope in the same environment as your script or
notebook. Python 3.11–3.15 is supported:

```console
uv pip install --upgrade linescope
```

Use an existing virtual environment, or create one with `uv venv` first.
Activate it with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on Linux and macOS. With pip, use
`python -m pip install --upgrade linescope`; you can replace `uv pip` with
`python -m pip` in the commands below.

The base package includes the default Trace profiler, CLI, HTML reports, and
driver memory collection.

### Latest source

Install unreleased changes directly from the `main` branch. Git is required:

```console
uv pip install --upgrade "git+https://github.com/tvdboom/linescope.git@main"
```

### Optional dependencies

- **`scalene`** installs Scalene for CPU sampling and supported GPU measurements
  on Python 3.11–3.14. Select it with `--backend scalene` or
  `backend="scalene"`; it is omitted on Python 3.15.

  ```console
  uv pip install --upgrade "linescope[scalene]"
  ```

- **`notebook`** installs IPython and ipykernel for cell magics, notebook
  capture, and inline reports.

  ```console
  uv pip install --upgrade "linescope[notebook]"
  ```

- **`full`** installs both extras. Scalene is omitted on Python 3.15;
  development tools and GPU demo dependencies are separate.

  ```console
  uv pip install --upgrade "linescope[full]"
  ```

Combine individual extras with `linescope[scalene,notebook]`. Spark and
Databricks integration use the runtime's existing PySpark and Databricks SDK;
LineScope does not install either library. Install the base package or
`linescope[notebook]` in those environments. Spark profiling is opt-in with
`spark=True` or `--spark`, and requires PySpark to be available.

Install the latest source with all integrations directly from Git:

```console
uv pip install "linescope[full] @ git+https://github.com/tvdboom/linescope"
```

### Contributing

Install the locked development environment from a checkout:

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
uv sync --locked
```

See the [installation guide] for more setup details and the
[development guide] for contribution checks.

[installation guide]:
  https://tvdboom.github.io/linescope/latest/getting_started/#installation
[development guide]: https://tvdboom.github.io/linescope/latest/development/

## Usage

### From the terminal

Run your own script under LineScope without changing its source:

```console
linescope --backend trace --output linescope.html your_script.py
```

The script runs normally, then LineScope saves `linescope.html` in the current
directory and opens it in a browser. The report contains your source with
line-by-line timings and works offline.

Place LineScope options before the script path. Arguments after the path go to
your script. You can also profile an importable module from your project root:

```console
linescope --backend trace --output linescope.html your_script.py --input data.csv
linescope --backend trace --output linescope.html -m your_package.your_module
```

To save the report without opening a browser:

```console
linescope --backend trace --display none --output linescope.html your_script.py
```

### From Python code

Wrap the work you want to measure with `profile` in your own script. Here,
`main()` is your existing entry point; replace it with the calls you want to
profile:

```python
from linescope import profile

if __name__ == "__main__":
    with profile(backend="trace", output="linescope.html") as session:
        main()
```

Run the script as usual:

```console
python your_script.py
```

When the block exits, profiling stops and the report is saved and opened. For
headless use, add `display="none"` to `profile(...)` and call
`session.save("linescope.html")` after the block to write the report.

<br>

📘 Documentation
----------------

| **Relevant links** | |
| --- | --- |
| ⭐ **[About](https://tvdboom.github.io/linescope/latest/about/)** | Learn more about the package. |
| 🚀 **[Getting started](https://tvdboom.github.io/linescope/latest/getting_started/)** | New to LineScope? Here's how to get you started! |
| 👨‍💻 **[User guide](https://tvdboom.github.io/linescope/latest/user_guide/introduction/)** | How to use LineScope and its features. |
| 📓 **[Notebooks](https://tvdboom.github.io/linescope/latest/user_guide/notebooks/)** | Profile a cell or a whole notebook session. |
| ⚡ **[Spark](https://tvdboom.github.io/linescope/latest/user_guide/spark/)** | Follow Spark actions, executed plans, and distributed metrics. |
| 🧱 **[Databricks](https://tvdboom.github.io/linescope/latest/user_guide/notebooks/#databricks)** | Capture workspace source and correlate child notebook runs. |
| 🎛️ **[API Reference](https://tvdboom.github.io/linescope/latest/api/configuration/config/)** | The detailed reference for LineScope's API. |
| ⌨️ **[CLI](https://tvdboom.github.io/linescope/latest/cli/linescope/)** | Profile Python scripts and modules from the command line. |
| ❔ **[FAQ](https://tvdboom.github.io/linescope/latest/faq/)** | Get answers to frequently asked questions. |
| 🔧 **[Contributing](https://tvdboom.github.io/linescope/latest/development/)** | Read this before creating a PR. |
| 🌳 **[Dependencies](https://tvdboom.github.io/linescope/latest/dependencies/)** | Which other packages does LineScope depend on? |
| 📃 **[License](https://tvdboom.github.io/linescope/latest/license/)** | Copyright and permissions under the MIT license. |
