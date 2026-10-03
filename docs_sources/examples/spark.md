# Local Spark

Spark's `local[2]` master runs on one machine, so you can exercise real plans without deploying
a cluster. Install a compatible Java runtime and the Spark extra:

```console
uv sync --extra spark
uv run python examples/local_spark.py
```

```python
from pyspark.sql import SparkSession
from linescope import profile

spark = SparkSession.builder.master("local[2]").appName("LineScope example").getOrCreate()
try:
    with profile(backend="trace", spark=True, display="none") as session:
        values = spark.range(10_000).filter("id % 2 = 0")
        totals = values.groupBy().sum("id").collect()
    session.save("spark.html")
finally:
    spark.stop()
```

The transformation remains lazy. Only the explicit `collect` triggers execution. Reuse the same
DataFrame for another action to inspect multiple execution references. The plan view uses the
executed/adaptive plan available from that runtime; absent task counters stay unavailable.

## Windows filesystem actions

Local aggregation and plan inspection can run on Windows with Java. Hadoop-backed file writes
may additionally require compatible `winutils`/Hadoop native binaries. A missing Hadoop binary
can make a parquet write fail independently of profiling; LineScope preserves the original
Spark error and records the failed action. The Linux CI integration covers successful writes
and scans, while Windows skips that particular success case when Hadoop support is missing.
