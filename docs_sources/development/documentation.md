# Documentation development

LineScope uses Backtide's Material for MkDocs setup, adapted for a Python-only project. The
source is `docs_sources`; generated output is `docs` and stays out of version control.

```console
uv run python -m mkdocs serve --dev-addr 127.0.0.1:8001
uv run python -m mkdocs build --strict
```

## API pages

`docs_sources/scripts/autodocs.py` is adapted from Backtide's renderer. It reads NumPy-style
docstrings and supports signatures, summaries, parameter tables, return values, methods, examples,
and source links. A page uses directives such as:

```text
:: linescope:configure
    :: signature
    :: head
    :: table:
        - parameters
        - returns
```

Use ordinary Markdown links for prose. The custom reference helper also understands Backtide's
short reference syntax. Keep symbols documented before linking to them.

## Executable examples

Explicit `pycon` fences run during a documentation build through `autorun.py`, which follows
Backtide's console-transcript convention. Ordinary `python` fences only display source. Executed
fences have isolated namespaces and fail the build on exceptions. A statement ending in
`# hide` runs invisibly; `# norun` displays without execution.

Examples must be offline, deterministic, and fast. Do not launch a browser, start Spark, call a
remote notebook, or save a persistent report during a build. Notebook examples are distributed
as `.ipynb` downloads with unexecuted cells so users can run them in their own environment.

## Theme

`overrides/main.html` retains Backtide's title/version/download controls. `overrides/home.html`
provides the branded landing page. The shared stylesheet applies teal and cyan colors in light
and dark modes. Reports have their own embedded assets and do not depend on MkDocs.

![The LineScope Material documentation home page](../img/documentation.jpg)

Versioned publication uses `mike`; see [releasing](releasing.md).
