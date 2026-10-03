# Dependencies

The base package has **no third-party runtime dependencies**. Source discovery, tracing, models,
and HTML generation use Python's standard library. The generated report embeds its scripts and
styles, so viewing it requires only a browser.

## Optional runtime features

| Extra | Dependencies | Purpose |
| --- | --- | --- |
| `scalene` | Scalene | Default sampling collector and memory support on supported platforms |
| `notebook` | IPython, ipykernel | Jupyter/IPython extension and kernel integration |
| `spark` | PySpark | Standalone/local Spark environments |
| `full` | All supported extras | Convenience installation |

The adapter targets Scalene 2.3 and supports CPU sampling on Windows. The package does not silently
substitute the trace backend; explicitly choose it when Scalene is unavailable. In Databricks, use the
runtime's PySpark instead of installing a replacement via the `spark` extra.
Scalene native memory requires its allocator binary; Python 3.11 Windows source builds can
support CPU sampling while lacking that DLL. Python 3.12+ is recommended for Windows memory.

## Development groups

| Group | Tools |
| --- | --- |
| `test` | pytest, pytest-cov, pytest-mock, IPython |
| `lint` | Ruff and ty |
| `pre-commit` | pre-commit and pre-commit-uv |
| `tox` | tox and tox-uv |
| `docs` | MkDocs, Material, autorefs, simple hooks, mike, PyMdown Extensions, Click, regex, YAML |
| `utils` | Optional `just` task runner |

`dev` includes the standard test, lint, hooks, tox, and docs groups. `uv.lock` pins transitive
versions for reproducible development. `pyproject.toml` is the authoritative source for minimum
versions and environment markers.

## Licenses and attribution

LineScope is MIT licensed. Dependencies retain their own licenses; consult each installed
distribution's metadata. Documentation helpers and base styling are adapted from the MIT-licensed
[Backtide repository](https://github.com/tvdboom/backtide), copyright 2026 Mavs.
