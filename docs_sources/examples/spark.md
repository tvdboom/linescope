---
notebook: examples/notebooks/local_spark/local_spark.ipynb
---

# Local Spark

[Read the rendered Spark notebook](notebooks/local_spark.ipynb), or download it
using the download button.

Spark's `local[2]` master runs on one machine, so you can exercise real plans
without deploying a cluster. Install a compatible [Java] runtime and the Spark
extra:

```console
uv sync --extra spark
uv run python examples/local_spark.py
```

From a repository checkout with Just installed, `just demo-spark` installs the
Spark extra, runs this same script, and opens `spark.html` in your browser.
[Java] must be available on `PATH` or configured through `JAVA_HOME`.

:: example: local_spark.py

The transformation remains lazy. The explicit `collect` and `count` actions
trigger execution, reusing the same [DataFrame] to show multiple execution
references. The plan view uses the executed/adaptive plan available from that
runtime; absent task counters stay unavailable.

## Windows filesystem actions

Local aggregation and plan inspection can run on Windows with [Java].
Hadoop-backed file writes may additionally require compatible `winutils`/Hadoop
native binaries. A missing Hadoop binary can make a parquet write fail
independently of profiling; LineScope preserves the original Spark error and
records the failed action. The Linux CI integration covers successful writes and
scans, while Windows skips that particular success case when Hadoop support is
missing.
