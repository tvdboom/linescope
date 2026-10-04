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

Scalene, the default sampling engine on Python 3.11–3.14, is installed with
LineScope:

* **[scalene](https://github.com/plasma-umass/scalene)** (>=2.3,<2.4; Python
  <3.15)

Trace uses the standard library; Tachyon uses Python 3.15's built-in sampling
profiler. Scalene is not installed on Python 3.15.

### Optional

Install all optional integrations with `uv pip install "linescope[full]"`. In
Databricks, use the runtime's PySpark instead of installing a replacement.

* **[ipython](https://ipython.org/)** (>=8.20)
* **[ipykernel](https://github.com/ipython/ipykernel)** (>=6.29)
* **[pyspark](https://spark.apache.org/docs/latest/api/python/)** (>=3.5)

### Development

Development dependencies are not installed with the package and are only needed
to [contribute](development.md). Install them with `uv sync --all-groups`.

**Dev**

* **[tox](https://pypi.org/project/tox/)** (>=4.25)
* **[tox-uv](https://pypi.org/project/tox-uv/)** (>=1.25)

**Linting**

* **[ruff](https://pypi.org/project/ruff/)** (>=0.11)
* **[ty](https://pypi.org/project/ty/)** (>=0.0.1)
* **[pre-commit](https://pypi.org/project/pre-commit/)** (>=4.2)
* **[pre-commit-uv](https://pypi.org/project/pre-commit-uv/)** (>=4.1)

**Testing**

* **[pytest](https://pypi.org/project/pytest/)** (>=8.3)
* **[pytest-cov](https://pypi.org/project/pytest-cov/)** (>=6)
* **[pytest-mock](https://pypi.org/project/pytest-mock/)** (>=3.14)
* **[ipython](https://pypi.org/project/ipython/)** (>=8.20)
* **[nbmake](https://github.com/treebeardtech/nbmake)** (>=1.5.3)

**Documentation**

* **[click](https://pypi.org/project/click/)** (>=8.1)
* **[mike](https://pypi.org/project/mike/)** (>=2.1)
* **[mkdocs](https://pypi.org/project/mkdocs/)** (>=1.6,<2)
* **[mkdocs-autorefs](https://pypi.org/project/mkdocs-autorefs/)** (>=1.4)
* **[mkdocs-jupyter](https://pypi.org/project/mkdocs-jupyter/)** (>=0.24.6)
* **[mkdocs-material](https://pypi.org/project/mkdocs-material/)** (>=9.6)
* **[mkdocs-simple-hooks](https://pypi.org/project/mkdocs-simple-hooks/)**
  (>=0.1.5)
* **[pymdown-extensions](https://pypi.org/project/pymdown-extensions/)**
  (>=10.14)
* **[pyyaml](https://pypi.org/project/pyyaml/)** (>=6)
* **[regex](https://pypi.org/project/regex/)** (>=2024.11.6)

**Utilities**

* **[rust-just](https://pypi.org/project/rust-just/)** (>=1.40)

**Demos** (optional `demo` group, installed by `just demo-notebook`)

* **[jupyterlab](https://jupyterlab.readthedocs.io/)** (>=4.4,<5)
* **[nbconvert](https://nbconvert.readthedocs.io/)** (>=7.16,<8)
