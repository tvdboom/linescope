# LineScope development

LineScope is a Python-only source profiler. Keep library code in
`src/linescope`, tests in `tests`, runnable examples in `examples`, and Material
for MkDocs source in `docs_sources`. `pyproject.toml` is the version and
dependency source of truth; `uv.lock` is committed.

## Commands

```console
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python -m mkdocs build --strict
uv run tox -m unit
uv build
```

Use `uv`, not pip, for repository dependency management. The optional `justfile`
wraps these commands. Do not add Rust, Node, or frontend build requirements to
this pure Python package. Use the explicit trace backend for deterministic unit
tests. Scalene tests run separately on supported platforms. Local Spark tests
need Java and the `spark` extra; mock Databricks APIs.

## Architecture and invariants

- Backends collect measurements; normalized models do not depend on Scalene
  internals.
- Keep source discovery, notebook capture, Spark correlation, and HTML rendering
  separate.
- Show full snapshotted project source. Hide third-party internals and attribute
  their cost to the nearest relevant project line.
- Unknown metrics are `None`, never fabricated zeroes or hit counts.
- Resolve symbol links conservatively. Never navigate a symbol to an ambiguous
  definition.
- Never force Spark actions. Keep driver wall time separate from cumulative task
  metrics.
- Prefer the final AQE plan when available. Unsupported executor metrics stay
  unavailable.
- Notebook-wide profiling generates one report at stop unless live display is
  requested.
- Restore trace hooks, notebook hooks, and monkeypatches on success and failure.
- HTML must be self-contained and escape source, metadata, and embedded JSON
  safely.
- Scrub sensitive notebook parameters; reports contain snapshots of the user's
  source.

## Style and documentation

Follow Backtide's NumPy-style docstrings: imperative summary, explanatory
paragraph, `Parameters`, `Returns`, and `See Also` where useful. Separate every
parameter or attribute with a blank line and document optional parameters with
`, default=...`. Use Python type syntax, such as
`dict[str, list[[SourceUnit]]]`, and square-bracket references for package and
third-party classes. Use single backticks for inline code. Put notes and failure
conditions in the description; do not add `Notes` or `Raises` sections. Keep
examples in documented public APIs. Leave a blank line before the closing triple
quotes, including short docstrings. Use the same `LineScope.`, `Author: Mavs`,
and `Description:` module header. Start function and method code immediately
after the closing quotes, without a blank line. Keep Ruff's spacing after
module and class docstrings.

Prefer enums over hardcoded strings for fixed choices, states, and kinds. Use
`StrEnum` when values are displayed or serialized as strings, and normalize
string inputs to enum members at API boundaries. Custom backend names remain
strings because their registry is open-ended.

Wrap docstrings and documentation at 80 columns. Give public interfaces type
annotations. Use four-space indentation and Ruff formatting with 99 columns for
Python code. Keep function signatures on one line when they fit; otherwise, put
each parameter and parameter separator on its own line and end the parameter
list with a trailing comma so Ruff preserves the layout. Separate logical steps
with blank lines and add comments that explain ownership, cleanup, or
non-obvious choices. Avoid speculative compatibility code and broad exception
handling except at optional integration boundaries, where failures must produce
honest diagnostic metadata.

Documentation uses the adapted Backtide `autodocs.py` directive format
(`:: module:object`) and opt-in executable `pycon` fences through `autorun.py`.
Ordinary `python` fences are displayed without execution. Keep documentation
examples deterministic and offline; no browser launch, Spark startup, network
call, or persistent report generation during a documentation build. Match logo
and theme colors (`#0f766e` teal and `#22d3ee` cyan). Build docs in strict mode.

## Tests and changes

Add meaningful regression tests for changed behavior, including error cleanup.
Check reporting semantics, source links, unknown metrics, and lazy Spark
behavior rather than fragile timings. Do not assert exact nanoseconds. Unit
tests should not need Java, a Spark cluster, or Databricks. See
`docs_sources/development.md` for the test matrix. Run relevant tests, then
normal lint, typing, documentation, and packaging checks. Do not publish
packages, push tags, or deploy docs without user direction.
