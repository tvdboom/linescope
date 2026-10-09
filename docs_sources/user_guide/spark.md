# Spark

Enable Spark observation with `spark=True` or `--spark`. LineScope uses existing
PySpark and the application's Spark session. It never creates a session or
forces an action to measure a lazy transformation.

```python
from linescope import profile

with profile(backend="trace", spark=True, display="none") as session:
    values = spark.range(1000).filter("id > 100")
    total = values.groupBy().sum("id").collect()

session.save("spark.html")
```

PySpark must be installed when enabling integration. Observation starts as the
workload loads and calls Spark; the query listener stays dormant until the
first observed action.

## Lazy execution

`filter` and `groupBy` build a plan; `collect` triggers execution. Driver line
timings usually show the long wait on the action. Source links connect the plan
to its transformations without assigning action wall time to each lazy line.

## Worker scope and overall time

Python source profiling covers the driver according to the
[backend's thread coverage](backends.md#profiling-scope). Executor JVMs and
Python UDF workers are outside that profile, including with `local[2]`.
Spark actions are observed on the thread that starts the integration.

The action line includes the driver's wait. The **Spark** view can also show
cumulative executor/task time and operator counters, but these do not provide
Python line or allocation measurements inside workers.

Two parallel tasks taking five seconds each can produce about five seconds of
driver waiting and ten seconds of cumulative task time. Do not add those values:
they overlap. Missing task metrics mean the runtime did not expose them.

## Plans and metrics

Start with the action table and follow an expensive action to its detail page.
Summary cards and rows show action wall time, the largest reported operator or
pipeline time, peak memory, and disk spill. Source links open the captured line
that triggered the action, including notebook cells and child runs.

**Main plan steps** follows the data through reading, filtering, joining,
summarizing, sorting, and exchanges. **From step** identifies the inputs;
`2 + 5` means two branches feed the step and can run in parallel. **Rows after**
helps identify growth at joins when input counts are available.

Select a step to inspect it in the physical operator tree. Expand the sections
below the overview for full plans, operator rankings, stage details, and
collection notes. Plans prefer the executed physical plan, including final AQE
when accessible. Initial and optimized logical plans provide context.

### Interpret the counters

| Metric | Meaning |
| --- | --- |
| Action wall time | Driver time waiting for the action |
| Executor/task time | Cumulative work across parallel tasks |
| Operator time | Spark timing for a node or shared pipeline |
| Rows/bytes | Runtime counters reported by Spark |
| Peak memory/spill | Operator or executor counters, separate from Python RAM |

Operator timings can include cumulative work, preparation, or upstream waits.
They overlap and do not sum to action wall time. Fused operators appear under
**Operations measured together**; steps without their own timing show
**Shared timing** when a pipeline measurement is available.

**Rows after** uses output-row counters. Sorts and exchanges can carry known
input counts forward as **From input · unchanged**. Missing counts after
filters, joins, or aggregates remain unavailable. Peak memory requires a
peak-memory counter; data size and spill are different measurements.

Stage details can include wall time, cumulative executor time, I/O, shuffle,
spill, and task-duration p50/p95/maximum. Action executor totals cover unique
completed stage attempts within the observed action. Incomplete or overlapping
work can leave totals unavailable.

A dash means unavailable. Spark Connect, JVM access restrictions, and Databricks
policies can limit plan or metric access. Check **Plan provenance and collection
notes** for the recorded limitations. A write may expose only its input
DataFrame plan when listener access is restricted; that fallback is labeled.

## Scope

| Interface | Support |
| --- | --- |
| Classic [DataFrame] and writers | Supported actions and executed plans |
| Driver Python | Source profiling through the selected backend |
| Executor Python UDF workers | Separate worker processes are not collected |
| RDD, streaming, Spark Connect, ML | Outside automatic observation |
| Deferred iterators and eager SQL | Not comprehensively observed |

Catalyst and AQE can reorder, fuse, or eliminate transformations, so source
links explain lineage rather than exact per-line Spark runtimes. External
integrations can use `SparkIntegration.record_action` to define an explicit
observation boundary.

See the [local Spark notebook](../examples/notebooks/spark_example.ipynb) for a
complete example without an external cluster.
