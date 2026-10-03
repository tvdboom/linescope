# About LineScope

**Which lines of my code are expensive?** LineScope answers that question with a source browser,
timing heatmap, and reliable links to your own symbols. It collapses implementation details
inside libraries instead of sending you through their call trees.

The package owns project discovery, source snapshots, notebook correlation, Spark context,
and rendering. Collection engines are interchangeable behind a small protocol. Scalene is
the default sampling engine; a built-in trace backend provides a portable alternative.

## Why use it?

- **Source comes first.** See entire files and cells, including unexecuted lines.
- **Your boundaries.** Include project packages and exclude generated or unrelated code.
- **Follow the symbol.** Navigate individual calls, including multiple calls on one line.
- **Notebook sessions.** Capture one cell or several, then show one final report.
- **Spark context.** Link actions to executed plans without triggering extra computation.
- **Portable output.** Keep source and report assets inside one offline HTML file.

## Release scope

Version 0.1.0 is an initial implementation. Spark support covers driver-side Python and
available plan/operator information. It does not measure Python UDF workers on executors.
Databricks child runs have parent/child metadata and correlation interfaces; automatic
instrumentation in remote child jobs requires deployment-specific bootstrap.

Tachyon, attach-to-PID profiling, historical comparisons, and regression dashboards remain
future backend/product work. Unknown metrics are shown as unavailable rather than invented.

## A familiar development experience

The repository follows Backtide's conventions: `src` layout, uv and tox, Ruff and ty, NumPy
docstrings, Material for MkDocs, versioned documentation, custom API rendering, and executable
documentation examples. LineScope is pure Python; it needs no Rust compiler or frontend build.
