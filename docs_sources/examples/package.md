# Profile a package

Run the package example from a checkout with Just installed:

```console
just demo-package
```

The recipe runs `examples/package_example.py` and opens `package.html`. The
runner profiles the dummy `sample_package`, which has a job and helper module.
It includes both modules and the runner in the report, so you can follow calls
across files without adding profiling code to the package.
The runner processes 200,000 orders to give the sampling collector sustained
work and allocations. Short lines can still have unavailable measurements.

:: example: package_example.py

You can also profile the package entry point with the CLI:

```console
uv run linescope --backend scalene --memory -m examples.sample_package
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
linescope --memory --include mypackage -m mypackage.job --limit 1000
```
