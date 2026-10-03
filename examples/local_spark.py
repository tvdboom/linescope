"""Observe real local Spark actions; requires Java and the spark extra."""

from pyspark.sql import SparkSession

from linescope import profile


def main():
    """Save a driver profile with real executed plans."""
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("LineScope example")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    try:
        with profile(backend="trace", spark=True, display="none") as session:
            values = spark.range(10_000).filter("id % 2 = 0")
            totals = values.groupBy().sum("id").collect()
            number = values.count()
        report = session.save("spark.html")
        print(f"Rows: {number}; totals: {totals}; report: {report}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
