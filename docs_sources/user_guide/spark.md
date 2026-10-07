# Spark

Enable Spark integration with `spark=True` or `--spark`; it defaults to `False`.
LineScope uses the environment's existing PySpark and does not install Spark.
Requesting integration without PySpark raises an error at startup.
Observation is lazy:
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

The Spark view starts with summary cards for maximum action wall time,
maximum operator time, peak operator memory, maximum disk spill, and captured
Spark jobs. The job count totals the job records associated with captured
actions, including child notebook runs. An action can have several job records.
The sortable action table follows these summaries. Each row shows action wall
time and the largest reported operator or shared pipeline time, peak memory,
and disk spill. The longest measured action appears first, and actions from
child notebook runs are included. Select a row or its action link to open the
action's detail page and main-step plan overview. Source links open the
captured trigger line instead.
The overview uses **Operator time**, **Peak memory**, and **Disk spill** for
the reported plan costs. The **All operator costs** table below it uses the
same column names. Cumulative executor time and executor peak memory remain
available on each action's detail page. Select a column heading to order the
table. Operator rankings and plan-step tables use the same header arrows.
Select the heading again to reverse the direction. Unknown measurements sort
after measured values, including zero, in both directions.

**Main plan steps** follows the data from inputs to the result. Plain-language
labels describe reading, filtering, joining, summarizing, sorting, and moving
data between workers. Unmeasured projections and internal wrappers are omitted.
The **From step** column keeps separate branches explicit: `2 + 5` means that
this operation consumes both inputs, not that those inputs ran sequentially.
Separate inputs can run in parallel.
Reported time, peak memory, and total **Rows after** appear beside each step.

The detail page combines action and operator metrics in one overview above
the **Main plan steps** table. **Operator time**, **Peak memory**, and **Disk
spill** identify the largest reported operator or shared pipeline costs, with
the responsible step shown below each value. Row multiplication at joins
appears when both input counts are known. Operator counters have a different
scope from action wall time, cumulative executor time, and executor peak
memory. They point to measured work to investigate; partial counters cannot
establish the complete cause of a slow action. Time bars compare measured
individual steps rather than percentages of the action's elapsed time.

Rows use Spark's output-row counters. Sorts and data exchanges can carry a
known input count forward because they preserve rows; those values are labeled
**From input · unchanged**. Missing output counts after filters, joins,
aggregates, limits, and unknown operations remain unavailable. Estimates,
partition counts, and shuffle-record counters do not fill missing row counts.

Each action has a Source link showing its captured filename
and exact action line. Select it to open and highlight the line that triggered
the execution. The same link appears on the execution's detail page. Notebook
actions link to their captured cell and line. When the trigger or its source
snapshot is unavailable, the report shows `Trigger source unavailable`.

The captured action line appears beside the link so repeated `collect` or
`count` calls are easy to distinguish. Select an action to see its cost summary
and main-step overview. Additional action metrics appear
only when row, read, shuffle, or spill measurements are available.
Select a step to open its action's physical operator
tree, expand and highlight that exact step, and show its
description and metrics. Query plans, extra action metrics, stage details, and
the full operator cost ranking are collapsed below the overview. Runtime
limitations and plan provenance appear under **Plan provenance and collection
notes** on each
action's detail page when available.

Query plans prefer the actual executed physical plan after the action
completes, including the final adaptive plan when accessible. Initial and
optimized logical plans provide context. Operator details expose available rows,
bytes, shuffle, memory, spill, and time metrics.

Operator metrics are supplied by Spark. Operator time shows the largest
available Spark timing on each node, converted to seconds. The selected metric's
name appears below the main step's time and in its tooltip. Timings can measure
cumulative worker work, preparation, or waiting for upstream input; they are not
exclusive durations for individual steps and do not add up to action wall time.
Fused pipeline timings can overlap their children, so the ranking never sums
them into an action total or assigns a fused pipeline's time to its children.
Measured fused pipelines appear under **Operations measured together**, with
the main step numbers they cover. A step with no separate timing shows
**Shared timing** when its pipeline has a measurement. Input adapters end
pipeline membership, so upstream work is not assigned to a downstream fused
group. Peak memory uses an explicit peak-memory counter; shuffle data size and
spill do not stand in for memory usage. Peak memory does not represent the
total data size processed by the step.

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
[local Spark notebook](../examples/notebooks/spark_example.ipynb).

Where a runtime permits a query-execution listener, the observer can capture the
final action plan, including writes. If listener access is restricted, a writer
may only expose the input [DataFrame] plan; that fallback is labeled instead of
presented as the final write execution.
