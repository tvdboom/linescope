---
notebook: examples/notebooks/spark_example/spark_example.ipynb
---

# Local Spark

[Read the rendered Spark notebook](notebooks/spark_example.ipynb), or download
it using the download button.

Spark's `local[2]` master runs on one machine, so you can exercise real plans
without deploying a cluster. Install a compatible [Java] runtime and the Spark
extra:

```console
uv sync --extra spark
uv run python examples/spark_example.py
```

From a repository checkout with Just installed, run `just sync` once to install
all extras and dependency groups. Then `just demo-spark` runs this same script
and opens `spark.html` in your browser using the installed environment.
[Java] must be available on `PATH` or configured through `JAVA_HOME`.

On Windows, install Java 17 with WinGet:

```powershell
winget install --id EclipseAdoptium.Temurin.17.JDK --exact
```

Reopen PowerShell and verify `java -version` before running `just demo-spark`.
For an archive installation, set `JAVA_HOME` to the extracted JDK directory.
The script and notebook also read the saved Windows `JAVA_HOME` when an
existing terminal has neither Java on `PATH` nor `JAVA_HOME` in its current
environment. Explicit settings take precedence. Missing Java or an invalid
`JAVA_HOME` produces a short setup message before Spark starts.
The Spark extra installs PySpark; it does not install Java. An unavailable
Java runtime causes `JAVA_GATEWAY_EXITED` before the workload starts. See the
[Temurin Windows installation
guide](https://adoptium.net/installation/windows/) for setup options.

The script and notebook default Spark workers to the active Python interpreter.
Set `PYSPARK_PYTHON` before running them to choose a different worker
interpreter.

The script and notebook use Scalene with driver memory collection enabled on
Python 3.11–3.14. On Linux and macOS, prepare the [memory allocator
environment](../user_guide/backends.md#memory) before starting Python or the
notebook kernel.

:: example: spark_example.py

To execute the matching Spark notebook and inspect its inline outputs in
JupyterLab, run `just demo-spark-notebook`. The executed copy is saved to
`reports/spark_example.ipynb`; Java must be available on PATH. Press Ctrl+C in
the terminal to stop JupyterLab.

The transformation remains lazy. The explicit `collect` and `count` actions
trigger execution, reusing the same [DataFrame] to show multiple execution
references. The plan view uses the executed/adaptive plan available from that
runtime; absent task counters stay unavailable.

`local[2]` permits two concurrent Spark tasks. The source report covers the
Python driver; executor JVM work and any Python UDF worker processes have no
separate source-line profile. `PYSPARK_PYTHON` selects their interpreter without
extending collection scope. Action lines include the driver's wait, while the
Spark view shows available task metrics separately. See [worker scope and
overall time](../user_guide/spark.md#worker-scope-and-overall-time) for how to
interpret parallel execution.

## Windows filesystem actions

Local aggregation and plan inspection can run on Windows with [Java].
Hadoop-backed file writes may additionally require compatible `winutils`/Hadoop
native binaries. A missing Hadoop binary can make a parquet write fail
independently of profiling; LineScope preserves the original Spark error and
records the failed action. The Linux CI integration covers successful writes and
scans, while Windows skips that particular success case when Hadoop support is
missing.
