# Profile a package

The repository includes `examples/sample_package`, with a job and helper module:

```console
uv run linescope --backend trace --include examples -m examples.sample_package
```

The entry point calls two functions defined in the helper module. These are the
same files used by the command above:

=== "__main__.py"

    :: example: sample_package/__main__.py

=== "helpers.py"

    :: example: sample_package/helpers.py

Inspect the helper call in the report, then follow its symbol link to the other
file. This exercise checks the same workflow used in a `src`-layout application
installed in your environment.

For your own package:

```console
uv pip install -e .
linescope --backend trace --include mypackage -m mypackage.job --limit 1000
```
