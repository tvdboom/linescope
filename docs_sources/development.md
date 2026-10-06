# Development
-------------

Are you interested in contributing to LineScope? Do you want to report a bug?
Do you have a question? Before you do, please read the following guidelines.

<br>


## Submission context

### Question or problem?

For quick questions, there's no need to open an issue. Check first if the
question isn't already answered in the [FAQ](faq.md) section. If not, reach us
through the [discussions] page.


### Report a bug?

If you found a bug in the source code, you can help by submitting an issue
to the [issue tracker](https://github.com/tvdboom/linescope/issues) in the
GitHub repository. Even better, you can
submit a Pull Request with a fix. However, before doing so, please read the
[submission guidelines](#submission-guidelines).


### Missing a feature?

You can request a new feature by submitting an
[issue](https://github.com/tvdboom/linescope/issues) to the GitHub repository.
If you would like to implement a new feature, please submit
an issue with a proposal for your work first. Please consider what kind of
change it is:

* For a **major feature**, first open an issue and outline your proposal so
  that it can be discussed. This will also allow us to better coordinate our
  efforts, prevent duplication of work, and help you to craft the change so
  that it is successfully accepted into the project.

* **Small features and bugs** can be crafted and directly submitted as a Pull
  Request. However, there is no guarantee that your feature will make it into
  `main`, as it's always a matter of opinion whether it benefits the
  overall functionality of the project.

<br><br>


## Project layout

Make sure to familiarize yourself with the project layout before making any
major contributions.

### Folder structure

```text
linescope/                         # Repository root
|-- pyproject.toml                 # Package metadata, dependencies & tools
|-- tox.ini                        # Test / CI task runner configuration
|-- uv.lock                        # Locked dependency versions
|-- justfile                       # Convenience task recipes for just
|-- mkdocs.yml                     # Documentation site configuration
|-- .pre-commit-config.yaml        # Pre-commit hook definitions
|
|-- src/
|   `-- linescope/                 # Python package
|       |-- __init__.py            # Public API exports and notebook extension
|       |-- api.py                 # Profiling sessions and controller
|       |-- cli.py                 # Click CLI entry point
|       |-- config.py              # Project and session configuration
|       |-- enums.py               # Backend, display, and lifecycle choices
|       |-- memory.py              # Shared driver memory collection
|       |-- model.py               # Normalized measurements and source models
|       |-- backends/              # Trace, Scalene, and Tachyon collectors
|       |-- source/                # Source discovery, snapshots, and links
|       |-- notebooks/             # Cell capture and Databricks child profiles
|       |-- spark/                 # Actions, plans, and runtime metrics
|       `-- render/                # Self-contained HTML reports and assets
|
|-- tests/                         # Python unit and integration tests
|-- examples/                      # Runnable scripts and sample package
|   `-- notebooks/                 # Executable notebook examples
|
|-- docs_sources/                  # MkDocs documentation sources
|   |-- user_guide/                # User-guide pages
|   |-- api/                       # API reference pages
|   |-- examples/                  # Example guides and notebook copies
|   |-- img/                       # Images, icons, and logos
|   |-- overrides/                 # MkDocs Material theme overrides
|   |-- scripts/                   # Build-time documentation hooks
|   `-- stylesheets/               # Documentation CSS
|
`-- images/                        # Branding assets and report screenshots
```

### Key technologies

| Layer | Technology |
| --- | --- |
| Source profiling | Trace, Scalene, and Tachyon backends |
| Python API | Context managers and session control |
| Notebook support | IPython and Databricks integrations |
| Spark support | Driver action and query-plan observation |
| Reports | Self-contained HTML, CSS, and JavaScript |
| CLI | [Click](https://click.palletsprojects.com/) |
| Docs | [MkDocs Material][mkdocs-material] |
| Testing | [pytest](https://docs.pytest.org/) |
| Linting | Ruff, ty, and pre-commit |
| Task runner | tox with tox-uv; just for local recipes |
| Package management | [uv](https://docs.astral.sh/uv/) |

## Development setup

### 1. Clone the repository

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
```

### 2. Create a virtual environment and install

```console
uv venv
uv sync --locked --all-extras --all-groups
```

This installs LineScope in editable mode together with its optional
integrations and development dependency groups.

### 3. Install pre-commit hooks

```console
uv run pre-commit install
```

### 4. (Optional) install just for local task recipes

A `justfile` at the repository root provides convenience recipes such as
`just build`, `just test`, `just lint`, `just docs` and `just demo`.

```console
uv tool install rust-just
just --list
```

<br><br>


## Running tests

### Python tests

Python tests live in the `tests/` directory and are executed with **pytest**:

```console
uv run pytest -m "not spark and not scalene and not databricks"
```

The unit suite runs offline and uses controlled fakes for Spark and Databricks.
It covers source snapshots, navigation, measurements, report rendering, CLI
entry points, notebook sessions, and cleanup after errors.

### Notebook execution

Portable notebooks are executed from top to bottom in real Python kernels with
**nbmake**:

```console
uv run tox -e notebooks
```

This environment installs the notebook and Spark extras, registers its own
kernel, and runs copies from `examples/notebooks` in a temporary directory.
Local Spark cells require a compatible [Java] runtime. Cell errors fail the
check; generated reports stay out of the source examples. The GPU notebook
retains saved CUDA outputs and requires GPU hardware to execute again.

### Optional integrations

Run real Scalene sampling and local Spark checks in their separate
environments:

```console
uv run tox -e scalene
uv run tox -e spark
```

Scalene checks require a supported Python version and platform. Spark checks
use `local[2]` and need compatible [Java]; no external cluster is required.
Ordinary unit tests mock these integrations.

### Databricks checks

Databricks runtime checks require a workspace. Profile a single cell, several
cells, inline `%run`, a child notebook call, and a Spark action. Verify source
identity, one final report, child measurements, and available executed plans.
Check child completion, failure, notebook exit, and nested calls, including
cleanup of temporary notebooks and profile files. Restricted workspace access
should retain available source and honest warnings. See the
[Databricks guide](user_guide/notebooks.md#databricks) for runtime setup.

<br><br>


## Tox

[Tox](https://tox.wiki/) is used as the unified task runner for the project.
It is configured in `tox.ini` and uses the [tox-uv] plugin so environments are
created with `uv` instead of plain `venv`.

### Available environments

| Environment | What it does |
| --- | --- |
| `py311` ... `py315` | Build the wheel and run pytest on that Python version. |
| `py311-min` | Test the oldest compatible direct runtime dependencies. |
| `pre-commit` | Run all pre-commit hooks, including Ruff and ty. |
| `notebooks` | Execute example notebooks in a real Python kernel. |
| `scalene` | Run the real Scalene integration checks. |
| `spark` | Run local Spark integration checks with Java. |
| `docs` | Build the MkDocs documentation in strict mode. |

Run the unit matrix or an individual environment with:

```console
uv run tox -m unit
uv run tox -e py311-min
```

Python 3.14 records branch coverage with a 95% minimum. The minimum-dependency
environment resolves the oldest supported direct dependencies separately from
the lockfile.

<br><br>


## Pre-commit & linting

The project uses [pre-commit](https://pre-commit.com/) to enforce code quality
on every commit. The hooks are defined in `.pre-commit-config.yaml`. To run all
hooks manually:

```console
uv run pre-commit run --all-files
```

Or through tox:

```console
uv run tox -e pre-commit
```

<br><br>


## Building the documentation

The docs are built with [MkDocs Material][mkdocs-material] and live in
`docs_sources/`. Build-time hooks in `docs_sources/scripts/` handle
auto-generated API reference pages.

Portable notebooks execute during the build and include their outputs. Install
the notebook and Spark extras and provide compatible [Java] for the local
Spark example. Execution uses temporary copies, so generated reports do not
change source notebooks. Cell errors fail the build. The GPU notebook retains
outputs captured on a CUDA device so documentation builders do not need a GPU.
Run it again with the `gpu` dependency group to refresh its saved outputs.

```console
# Live preview with hot-reload
uv run python -m mkdocs serve

# Production build (strict mode)
uv run python -m mkdocs build --strict
```

Or via tox:

```console
uv run tox -e docs
```

<br><br>


## Submission guidelines

### Submitting an issue

Before you submit an issue, please search the
[issue tracker](https://github.com/tvdboom/linescope/issues),
maybe an issue for your problem already exists, and the discussion
might inform you of workarounds readily available.

We want to fix all the issues as soon as possible, but before fixing a
bug, we need to reproduce and confirm it. In order to reproduce bugs, we
will systematically ask you to provide a minimal reproduction scenario
using the custom issue template.


### Submitting a pull request

Before you submit a pull request, please work through this checklist to
make sure that you have done the necessary so we can efficiently review
and accept your changes.

* Update the documentation so all of your changes are reflected there.
* Update the project unit tests to test your code changes as thoroughly
  as possible.
* Run `uv run pre-commit run --all-files` to verify the repository checks.
* Run the full tox suite: `uv run tox` and make sure all environments pass.
* Run any optional integration checks relevant to your changes.
* Build the package with `uv build`.

If your contribution requires a new **Python** library dependency:

* Double-check that the new dependency is easy to install with uv.
* The library should support Python 3.11, 3.12, 3.13, 3.14 and 3.15, or have
  explicit version markers when an optional integration is more limited.
* Make sure the code works with the latest version of the library.
* Update the dependencies in the documentation.
* Add the library with the minimum required version to `pyproject.toml` and
  update `uv.lock`.

After submitting your pull request, GitHub will automatically run the tests
on your changes and make sure that the updated code builds successfully.
The checks run on all supported Python versions, on Ubuntu, macOS and Windows.
We also use services that automatically check code quality and test coverage.

[discussions]: https://github.com/tvdboom/linescope/discussions
[mkdocs-material]: https://squidfunk.github.io/mkdocs-material/
[tox-uv]: https://github.com/tox-dev/tox-uv
