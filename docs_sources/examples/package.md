# Profile a package

The repository includes `examples/sample_package`, with a job and helper module:

```console
uv run linescope --backend trace --include examples -m examples.sample_package
```

Inspect the helper call in the report, then follow its symbol link to the other file. This
exercise checks the same workflow used in a `src`-layout application installed in your environment.

For your own package:

```console
uv pip install -e .
linescope --backend trace --include mypackage -m mypackage.job --limit 1000
```
