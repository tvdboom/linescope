"""LineScope.

Author: Mavs
Description: Opt-in integration checks against a real local Spark JVM.

"""

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.spark,
    pytest.mark.skipif(
        os.environ.get("LINESCOPE_TEST_SPARK") != "1",
        reason="Set LINESCOPE_TEST_SPARK=1 with PySpark and Java installed.",
    ),
]


@pytest.fixture(scope="module")
def spark():
    pyspark = pytest.importorskip("pyspark.sql")
    session = (
        pyspark.SparkSession.builder.master("local[2]")
        .appName("linescope-test")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.autoBroadcastJoinThreshold", "-1")
        .getOrCreate()
    )
    try:
        yield session
    finally:
        session.stop()


def test_local_spark_observes_lazy_join_aggregate_aqe_and_multiple_actions(spark, tmp_path):
    from linescope import profile

    with profile(backend="trace", spark=True, display="none") as session:
        left = spark.range(20).withColumnRenamed("id", "key")
        right = spark.range(10).withColumnRenamed("id", "key")
        joined = left.filter("key > 2").join(right, "key").groupBy("key").count()
        assert session.result.root_run.spark_executions == []
        assert len(joined.collect()) == 7
        assert joined.count() == 7
    executions = session.result.root_run.spark_executions
    assert len(executions) == 2
    assert all(execution.executed_plan for execution in executions)
    assert all("Aggregate" in execution.executed_plan for execution in executions)
    assert all(execution.stats.wall_time_ns > 0 for execution in executions)
    assert all(execution.stats.executor_time_ns > 0 for execution in executions)
    assert all(
        execution.metadata.get("plan_origin") == "query execution listener"
        for execution in executions
    )
    assert all(execution.jobs for execution in executions)
    assert all(execution.stages for execution in executions)
    assert all(
        execution.metadata.get("initial_plan_origin") == "AQE initial physical plan"
        for execution in executions
    )
    assert all(execution.metadata.get("aqe_final_plan") is True for execution in executions)
    stages = [stage for execution in executions for stage in execution.stages]
    assert any(stage.get("cumulative_executor_time_ns", 0) > 0 for stage in stages)
    assert any(len(line.spark_executions) == 2 for line in session.result.root_run.lines)
    operators = [operator for execution in executions for operator in execution.operators]
    flattened = []
    while operators:
        operator = operators.pop()
        flattened.append(operator)
        operators.extend(operator.children)
    assert any("Join" in operator.name for operator in flattened)
    assert any(operator.metrics for operator in flattened)
    report = Path(os.environ.get("LINESCOPE_SPARK_REPORT", str(tmp_path / "spark-profile.html")))
    session.save(report)
    assert report.is_file()


def test_local_spark_parquet_write_and_scan(spark, tmp_path):
    from linescope import profile

    if os.name == "nt" and not os.environ.get("HADOOP_HOME"):
        pytest.skip("Windows parquet writes require Hadoop winutils; this test runs on Linux.")
    output = str(tmp_path / "parquet-output")
    with profile(backend="trace", spark=True, display="none") as session:
        spark.range(12).write.mode("overwrite").parquet(output)
        assert spark.read.parquet(output).filter("id > 3").count() == 8
    executions = session.result.root_run.spark_executions
    assert len(executions) == 2
    assert executions[0].name == "write.parquet"
    assert all(execution.status == "success" for execution in executions)
    assert all(execution.executed_plan for execution in executions)


@pytest.mark.skipif(
    os.name != "nt" or bool(os.environ.get("HADOOP_HOME")),
    reason="Only applies to Windows without Hadoop native tools.",
)
def test_local_spark_failed_write_preserves_error_and_records_failure(spark, tmp_path):
    from linescope import profile

    with pytest.raises(Exception, match=r"HADOOP_HOME|winutils"):
        with profile(backend="trace", spark=True, display="none") as session:
            spark.range(3).write.parquet(str(tmp_path / "unsupported-write"))
    assert session.result.root_run.status == "failed"
    execution = session.result.root_run.spark_executions[0]
    assert execution.name == "write.parquet"
    assert execution.status == "failed"
    assert execution.stats.wall_time_ns > 0
