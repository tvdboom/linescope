"""LineScope.

Author: Mavs
Description: Spark observation using JVM doubles; no cluster is required.

"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from linescope.model import (
    BackendCapabilities,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SparkExecution,
)
from linescope.spark.listener import SparkIntegration, _install_class
from linescope.spark.metrics import (
    _execution_executor_time,
    operator_metrics,
    scala_items,
    stage_statistics,
)
from linescope.spark.plans import capture_query, parse_plan


class Plan:
    def __init__(self, name="Filter", node_id=1, children=(), metrics=None):
        self.name, self.node_id, self.nodes = name, node_id, children
        self.values = metrics or {}

    def nodeName(self):
        return self.name

    def id(self):
        return self.node_id

    def children(self):
        return self.nodes

    def metrics(self):
        return self.values

    def simpleString(self, max_fields):
        del max_fields
        return self.name + " description"

    def toString(self):
        return self.name + " physical plan"


class Query:
    def __init__(self, plan):
        self.plan = plan

    def id(self):
        return 17

    def executedPlan(self):
        return self.plan

    def logical(self):
        return Plan("logical")

    analyzed = optimizedPlan = sparkPlan = logical


class Session:
    def __init__(self):
        self.registry = SimpleNamespace(snapshot=lambda _filename: None)
        self.result = ProfileResult(ProfileRun(), {}, "trace", BackendCapabilities())
        self.locations = []

    def add_spark_execution(self, execution, locations):
        self.result.root_run.spark_executions.append(execution)
        self.locations.append(locations)


def integration(session=None):
    observer = SparkIntegration(session or Session(), spark=SimpleNamespace())
    observer._listener_attempted = True
    return observer


def test_scala_iteration_and_missing_collection():
    values = iter([1, 2])
    remaining = [2]
    iterator = SimpleNamespace(
        hasNext=lambda: bool(remaining[0]),
        next=lambda: (remaining.__setitem__(0, remaining[0] - 1), next(values))[1],
    )
    assert scala_items(SimpleNamespace(iterator=lambda: iterator)) == [1, 2]
    assert scala_items(None) == []
    assert scala_items(object()) == []


def test_operator_metrics_preserve_raw_units_and_unknowns():
    metric = SimpleNamespace(
        name=lambda: SimpleNamespace(get=lambda: "output rows"),
        value=lambda: 123,
        metricType=lambda: "sum",
    )
    assert operator_metrics(Plan(metrics={"numOutputRows": metric})) == {
        "numOutputRows": {"value": 123, "name": "output rows", "type": "sum"}
    }
    assert operator_metrics(Plan()) == {}


def test_aqe_final_plan_and_query_stage_are_unwrapped():
    scan = Plan("Scan parquet", 3)
    stage = Plan("ShuffleQueryStage", 2)
    stage.plan = lambda: scan
    adaptive = Plan("AdaptiveSparkPlan", 1)
    adaptive.executedPlan = lambda: stage
    adaptive.isFinalPlan = lambda: True
    execution = SparkExecution("one")
    capture_query(execution, Query(adaptive))
    assert execution.operators[0].name == "ShuffleQueryStage"
    assert execution.operators[0].children[0].name == "Scan parquet"
    assert execution.warnings == []
    assert execution.parsed_plan is not None
    assert execution.optimized_plan is not None


def test_plan_cycles_and_size_are_bounded():
    parent, child = Plan("parent", 1), Plan("child", 2)
    parent.nodes, child.nodes = [child], [parent]
    assert parse_plan(parent)[0].children[0].children == []
    assert parse_plan(parent, max_nodes=1)[0].children == []
    assert parse_plan(parent, max_nodes=0) == []


def test_nonfinal_and_input_plan_fallback_are_explicit():
    plan = Plan("AdaptiveSparkPlan")
    plan.executedPlan = lambda: Plan("Filter", 2)
    plan.isFinalPlan = lambda: False
    record = SparkExecution("x")
    capture_query(record, Query(plan), actual=False)
    assert any("AQE final plan" in warning for warning in record.warnings)
    assert any("Input DataFrame plan only" in warning for warning in record.warnings)


def test_aqe_initial_physical_plan_preferred_over_unprepared_plan():
    plan = Plan("AdaptiveSparkPlan")
    plan.executedPlan = lambda: Plan("final aggregate", 2)
    plan.initialPlan = lambda: Plan("initial exchanges", 3)
    plan.isFinalPlan = lambda: True
    record = SparkExecution("x")
    capture_query(record, Query(plan))
    assert record.initial_plan == "initial exchanges physical plan"
    assert record.metadata["initial_plan_origin"] == "AQE initial physical plan"
    assert record.metadata["aqe_final_plan"] is True


def test_record_action_invokes_callback_once_and_preserves_result():
    observer = integration()
    frame = SimpleNamespace(_jdf=SimpleNamespace(queryExecution=lambda: Query(Plan())))
    calls = []
    expected = object()
    assert (
        observer.record_action(frame, "count", lambda: (calls.append(1), expected)[1]) is expected
    )
    assert calls == [1]
    execution = observer.session.result.root_run.spark_executions[0]
    assert execution.stats.wall_time_ns > 0
    assert execution.stats.executor_time_ns is None
    assert execution.status == "success"
    assert any("Input DataFrame" in warning for warning in execution.warnings)


def test_action_error_preserved_and_missing_jvm_is_supported():
    observer = integration()

    def fail():
        raise RuntimeError("original")

    with pytest.raises(RuntimeError, match="original"):
        observer.record_action(object(), "count", fail)
    record = observer.session.result.root_run.spark_executions[0]
    assert record.status == "failed"
    assert record.executed_plan is None
    assert record.warnings
    assert observer._depth == 0


def test_malformed_plan_metadata_never_discards_action_or_result():
    observer = integration()
    metric = SimpleNamespace(value=lambda: "not numeric")
    frame = SimpleNamespace(
        _jdf=SimpleNamespace(queryExecution=lambda: Query(Plan(metrics={"bad": metric})))
    )
    assert observer.record_action(frame, "count", lambda: 42) == 42
    assert len(observer.session.result.root_run.spark_executions) == 1
    assert observer.session.result.warnings


def test_listener_replaces_input_fallback_with_actual_query():
    observer = integration()
    observer._active = True
    frame = SimpleNamespace(_jdf=SimpleNamespace(queryExecution=lambda: Query(Plan("input"))))
    observer.record_action(frame, "count", lambda: 3)
    measured_wall = observer.session.result.root_run.spark_executions[0].stats.wall_time_ns
    observer._query_finished("count", Query(Plan("HashAggregate")), 700, "success")
    record = observer.session.result.root_run.spark_executions[0]
    assert record.executed_plan == "HashAggregate physical plan"
    assert record.metadata["query_wall_time_ns"] == 700
    assert not any("Input DataFrame" in warning for warning in record.warnings)
    assert record.stats.wall_time_ns == measured_wall


def test_listener_never_guesses_between_ambiguous_actions():
    observer = integration()
    observer._active = True
    observer.record_action(object(), "count", lambda: 1)
    observer.record_action(object(), "count", lambda: 1)
    observer._query_finished("count", Query(Plan()), 1, "success")
    assert all(
        record.executed_plan is None
        for record in observer.session.result.root_run.spark_executions
    )


def test_stage_counts_and_missing_retention():
    stage = SimpleNamespace(name="aggregate", numTasks=8, numCompletedTasks=8, numFailedTasks=0)
    tracker = SimpleNamespace(
        getJobInfo=lambda job: SimpleNamespace(stageIds=[2, 3]) if job == 1 else None,
        getStageInfo=lambda stage_id: stage if stage_id == 2 else None,
    )
    context = SimpleNamespace(statusTracker=lambda: tracker)
    assert stage_statistics(context, [1, 99]) == [
        {"id": 2, "name": "aggregate", "tasks": 8, "completed_tasks": 8, "failed_tasks": 0}
    ]
    assert stage_statistics(object(), [1]) == []


def test_optional_stage_metrics_distinguish_wall_cumulative_and_task_quantiles():
    stage = SimpleNamespace(name="aggregate", numTasks=8, numCompletedTasks=8, numFailedTasks=0)
    tracker = SimpleNamespace(
        getJobInfo=lambda _job: SimpleNamespace(stageIds=[2]), getStageInfo=lambda _stage_id: stage
    )
    data = SimpleNamespace(
        attemptId=lambda: 1,
        status=lambda: "COMPLETE",
        executorRunTime=lambda: 2400,
        inputBytes=lambda: 100,
        shuffleReadBytes=lambda: 64,
        shuffleWriteBytes=lambda: 32,
        memoryBytesSpilled=lambda: 0,
        diskBytesSpilled=lambda: 1,
        submissionTime=lambda: SimpleNamespace(get=lambda: SimpleNamespace(getTime=lambda: 1000)),
        completionTime=lambda: SimpleNamespace(get=lambda: SimpleNamespace(getTime=lambda: 2500)),
    )
    distribution = SimpleNamespace(
        isDefined=lambda: True, get=lambda: SimpleNamespace(duration=lambda: [12.5, 30.0, 42.0])
    )
    store = SimpleNamespace(
        stageData=lambda *_args: [data], taskSummary=lambda *_args: distribution
    )
    context = SimpleNamespace(
        statusTracker=lambda: tracker,
        _jsc=SimpleNamespace(sc=lambda: SimpleNamespace(statusStore=lambda: store)),
        _jvm=SimpleNamespace(
            java=SimpleNamespace(util=SimpleNamespace(ArrayList=list)), double=float
        ),
        _gateway=SimpleNamespace(new_array=lambda _kind, size: [0] * size),
    )
    result = stage_statistics(context, [1])[0]
    assert result["cumulative_executor_time_ns"] == 2_400_000_000
    assert result["wall_time_ns"] == 1_500_000_000
    assert result["task_duration_ns"] == {"p50": 12_500_000, "p95": 30_000_000, "max": 42_000_000}
    assert result["memory_spill_bytes"] == 0
    assert result["disk_spill_bytes"] == 1
    assert "output_bytes" not in result
    during = stage_statistics(context, [1], action_window=(900, 2600))[0]
    assert during["attribution"] == "completed during this action"
    reused = stage_statistics(context, [1], action_window=(3000, 4000))[0]
    assert reused["attribution"] == "previously completed dependency"
    assert "_submitted_at_ms" not in during


def test_execution_executor_time_deduplicates_stages_and_excludes_reused_work():
    tracker = SimpleNamespace(getJobInfo=lambda _job: SimpleNamespace(stageIds=[1, 2]))
    context = SimpleNamespace(statusTracker=lambda: tracker)
    stages = [
        {
            "id": 1,
            "attempt": 0,
            "attribution": "previously completed dependency",
            "cumulative_executor_time_ns": 9999,
        },
        {
            "id": 2,
            "attempt": 0,
            "attribution": "completed during this action",
            "cumulative_executor_time_ns": 123,
        },
    ]
    assert _execution_executor_time(context, [7, 8], stages) == 123


@pytest.mark.parametrize("fault", ["missing_stage", "missing_metric", "overlap", "unknown_job"])
def test_execution_executor_time_stays_unknown_when_status_is_incomplete(fault):
    tracker = SimpleNamespace(
        getJobInfo=lambda _job: None if fault == "unknown_job" else SimpleNamespace(stageIds=[1])
    )
    context = SimpleNamespace(statusTracker=lambda: tracker)
    stages = [
        {
            "id": 1,
            "attempt": 0,
            "attribution": "completed during this action",
            "cumulative_executor_time_ns": 123,
        }
    ]
    if fault == "missing_stage":
        stages.clear()
    elif fault == "missing_metric":
        stages[0].pop("cumulative_executor_time_ns")
    elif fault == "overlap":
        stages[0]["attribution"] = "overlapping or unavailable stage interval"
    assert _execution_executor_time(context, [7], stages) is None


def test_automatic_wrappers_preserve_laziness_lineage_multiple_actions_and_restore(monkeypatch):
    calls = []

    class DataFrame:
        def filter(self, condition):
            del condition
            calls.append("filter")
            return DataFrame()

        def count(self):
            calls.append("count")
            return 10

    original = DataFrame.count

    def install():
        _install_class(DataFrame, {"filter"}, "transform")
        _install_class(DataFrame, {"count"}, "action")

    monkeypatch.setattr("linescope.spark.listener._install_patches", install)
    monkeypatch.setattr(
        "linescope.spark.listener.caller_location",
        lambda _session: SourceLocation("pipeline.py", 3),
    )
    observer = integration()
    observer.start()
    try:
        frame = DataFrame().filter("x > 1")
        assert calls == ["filter"]
        assert not observer.session.result.root_run.spark_executions
        assert frame.count() == frame.count() == 10
        assert len(observer.session.result.root_run.spark_executions) == 2
        assert observer.session.locations == [[SourceLocation("pipeline.py", 3)]] * 2
    finally:
        observer.stop()
    assert DataFrame.count is original
    assert calls == ["filter", "count", "count"]


def test_wrappers_ignore_other_threads_and_restore_nested_sessions(monkeypatch):
    class Frame:
        def count(self):
            return 1

    original = Frame.count
    monkeypatch.setattr(
        "linescope.spark.listener._install_patches",
        lambda: _install_class(Frame, {"count"}, "action"),
    )
    outer, inner = integration(), integration()
    outer.start()
    inner.start()
    try:
        thread = threading.Thread(target=lambda: Frame().count())
        thread.start()
        thread.join()
        assert not inner.session.result.root_run.spark_executions
        Frame().count()
        assert len(inner.session.result.root_run.spark_executions) == 1
        assert not outer.session.result.root_run.spark_executions
        inner.stop()
        Frame().count()
        assert len(outer.session.result.root_run.spark_executions) == 1
    finally:
        inner.stop()
        outer.stop()
    assert Frame.count is original


def test_writer_uses_owning_dataframe_and_does_not_duplicate_nested_action(monkeypatch):
    class Frame:
        _jdf = SimpleNamespace(queryExecution=lambda: Query(Plan("Scan")))

    class Writer:
        def __init__(self, frame):
            self._df = frame

        def save(self):
            return self.parquet()

        def parquet(self):
            return "written"

    monkeypatch.setattr(
        "linescope.spark.listener._install_patches",
        lambda: _install_class(Writer, {"save", "parquet"}, "write"),
    )
    observer = integration()
    observer.start()
    try:
        assert Writer(Frame()).save() == "written"
    finally:
        observer.stop()
    records = observer.session.result.root_run.spark_executions
    assert len(records) == 1
    assert records[0].name == "write.save"
    assert records[0].executed_plan == "Scan physical plan"
