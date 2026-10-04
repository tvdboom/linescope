# Spark

Enable Spark integration with `profile(spark=True)` or allow the default
auto-detection of an available Spark environment. The profiler observes existing
action calls; it never inserts a `count`, `collect`, or other action to measure
a lazy transformation.

```python
from linescope import profile

with profile(backend="trace", spark=True, display="none") as session:
    values = spark.range(1000).filter("id > 100")
    total = values.groupBy().sum("id").collect()

session.save("spark.html")
```

## Lazy execution

`filter` and `groupBy` build a plan. The final `collect` triggers work. Driver
line timings preserve that behavior: the action usually contains the long wait.
Spark references can explain the transformations participating in that action,
but they do not assign fake wall-clock time to lazy source lines.

## Plans and metrics

The Spark view prefers the actual executed physical plan after the action
completes, including the final adaptive plan when accessible. Initial and
optimized logical plans provide context. Operator details expose available rows,
bytes, shuffle, memory, spill, and time metrics.

| Metric | Meaning |
| --- | --- |
| Action wall time | Elapsed time waiting on the action from the driver |
| Executor/task time | Cumulative work across parallel tasks, when exposed |
| Operator rows/bytes | Spark-reported runtime counters |
| Peak memory/spill | Operator/executor metrics, separate from Python memory |

Cumulative task time can exceed wall time because tasks run in parallel. Missing
metrics remain unknown. Spark Connect, runtime access restrictions, JVM API
changes, and Databricks policies can limit plan access; the report keeps
collected information and records limitations.

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
one lineage can participate in multiple actions.

For a real integration test without an external cluster, use
[local Spark](../examples/spark.md).

Where a runtime permits a query-execution listener, the observer can capture the
final action plan, including writes. If listener access is restricted, a writer
may only expose the input [DataFrame] plan; that fallback is labeled instead of
presented as the final write execution.
