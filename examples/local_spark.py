"""LineScope.

Author: Mavs
Description: Observe real local Spark actions; requires Java and the spark
extra.

"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from linescope import profile


def main():
    """Run main.

    Save a driver profile with real executed plans.

    """
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("LineScope example")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )

    try:
        with profile(backend="trace", spark=True, display="none") as session:
            # Build a deterministic event stream and a small dimension table.
            values = spark.range(60_000).filter("id % 2 = 0")
            events = (
                values.withColumn("category_id", F.col("id") % 4)
                .withColumn("customer_id", F.col("id") % 2000)
                .withColumn("day", (F.col("id") / 2000).cast("int"))
                .withColumn("amount", (F.col("id") % 97 + 1).cast("double"))
            )
            categories = spark.createDataFrame(
                [(0, "books"), (1, "stationery"), (2, "furniture"), (3, "other")],
                ["category_id", "category"],
            )
            enriched = events.join(F.broadcast(categories), "category_id")
            daily = enriched.groupBy("day", "category").agg(
                F.sum("amount").alias("revenue"),
                F.count("id").alias("orders"),
                F.countDistinct("customer_id").alias("customers"),
            )
            leaders = (
                daily.groupBy("category")
                .agg(F.sum("revenue").alias("revenue"), F.sum("orders").alias("orders"))
                .orderBy(F.desc("revenue"))
                .collect()
            )
            # LineScope observes application actions without triggering jobs.
            daily_rows = daily.orderBy("day", "category").limit(8).collect()
            totals = values.groupBy().sum("id").collect()
            number = values.count()

        report = session.save("spark.html")
        print(f"Rows: {number}; totals: {totals}; report: {report}")
        print(f"Category leaders: {leaders}")
        print(f"First daily summaries: {daily_rows}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
