# Contributing

Contributions to code, tests, examples, and documentation are welcome. Read the repository's
`CODE_OF_CONDUCT.md` and `AGENTS.md` before starting. Discuss large changes in an issue so the
design stays focused on source-level profiling.

## Set up

```console
git clone https://github.com/tvdboom/linescope.git
cd linescope
uv sync --locked
uv run pre-commit install
```

The source lives under `src/linescope`. This is a pure Python package. Normal development needs
no Rust toolchain, Node installation, Java runtime, or Spark cluster. Optional integrations have
their own environments.

## Everyday checks

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python -m mkdocs build --strict
uv build
```

`just` is optional: `uv tool install rust-just`, then `just --list`. The package named
`rust-just` supplies the task-runner binary; LineScope itself contains no Rust code.

Use NumPy-style docstrings with an imperative summary and explicit `Parameters`, `Returns`,
`Raises`, and useful `Examples` sections. Annotate public interfaces and keep changed behavior
covered by regression tests. Prefer assertions about semantics and structure over exact timings.

See [architecture](development/architecture.md), [testing](development/testing.md),
[documentation](development/documentation.md), and [release setup](development/releasing.md).
