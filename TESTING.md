# Testing LineScope

The default test suite runs offline with the explicit trace backend and fake notebook/Spark
objects. It never starts a cluster. Tests cover normalized results, project filtering, immutable
source snapshots, symbol resolution, report escaping/navigation, API lifecycle, CLI execution,
notebook display timing, Databricks correlation, Spark plan parsing, and lazy action observation.

```console
uv sync --locked
uv run pytest -m "not spark and not scalene and not databricks"
uv run pytest --cov=linescope --cov-report=term-missing
uv run tox -m unit
uv run tox -e py311-min,lint,docs
```

`tox -m unit` tests Python 3.11–3.14 against installed wheels. Python 3.14 collects branch
coverage with a 70% floor. The minimum-dependency environment tests Python 3.11 with the oldest
compatible direct requirements. CI also tests Windows and macOS to catch path/platform problems.

## Real local Spark

Spark supports an embedded local execution mode: `local[2]` runs two worker threads on one
machine, so no external cluster is necessary. Install Java 17 or a Java version supported by
your Spark release, then run:

```console
uv run tox -e spark
```

This opt-in environment installs the `spark` extra and sets `LINESCOPE_TEST_SPARK=1`. It checks
real lazy transformations and actions with AQE, using small generated data. The regular suite
uses fakes to cover unsupported APIs and error recovery cheaply. CI exposes the local Spark
job through manual workflow dispatch; it is deliberately separate from the ordinary matrix.
Successful local parquet writes on Windows also require Hadoop native support (`winutils`).
Those cases skip when that support is unavailable; Linux exercises successful writes/scans,
and the Windows failure path still verifies that the original Spark error is preserved.

## Real Scalene

```console
uv run tox -e scalene
```

Run on a supported Windows/Linux/macOS environment. Scalene has platform and CPython-version
constraints; the base package and trace backend work independently. Sampling tests use enough
work to collect data and assert structure, never exact timings. The CI integration job uses
Linux and Python 3.11.

## Databricks manual acceptance

Install the package on a development cluster and open the provided Databricks example notebook.
Verify a cell magic, a multi-cell session, an inline `%run`, and a `dbutils.notebook.run` child.
Confirm the child path and parent wait time appear, sensitive parameters are redacted, and a
Spark action links to its final executed plan. Separate child processes require explicit
correlation/bootstrap to merge their line measurements. Mock coverage does not claim full
Databricks runtime validation.

## Documentation and artifacts

```console
uv run python -m mkdocs build --strict
uv build
uvx twine check dist/*
```

The documentation build imports public objects for API pages and executes explicit `pycon`
fences. Example failures abort the build. Open a generated HTML report and verify file, symbol,
Spark and notebook links, browser back/forward navigation, light/dark styling, and narrow-screen
layout. Reports must work offline without a JavaScript CDN.
