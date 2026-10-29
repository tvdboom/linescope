# Development

Use [discussions] for questions and the
[issue tracker](https://github.com/tvdboom/linescope/issues) for bugs. Include
a minimal reproducer. Discuss large changes before starting a pull request.

## Project layout

| Path | Contents |
| --- | --- |
| `src/linescope` | Library, collectors, integrations, and report renderer |
| `tests` | Unit and integration tests |
| `examples` | Scripts and source notebooks |
| `docs_sources` | MkDocs pages, API directives, and build hooks |
| `pyproject.toml` | Package version, dependencies, and tool settings |
| `uv.lock` | Locked dependency versions |
| `tox.ini` | Test environments |

## Development setup

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
uv sync --locked
uv run pre-commit install
```

Use uv 0.11.13 or later in the 0.11 or 0.12 series, as specified in
`pyproject.toml`. The optional `justfile` wraps common commands.

## Running tests

### Python tests

```console
uv run pytest -m "not spark and not scalene and not databricks"
```

Unit tests run offline with mocked Spark and Databricks APIs. Use the explicit
Trace backend for deterministic measurements.

### Notebook execution

```console
uv run tox -e notebooks
```

This executes temporary copies of `examples/notebooks` with nbmake. Local
Spark needs [Java]; the GPU notebook keeps its saved CUDA output.

### Optional integrations

```console
uv run tox -e scalene
uv run tox -e spark
```

Scalene needs a supported Python version and platform. Spark uses `local[2]`
and needs Java.

### Databricks checks

In a workspace, test cell and session profiling, `%run`, child notebooks, and
Spark actions. Check source links, child measurements, failure cleanup, notebook
exit values, and the fallback when workspace permissions are restricted. See
[Databricks setup](user_guide/notebooks.md#databricks).

## Tox

| Environment | Checks |
| --- | --- |
| `py311` … `py315` | Wheel build and unit tests |
| `py311-min` | Oldest supported direct dependencies |
| `pre-commit` | Ruff, ty, and repository hooks |
| `notebooks` | Notebook execution |
| `scalene`, `spark` | Real integration tests |
| `docs` | Strict documentation build |

```console
uv run tox -m unit
uv run tox -e py311-min
```

Python 3.14 enforces 95% branch coverage. The minimum-dependency environment
resolves separately from `uv.lock`.

## Pre-commit & linting

```console
uv run pre-commit run --all-files
```

Or run the individual checks:

```console
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

## Building the documentation

```console
uv sync --locked --group docs --group spark --extra notebook
uv run python -m ipykernel install --sys-prefix --name python3
uv run python -m mkdocs build --strict
```

Use `uv run python -m mkdocs serve` for a local preview.

API pages come from Python docstrings. `pycon` fences execute during builds;
ordinary `python` fences do not. Keep examples offline and prevent browser
launches or persistent reports.

Notebook builds use temporary copies. The Spark notebook executes when Java is
found through `JAVA_HOME` or `PATH`; Windows also checks saved `JAVA_HOME`.
Without valid Java, it uses saved content. With Java available, startup and cell
errors fail the build.

GPU outputs remain saved. Refreshing them needs CUDA hardware, the `gpu`
dependency group, and the `scalene` extra.

## Submission guidelines

Update tests and documentation with the change. Run the checks above, relevant
integration tests, and `uv build`. New dependencies belong in `pyproject.toml`
and `uv.lock`, with markers for unsupported Python versions or platforms.
Follow `AGENTS.md` for code and docstring conventions.

[discussions]: https://github.com/tvdboom/linescope/discussions
