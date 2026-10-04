# Dependencies
--------------

## Python & OS

LineScope supports the following Python versions:

* [Python 3.11](https://docs.python.org/3.11/)
* [Python 3.12](https://docs.python.org/3.12/)
* [Python 3.13](https://docs.python.org/3.13/)
* [Python 3.14](https://docs.python.org/3.14/)
* [Python 3.15](https://docs.python.org/3.15/)

And operating systems:

* Linux (Ubuntu, Fedora, etc.)
* Windows
* macOS

Scalene supports Python 3.11–3.14 in LineScope. On Python 3.15, choose Trace or
Tachyon. Optional collectors can impose additional platform or device
requirements.

<br><br>

## Python packages

### Required

The command-line interface and Scalene sampling engine are installed with
LineScope. Trace is the default collector:

* **[click](https://pypi.org/project/click/)** (>=8.4.2): defines CLI options,
  validates arguments, formats help and errors, and supplies the CLI test
  runner.
* **[psutil](https://psutil.readthedocs.io/)** (>=5.9): reads resident
  process RAM for the memory columns and timeline.
* **[scalene](https://github.com/plasma-umass/scalene)** (>=2.3,<2.4; Python
  <3.15): collects CPU, native allocation, and supported GPU measurements.

Trace uses standard-library execution hooks. The shared memory collector uses
`tracemalloc` for retained Python allocations and psutil for process RAM.
Tachyon uses Python 3.15's built-in sampling profiler. Scalene is not installed
on Python 3.15.

LineScope does not require Plotly or pandas. Its self-contained reports use
bundled HTML, CSS, and JavaScript. NumPy is a transitive dependency of Scalene;
LineScope's own models and renderer do not import it. A package in `uv.lock`
can belong to another dependency or a development group without being a direct
LineScope requirement.

### Optional

Install all optional integrations with `uv pip install "linescope[full]"`. In
Databricks, use the runtime's PySpark instead of installing a replacement.

* **[ipython](https://ipython.org/)** (>=8.20): supplies the notebook shell,
  profiling magics, and cell execution hooks in the `notebook` extra.
* **[ipykernel](https://github.com/ipython/ipykernel)** (>=6.29): runs IPython
  as a Jupyter kernel in the `notebook` extra.
* **[pyspark](https://spark.apache.org/docs/latest/api/python/)** (>=3.5):
  supplies the Spark Python APIs observed by the `spark` extra.
* **[databricks-sdk](https://github.com/databricks/databricks-sdk-py)**:
  supplies the workspace API client for remote notebook source capture and
  child run report retrieval in the `databricks` extra. That extra also includes
  notebook support.

The `full` extra combines `notebook`, `spark`, and `databricks`; it adds no
separate library.

### Development

Development dependencies are not installed with the package and are only needed
to [contribute](development.md). Install them with `uv sync --locked`.
The `dev` group includes documentation, linting, pre-commit hooks, tests, tox,
utilities, and notebook demos. The GPU demo group is opt-in with plain uv.
From a checkout, `just sync` installs all extras and dependency groups,
including GPU demos where supported. Other Just recipes reuse that environment.

**Build**

* **[uv_build](https://docs.astral.sh/uv/concepts/build-backend/)**
  (>=0.11.13,<0.12): packages the Python source, assets, and metadata into
  wheels and source distributions. It is a build requirement, not a runtime
  dependency.

**Test environments**

* **[tox](https://pypi.org/project/tox/)** (>=4.25): runs isolated test and
  documentation environments across supported Python versions.
* **[tox-uv](https://pypi.org/project/tox-uv/)** (>=1.25): uses uv to create
  tox environments and install dependencies, including locked test environments.

**Linting**

* **[databricks-sdk](https://github.com/databricks/databricks-sdk-py)**:
  supplies official `dbutils` stubs and SDK client types for static checking.
  These annotations use `TYPE_CHECKING` imports, so they do not require the
  SDK when LineScope runs without workspace access.
* **[ruff](https://pypi.org/project/ruff/)** (>=0.11): checks Python style,
  imports, and common mistakes, and formats Python source and notebooks.
* **[ty](https://pypi.org/project/ty/)** (>=0.0.1): checks library type
  annotations and their use.
* **[pre-commit](https://pypi.org/project/pre-commit/)** (>=4.2): runs the
  repository's configured quality and file hygiene hooks.
* **[pre-commit-uv](https://pypi.org/project/pre-commit-uv/)** (>=4.1): uses
  uv to create Python environments for pre-commit hooks.

**Testing**

* **[pytest](https://pypi.org/project/pytest/)** (>=8.3): discovers and runs
  unit and integration tests.
* **[pytest-cov](https://pypi.org/project/pytest-cov/)** (>=6): measures test
  coverage and enforces the coverage threshold in the Python 3.14 environment.
* **[pytest-mock](https://pypi.org/project/pytest-mock/)** (>=3.14): provides
  pytest's mocking fixture for tests that replace integration boundaries.
* **[ipython](https://pypi.org/project/ipython/)** (>=8.20): supplies a real
  shell for notebook hook and magic tests without requiring Jupyter to run.
* **[nbmake](https://github.com/treebeardtech/nbmake)** (>=1.5.3): executes
  example notebooks through pytest in the separate notebook environment.

**Documentation**

* **[mike](https://pypi.org/project/mike/)** (>=2.1): manages versioned
  documentation and the version selector. Publishing remains an explicit task.
* **[mkdocs](https://pypi.org/project/mkdocs/)** (>=1.6,<2): builds the
  documentation site from Markdown and configuration.
* **[mkdocs-autorefs](https://pypi.org/project/mkdocs-autorefs/)** (>=1.4):
  resolves references between documented APIs and pages.
* **[mkdocs-jupyter](https://pypi.org/project/mkdocs-jupyter/)** (>=0.24.6):
  renders example notebooks without executing their cells during a docs build.
* **[mkdocs-material](https://pypi.org/project/mkdocs-material/)** (>=9.6):
  supplies the documentation theme, navigation, and search interface.
* **[mkdocs-simple-hooks](https://pypi.org/project/mkdocs-simple-hooks/)**
  (>=0.1.5): connects local API rendering and other build hooks to MkDocs.
* **[pymdown-extensions](https://pypi.org/project/pymdown-extensions/)**
  (>=10.14): enables the configured extended Markdown syntax and code fences.
* **[pyyaml](https://pypi.org/project/pyyaml/)** (>=6): parses YAML arguments
  in the custom API documentation directives.
* **[regex](https://pypi.org/project/regex/)** (>=2024.11.6): supplies the
  regular expression features used by the API renderer.

The documentation renderer also imports the core Click dependency to inspect
CLI command objects.

**Utilities**

* **[rust-just](https://pypi.org/project/rust-just/)** (>=1.40): supplies the
  optional `just` binary that runs repository command recipes. It does not add
  a Rust build requirement to LineScope.

**Demos** (`demo` group, installed by `just sync`)

* **[jupyterlab](https://jupyterlab.readthedocs.io/)** (>=4.4,<5): provides
  the interactive environment for opening and running notebook examples.
* **[nbconvert](https://nbconvert.readthedocs.io/)** (>=7.16,<8): executes
  notebook demo recipes and supplies notebook conversion utilities.

**GPU demos** (optional `gpu` group)

* **[torch](https://pytorch.org/)** (>=2.8,<3; Python <3.15 on Linux or
  Windows): runs the GPU example workload. uv uses the explicit CUDA 12.8
  package index for this group. LineScope's GPU collector itself uses Scalene,
  so profiling another GPU library does not require PyTorch.
