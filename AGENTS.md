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

## Python style

Treat `pyproject.toml` as the source of truth for Ruff and ty configuration.
Use Python 3.11-compatible syntax and built-in generic types such as `list[str]`
and `dict[str, int]`. Write unions as `Type | None`; avoid legacy `typing.List`
and `typing.Optional`. Give public interfaces parameter and return annotations.
Use `from __future__ import annotations` when forward references need it.

Use four-space indentation, double-quoted Python strings, and Ruff formatting
with a 99-column code limit. Wrap docstrings and documentation at 80 columns,
including indentation and quote delimiters. Keep function signatures on one
line when they fit; otherwise, put each parameter and parameter separator on
its own line and end the parameter list with a trailing comma so Ruff preserves
the layout. Do not enforce a separate trailing-comma rule that conflicts with
Ruff's formatter.

Let Ruff sort imports in this order: future, standard library, third party,
first party, and local folder. Sort imports within each section. Keep optional
integration imports lazy when importing them would start a runtime or require
an unavailable dependency. Keep package directories explicit with `__init__.py`.

Use `snake_case` for functions, methods, parameters, and ordinary attributes;
`PascalCase` for classes; and uppercase names for constants and enum members.
Prefix internal interfaces with `_`. Preserve names required by external
protocols, such as Spark's JVM callbacks. Make boolean options keyword-only
and pass boolean flags by name so calls explain the requested behavior.

Prefer enums over hardcoded strings for fixed choices, states, and kinds. Use
`StrEnum` when values are displayed or serialized as strings, and normalize
string inputs to enum members at API boundaries. Custom backend names remain
strings because their registry is open-ended.

Separate logical steps with blank lines. Add comments explaining ownership,
cleanup, and non-obvious choices rather than restating the code. Avoid
speculative compatibility code and broad exception handling except at optional
integration boundaries, where failures must produce honest diagnostic metadata.
Use narrowly scoped `noqa` comments with a specific rule code when a protocol
or integration requires an exception. Do not add debug prints to library code;
runnable examples may print their results.

Keep UTF-8 source files, LF line endings, a final newline, and no trailing
whitespace. Run Ruff lint and format checks and ty after changes. Ruff's
docstring exemptions for tests, examples, and the adapted documentation parser
do not waive the documentation requirements below.

## Docstrings

Document every Python module, class, function, and method, including internal
interfaces, nested helpers, constructors, special methods, static methods,
class methods, properties, callbacks, fixtures, and test helpers. Apply this
rule to `src/linescope`, `examples`, `docs_sources/scripts`, and `tests`.
Executable notebook definitions follow the same conventions. Literal source
fixtures used to test parsing or profiling retain the syntax their tests need.

Document every attribute defined by a class in its NumPy-style `Attributes`
section. Include public and private instance attributes, dataclass fields,
class variables, enum members, property values, and assigned method aliases.
Constructor `Parameters` documentation does not replace attribute
documentation. Document inherited fields where they are defined, and describe
an override in the subclass that defines it.

Give each attribute its name, type, and a useful explanation of its meaning.
Explain measurement units, indexing conventions, unknown values, mutable state,
and resource or cleanup ownership where relevant. Describe real behavior;
avoid placeholder text that merely repeats the attribute or helper name.

Follow Backtide's NumPy-style docstrings. Start with an imperative summary,
then an explanatory paragraph when useful. Use `Parameters`, `Attributes`,
`Returns`, `Yields`, and `See Also` where applicable, with underlined section
headings. Separate every parameter or attribute entry with a blank line and
indent its description by four spaces. Spell parameter names exactly as in
the signature, including `*args` and `**kwargs`; omit implicit `self` and `cls`.
Document optional parameters with `, default=...`, matching the actual default
or the documented default-factory expression. Do not use `, optional`.

Use Python type syntax such as `dict[str, list[[SourceUnit]]]` in documentation.
Use square-bracket references for package and third-party classes that have a
documentation target. Keep unlinked internal implementation types literal so
strict documentation builds do not create unresolved references. Use single
backticks for inline code. Put operational notes and failure conditions in the
description; do not add `Notes` or `Raises` sections. State relevant errors in
prose, including cleanup and unknown-metric behavior.

Use multiline triple-double-quoted docstrings, including short summaries.
Leave a blank line before the closing triple quotes. Start function and method
code immediately after the closing quotes, without a blank line. Keep Ruff's
spacing after module and class docstrings. If Ruff would collapse a summary
onto one line, add a useful explanatory paragraph instead of repeating the
summary or adding empty lines. Preserve useful examples in documented public
APIs and keep them deterministic and offline. Use raw docstrings when examples
need literal backslashes, and preserve their escape sequences during edits.

Use this module header consistently:

```python
"""LineScope.

Author: Mavs
Description: Explain the module's responsibility.

"""
```

## Documentation rendering

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
