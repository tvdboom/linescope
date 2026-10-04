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
    """Expose a controlled Spark physical plan through a JVM-like interface.

    Attributes
    ----------
    name : str
        Controlled physical operator name returned by the fake JVM interface.

    node_id : int
        Controlled node identifier used to detect repeated plan nodes.

    nodes : tuple[Plan, ...]
        Child plan nodes returned by the fake JVM interface.

    values : dict[str, Any]
        Controlled raw operator metrics returned by the test double.

    """

    def __init__(self, name="Filter", node_id=1, children=(), metrics=None):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.name, self.node_id, self.nodes = name, node_id, children
        self.values = metrics or {}

    def nodeName(self):
        """Return the controlled physical operator name.

        Keep the operator label stable across plan normalization checks.

        """
        return self.name

    def id(self):
        """Return the controlled plan or query identifier.

        Keep identifiers stable so cycle handling and query correlation can be
        checked.

        """
        return self.node_id

    def children(self):
        """Return the controlled child plan nodes without executing Spark.

        Preserve the supplied child order for recursive plan capture.

        """
        return self.nodes

    def metrics(self):
        """Return raw controlled operator metrics and unit metadata.

        Retain unavailable values rather than substituting fabricated
        measurements.

        """
        return self.values

    def simpleString(self, max_fields):
        """Return a controlled operator description for plan capture.

        Ignore display limits because the test description is already bounded.

        """
        del max_fields
        return self.name + " description"

    def toString(self):
        """Return controlled physical plan text.

        Provide plan text without consulting a real JVM.

        """
        return self.name + " physical plan"


class Query:
    """Expose controlled query plans for Spark observation tests.

    Attributes
    ----------
    plan : Plan
        Controlled executed physical plan returned by this query.

    analyzed : Callable[[], Plan]
        Alias supplying a logical plan for the analyzed-plan stage.

    optimizedPlan : Callable[[], Plan]
        Alias supplying a logical plan for the optimized-plan stage.

    sparkPlan : Callable[[], Plan]
        Alias supplying a plan for the pre-execution physical stage.

    """

    def __init__(self, plan):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.plan = plan

    def id(self):
        """Return the controlled plan or query identifier.

        Keep identifiers stable so cycle handling and query correlation can be
        checked.

        """
        return 17

    def executedPlan(self):
        """Return the controlled executed physical plan.

        Preserve the exact fake plan object supplied by the case.

        """
        return self.plan

    def logical(self):
        """Return a controlled logical plan for preparation-stage assertions.

        Avoid Spark execution while exposing preparation-stage metadata.

        """
        return Plan("logical")

    analyzed = optimizedPlan = sparkPlan = logical


class Session:
    """Retain controlled integration results and observed source locations.

    Attributes
    ----------
    registry : object
        Minimal source registry used by the integration under test.

    result : [ProfileResult]
        Normalized result receiving observed notebook or Spark runs.

    locations : list[Any]
        Source locations supplied when child runs or actions are attached.

    """

    def __init__(self):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.registry = SimpleNamespace(snapshot=lambda _filename: None)
        self.result = ProfileResult(ProfileRun(), {}, "trace", BackendCapabilities())
        self.locations = []

    def add_spark_execution(self, execution, locations):
        """Attach an observed action and record its source references.

        Use controlled JVM-like plans and method wrappers to inspect action
        attribution without Java or a Spark cluster.

        """
        self.result.root_run.spark_executions.append(execution)
        self.locations.append(locations)


def integration(session=None):
    """Provide a Spark observer with controlled session and JVM behavior.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
    observer = SparkIntegration(session or Session(), spark=SimpleNamespace())
    observer._listener_attempted = True
    return observer


def test_scala_iteration_and_missing_collection():
    """Verify scala iteration and missing collection.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify operator metrics preserve raw units and unknowns.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify aqe final plan and query stage are unwrapped.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify plan cycles and size are bounded.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
    parent, child = Plan("parent", 1), Plan("child", 2)
    parent.nodes, child.nodes = [child], [parent]
    assert parse_plan(parent)[0].children[0].children == []
    assert parse_plan(parent, max_nodes=1)[0].children == []
    assert parse_plan(parent, max_nodes=0) == []


def test_nonfinal_and_input_plan_fallback_are_explicit():
    """Verify nonfinal and input plan fallback are explicit.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
    plan = Plan("AdaptiveSparkPlan")
    plan.executedPlan = lambda: Plan("Filter", 2)
    plan.isFinalPlan = lambda: False
    record = SparkExecution("x")
    capture_query(record, Query(plan), actual=False)
    assert any("AQE final plan" in warning for warning in record.warnings)
    assert any("Input DataFrame plan only" in warning for warning in record.warnings)


def test_aqe_initial_physical_plan_preferred_over_unprepared_plan():
    """Verify aqe initial physical plan preferred over unprepared plan.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify record action invokes callback once and preserves result.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify action error preserved and missing jvm is supported.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
    observer = integration()

    def fail():
        """Raise a controlled error for the failure path under test.

        Keep the original exception observable so cleanup cannot silently
        replace it.

        """
        raise RuntimeError("original")

    with pytest.raises(RuntimeError, match="original"):
        observer.record_action(object(), "count", fail)
    record = observer.session.result.root_run.spark_executions[0]
    assert record.status == "failed"
    assert record.executed_plan is None
    assert record.warnings
    assert observer._depth == 0


def test_malformed_plan_metadata_never_discards_action_or_result():
    """Verify malformed plan metadata never discards action or result.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
    observer = integration()
    metric = SimpleNamespace(value=lambda: "not numeric")
    frame = SimpleNamespace(
        _jdf=SimpleNamespace(queryExecution=lambda: Query(Plan(metrics={"bad": metric})))
    )
    assert observer.record_action(frame, "count", lambda: 42) == 42
    assert len(observer.session.result.root_run.spark_executions) == 1
    assert observer.session.result.warnings


def test_listener_replaces_input_fallback_with_actual_query():
    """Verify listener replaces input fallback with actual query.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify listener never guesses between ambiguous actions.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Verify stage counts and missing retention.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Check the expected behavior in this regression case.

    Verify optional stage metrics distinguish wall cumulative and task
    quantiles.

    """
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
    """Check the expected behavior in this regression case.

    Verify execution executor time deduplicates stages and excludes reused work.

    """
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
    """Verify execution executor time stays unknown when status is incomplete.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """
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
    """Check the expected behavior in this regression case.

    Verify automatic wrappers preserve laziness lineage multiple actions and
    restore.

    """
    calls = []

    class DataFrame:
        """Emulate lazy transformations and observed Spark actions.

        Return controlled lazy transformation and action results for wrapper
        assertions.

        """

        def filter(self, condition):
            """Return a lazy transformed DataFrame without triggering an action.

            Retain transformation lineage for attribution when a later action
            runs.

            """
            del condition
            calls.append("filter")
            return DataFrame()

        def count(self):
            """Provide the controlled behavior used by this test.

            Return a controlled Spark action result for observation assertions.

            """
            calls.append("count")
            return 10

    original = DataFrame.count

    def install():
        """Install controlled Spark method wrappers for lifecycle assertions.

        Use controlled JVM-like plans and method wrappers to inspect action
        attribution without Java or a Spark cluster.

        """
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
    """Verify wrappers ignore other threads and restore nested sessions.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """

    class Frame:
        """Provide a minimal DataFrame stand-in for action observation tests.

        Keep callbacks distinguishable for thread and session ownership checks.

        """

        def count(self):
            """Provide the controlled behavior used by this test.

            Return a controlled Spark action result for observation assertions.

            """
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
    """Verify writer uses owning dataframe and does not duplicate nested action.

    Use controlled JVM-like plans and method wrappers to inspect action
    attribution without Java or a Spark cluster.

    """

    class Frame:
        """Provide a minimal DataFrame stand-in for action observation tests.

        Attributes
        ----------
        _jdf : SimpleNamespace
            Fake JVM DataFrame exposing a controlled executed query.

        """

        _jdf = SimpleNamespace(queryExecution=lambda: Query(Plan("Scan")))

    class Writer:
        """Emulate nested Spark write actions and their DataFrame ownership.

        Attributes
        ----------
        _df : Frame
            Owning DataFrame used to correlate nested writer actions.

        """

        def __init__(self, frame):
            """Initialize the controlled test state and recorded observations.

            Retain only the state needed to observe arguments, results, and
            cleanup in the surrounding test.

            """
            self._df = frame

        def save(self):
            """Simulate a Spark write action through its owning DataFrame.

            Keep nested writes observable without introducing a real Spark
            action.

            """
            return self.parquet()

        def parquet(self):
            """Provide the controlled behavior used by this test.

            Delegate a simulated parquet write through the controlled save
            action.

            """
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
