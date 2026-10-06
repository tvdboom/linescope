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

<br><br>


## Python packages

### Required

* **[click](https://click.palletsprojects.com/)** (>=8.4.2)
* **[psutil](https://psutil.readthedocs.io/)** (>=5.9)


### Optional

Some utilities or configuration options require the installation of additional
libraries. Install all the optional dependencies with
`uv pip install "linescope[full]"`.

* **[databricks-sdk](https://github.com/databricks/databricks-sdk-py)**
* **[ipykernel](https://github.com/ipython/ipykernel)** (>=6.29)
* **[ipython](https://ipython.org/)** (>=8.20)
* **[pyspark](https://spark.apache.org/docs/latest/api/python/)** (>=3.5)
* **[scalene](https://github.com/plasma-umass/scalene)** (>=2.3,<2.4)

### Development

The development dependencies are not installed with the package, and are not
required for any of its functionalities. These libraries are only necessary to
[contribute](development.md) to the project. Install them with
`uv sync --locked --all-groups`.

**Dev**

* **[tox](https://tox.wiki/)** (>=4.25)
* **[tox-uv](https://github.com/tox-dev/tox-uv)** (>=1.25)
* **[uv_build](https://docs.astral.sh/uv/concepts/build-backend/)**
  (>=0.11.13,<0.12)

**Linting**

* **[databricks-sdk](https://github.com/databricks/databricks-sdk-py)**
* **[pre-commit](https://pre-commit.com/)** (>=4.2)
* **[pre-commit-uv](https://github.com/tox-dev/pre-commit-uv)** (>=4.1)
* **[ruff](https://docs.astral.sh/ruff/)** (>=0.11)
* **[ty](https://github.com/astral-sh/ty)** (>=0.0.1)

**Testing**

* **[ipython](https://ipython.org/)** (>=8.20)
* **[nbmake](https://github.com/treebeardtech/nbmake)** (>=1.5.3)
* **[pytest](https://docs.pytest.org/en/latest/)** (>=8.3)
* **[pytest-cov](https://pytest-cov.readthedocs.io/en/latest/)** (>=6)
* **[pytest-mock](https://github.com/pytest-dev/pytest-mock/)** (>=3.14)

**Documentation**

* **[mike](https://github.com/jimporter/mike)** (>=2.1)
* **[mkdocs](https://www.mkdocs.org/)** (>=1.6,<2)
* **[mkdocs-autorefs](https://mkdocstrings.github.io/autorefs/)** (>=1.4)
* **[mkdocs-jupyter](https://github.com/danielfrg/mkdocs-jupyter)** (>=0.24.6)
* **[mkdocs-material](https://squidfunk.github.io/mkdocs-material/)** (>=9.6)
* **[mkdocs-simple-hooks](https://github.com/aklajnert/mkdocs-simple-hooks)**
  (>=0.1.5)
* **[pymdown-extensions](https://facelessuser.github.io/pymdown-extensions/)**
  (>=10.14)
* **[pyyaml](https://pyyaml.org/)** (>=6)
* **[regex](https://github.com/mrabarnett/mrab-regex)** (>=2024.11.6)

**Utilities**

* **[rust-just](https://pypi.org/project/rust-just/)** (>=1.40)

**Demos**

* **[jupyterlab](https://jupyterlab.readthedocs.io/)** (>=4.4,<5)
* **[nbconvert](https://nbconvert.readthedocs.io/)** (>=7.16,<8)

**GPU demos**

* **[torch](https://pytorch.org/)** (>=2.8,<3)
