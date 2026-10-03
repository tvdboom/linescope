# Profile a script

```console
linescope --backend trace --output linescope.html path/to/script.py
```

Options belonging to LineScope go before the script path. Remaining arguments belong to the
target script:

```console
linescope --backend trace job.py --input data.csv --limit 100
```

The target runs as `__main__`. Its source remains in the report with measured project lines and
resolved symbol links. Profile collection is finalized even when the target raises an exception;
the command still communicates the target failure instead of hiding it.

Do not also start a profiler in a script already launched under the CLI. For programs that manage
their own profiling scope, run `python script.py` and use the [Python API](../api/profiling.md).
