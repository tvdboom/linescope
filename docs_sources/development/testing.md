# Testing

The normal pytest suite is offline and covers project discovery, source snapshots, symbol
resolution, timing normalization, report escaping, lifecycle/error cleanup, CLI scripts/modules,
notebook sessions, child correlation, and Spark adapters using controlled fakes.

```console
uv run pytest -m "not spark and not scalene and not databricks"
uv run pytest --cov=linescope --cov-report=term-missing
uv run tox -m unit
uv run tox -e py311-min,lint,docs
```

Tox tests installed wheels on Python 3.11–3.14. CI runs these on Linux, Windows, and macOS.
Python 3.14 records branch coverage with a 70% floor. The minimum environment resolves the
oldest supported direct dependencies rather than reusing only the latest lock resolution.

## Optional integration environments

```console
uv run tox -e scalene
uv run tox -e spark
```

Scalene 2.3 CPU integration runs on supported Windows/Linux/macOS environments. Local Spark uses Java and `local[2]`; there
is no external cluster. Its environment sets `LINESCOPE_TEST_SPARK=1`. The optional manual CI
input enables the real Spark job, while the ordinary suite exercises fake JVM/Databricks objects.
Windows parquet-write success cases require Hadoop native tools and skip when those are absent;
the Linux integration covers the corresponding write/scan behavior. Failed action tests verify
that profiling preserves the workload's original exception.

Databricks itself requires a manual workspace check: profile a cell, several cells, inline
`%run`, a child notebook call, and a Spark action. Verify source identity, one final report,
child relationships, parameter redaction, and available final plans. Mock tests cannot validate
workspace access policies or every Databricks runtime variation.

The repository's `TESTING.md` contains the full command and acceptance checklist.
