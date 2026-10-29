# Spark

Enable observation with `spark=True` or `--spark`. PySpark must be installed;
LineScope uses your existing session and observes actions your code runs.

```python
from linescope import profile

with profile(spark=True, display="none") as session:
    values = spark.range(1000).filter("id > 100")
    total = values.groupBy().sum("id").collect()

session.save("spark.html")
```

## Lazy execution

`filter` and `groupBy` build a plan; `collect` runs it. Driver line timing shows
the wait on the action. Plan links identify transformations without assigning
the action's duration to each lazy line. Profiling does not force extra actions
or create a Spark session.

## Worker scope and overall time

Python profiling covers the driver, including with `local[2]`. Executor JVMs and
Python UDF workers are outside that profile. Actions are observed on the thread
that starts the integration.

Parallel tasks can total ten seconds of executor work during five seconds of
driver waiting. Those times overlap; do not add them.

## Plans and metrics

Open an action from the **Spark** view to inspect its plan and costs. Source
links point to the triggering line, including child notebook calls. Plans use
the final executed AQE plan when available.

**Main plan steps** follows the data flow. **From step** lists inputs; `2 + 5`
means both branches feed that step. Select a step to open its physical operator.
Full plans, stages, and collection notes appear below the overview.

### Interpret the counters

| Metric | Meaning |
| --- | --- |
| Action wall time | Driver time waiting for the action |
| Executor/task time | Cumulative work across tasks |
| Operator time | Time reported for a node or shared pipeline |
| Rows/bytes | Runtime counters |
| Peak memory/spill | Operator or executor counters, separate from Python RAM |

Operator times can overlap and do not sum to wall time. Fused operators appear
under **Operations measured together**; their children may show **Shared
timing**.

**Rows after** uses output counters. Sorts and exchanges can carry known input
counts forward as **From input · unchanged**. Missing counts after filters,
joins, and aggregates stay unavailable. Data size and spill are not peak memory.

Stages can include task-duration p50/p95/maximum, I/O, shuffle, memory, and
spill.
Executor totals cover unique completed stage attempts. Incomplete or overlapping
work can leave totals unavailable.

JVM restrictions and runtime policies can limit capture. Check **Plan provenance
and collection notes**. For writes, a labeled fallback may show only the input
DataFrame plan.

## Scope

| Interface | Support |
| --- | --- |
| Classic [DataFrame] and writers | Supported actions and executed plans |
| Driver Python | The selected backend's source profile |
| Executor Python UDF workers | Not collected |
| RDD, streaming, Spark Connect, ML | Outside automatic observation |
| Deferred iterators and eager SQL | Not comprehensively observed |

Catalyst and AQE can reorder or remove transformations. Source links describe
lineage, not exact Spark time per Python line. Custom integrations can use
`SparkIntegration.record_action` to mark an observation boundary.

Use the AI icon for [plan suggestions](ai.md#spark-context), or try the
[local Spark notebook](../examples/notebooks/spark_example.ipynb).
