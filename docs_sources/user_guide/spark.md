# Spark

Spark integration is enabled by default with `spark=True`. Observation is lazy:
LineScope does not import PySpark or inspect an active Spark session at profile
startup. It watches Spark modules loaded by the workload and wraps supported
methods, including those already loaded before profiling. Lineage is captured
when those methods are called; the JVM query listener starts at the first
observed action. Profiles without Spark calls leave the listener dormant.

Use `profile(spark=False)` or `--no-spark` to disable integration. The `"auto"`
option is no longer accepted. The profiler observes existing action calls; it
never inserts a `count`, `collect`, or other action to measure a lazy
transformation, and it never creates a Spark session.

```python
from linescope import profile

with profile(backend="trace", display="none") as session:
    values = spark.range(1000).filter("id > 100")
    total = values.groupBy().sum("id").collect()

session.save("spark.html")
```

## Lazy execution

`filter` and `groupBy` build a plan. The final `collect` triggers work. Driver
line timings preserve that behavior: the action usually contains the long wait.
Spark references can explain the transformations participating in that action,
but they do not assign fake wall-clock time to lazy source lines.

## Worker scope and overall time

Python source profiling covers the driver according to the selected
[backend's thread coverage](backends.md#profiling-scope). Spark executor JVMs
and Python UDF worker processes are outside that source profile, including with
`local[2]`. Setting `PYSPARK_PYTHON` selects a worker interpreter; it does not
enable worker profiling. Spark actions are observed on the thread that starts
the integration.

Worker execution still affects the result. The driver's `collect()` or `count()`
line includes its wait for the action. The Spark view can also show cumulative
executor/task time and operator metrics supplied by Spark. These counters do
not provide line timings or Python allocation measurements inside a UDF worker.

For example, an action with two overlapping tasks that each take five seconds
could have about five seconds of driver waiting and ten seconds of cumulative
task time, plus scheduling and transfer overhead. Elapsed wall time remains the
duration of the profiling session. Adding driver waiting to cumulative task
time would count overlapping work twice. Missing task metrics do not imply that
workers were idle; they mean the runtime did not expose sufficient information.

## Plans and metrics

The Spark view starts with ranked actions and operators. Action wall time,
cumulative executor time, peak memory, and spill are visible together. The
slowest actions appear first; use Memory or Spill above either table to order
by bytes instead. Unknown measurements sort after measured values, including
zero. Operators from child notebook runs appear in the same ranking.

Each action has a Source link showing its captured filename
and exact action line. Select it to open and highlight the line that triggered
the execution. The same link appears on the execution's detail page. Notebook
actions link to their captured cell and line. When the trigger or its source
snapshot is unavailable, the report shows `Trigger source unavailable`.

The captured action line appears beside the link so repeated `collect` or
`count` calls are easy to distinguish. Select an action to see its cost summary
and operator ranking. Select an operator's name or ID to open its action's
physical operator tree, expand and highlight that exact step, and show its
description and metrics. Query plans, extra action metrics, and stage
details are collapsed below the ranking. Runtime limitations and plan
provenance appear under **Plan provenance and collection notes** on each
action's detail page when available.

Query plans prefer the actual executed physical plan after the action
completes, including the final adaptive plan when accessible. Initial and
optimized logical plans provide context. Operator details expose available rows,
bytes, shuffle, memory, spill, and time metrics.

Operator metrics are supplied by Spark. Operator time shows the largest
available Spark timing on each node, converted to seconds and cumulative across
tasks. Hover over the value to see which timing was selected. Fused pipeline
timings can overlap their children, so the ranking never sums them into an
action total or assigns a fused pipeline's time to its children. Measured fused
pipelines remain visible as their own rows. Peak memory uses an explicit
peak-memory counter; shuffle data size and spill do not stand in for memory
usage.

| Metric | Meaning |
| --- | --- |
| Action wall time | Elapsed time waiting on the action from the driver |
| Executor/task time | Cumulative work across parallel tasks, when exposed |
| Operator rows/bytes | Spark-reported runtime counters |
| Peak memory/spill | Operator/executor metrics, separate from Python memory |

Cumulative executor and operator time can exceed wall time because tasks run in
parallel. Missing metrics remain unknown and appear as a dash in the report.
Spark Connect, runtime access restrictions, JVM API changes, and Databricks
policies can limit plan access; the report keeps collected information and
records limitations.

When the runtime's status store permits access, stage details include
submission-to-completion wall time, cumulative executor time, I/O/shuffle/spill
bytes, and task-duration p50/p95/maximum. Stage wall time includes scheduling.
Action executor totals sum unique completed stage attempts whose submission and
completion fall inside the observed action. Previously completed or skipped
dependencies are excluded. Missing, overlapping, or concurrently observed work
leaves the total unknown, and SQL operator timings are never added together to
produce the action total.

## Scope

| Interface | Version 0.1.0 support |
| --- | --- |
| Classic [DataFrame] and writers | Supported actions and executed plans |
| Driver Python | Source profiling through the selected backend |
| Executor Python UDF workers | Separate worker processes are not collected |
| RDD, streaming, Spark Connect, ML | Outside automatic observation |
| Deferred iterators and eager SQL | Not comprehensively observed |

External integrations can use `SparkIntegration.record_action` to provide an
explicit observation boundary around an operation. Source-to-plan links remain
explanatory: Catalyst and AQE can reorder, fuse, or eliminate operations, and
one lineage can participate in multiple actions. These plans do not imply exact
per-line Spark runtimes.

For a real integration test without an external cluster, use
[local Spark](../examples/spark.md).

Where a runtime permits a query-execution listener, the observer can capture the
final action plan, including writes. If listener access is restricted, a writer
may only expose the input [DataFrame] plan; that fallback is labeled instead of
presented as the final write execution.
