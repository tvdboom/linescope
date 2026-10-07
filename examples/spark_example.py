"""LineScope.

Author: Mavs
Description: Observe real local Spark actions; requires Java and the spark
extra.

"""

import os
from pathlib import Path
import shutil
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from linescope import profile


def prepare_java():
    """Find Java before starting the local Spark gateway.

    Preserve explicit Java settings. On Windows, an existing terminal may
    not have inherited JAVA_HOME saved by a recent installation; read that
    saved setting when neither JAVA_HOME nor Java on PATH is available.
    Stop with a short setup message if Java is still unavailable.

    """
    if not os.environ.get("JAVA_HOME") and shutil.which("java"):
        return

    if not os.environ.get("JAVA_HOME") and sys.platform == "win32":
        import winreg

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ):
            try:
                with winreg.OpenKey(hive, key) as environment:
                    value, _ = winreg.QueryValueEx(environment, "JAVA_HOME")
            except FileNotFoundError:
                continue

            if isinstance(value, str) and value:
                os.environ["JAVA_HOME"] = os.path.expandvars(value)
                break

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        executable = "java.exe" if sys.platform == "win32" else "java"
        if (Path(java_home) / "bin" / executable).is_file():
            return

        raise SystemExit(
            f"JAVA_HOME points to {java_home!r}, but bin/{executable} is missing. "
            "Set JAVA_HOME to the Java installation directory and retry."
        )

    raise SystemExit(
        "Local Spark requires Java. Install Java 17 and set JAVA_HOME to its "
        "installation directory, or add its bin directory to PATH, then retry. "
        "On Windows: winget install --id EclipseAdoptium.Temurin.17.JDK --exact"
    )


def main():
    """Run main.

    Save a driver profile with real executed plans.

    """
    prepare_java()

    # Keep local workers in the active environment, including on Windows.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

    spark = (
        SparkSession.builder.master("local[2]")
        .appName("LineScope example")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )

    try:
        with profile(memory=True, spark=True, display="none") as session:
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
