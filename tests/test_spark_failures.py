"""LineScope.

Author: Mavs
Description: Keep optional Spark metadata failures lazy and correctly scoped.

"""

from importlib.machinery import ModuleSpec
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope.model import SourceLocation, SparkExecution
from linescope.spark import listener, metrics
from linescope.spark.plans import parse_plan
from tests.test_spark import Plan, Query, integration


def test_query_callbacks_reject_concurrency_and_preserve_unknown_duration():
    """Reject concurrent plan attribution and deduplicate its diagnostic.

    Match Spark's collect callback alias only when one eligible action exists,
    and retain unavailable JVM duration on failed query callbacks.

    """
    observer = integration()
    observer._query_finished("collect", Query(Plan()), 1, "success")
    observer._active = True
    observer._concurrent_queries = True
    for _ in range(2):
        observer._query_finished("collect", Query(Plan()), 1, "success")
    assert len(observer.session.result.warnings) == 1
    observer._concurrent_queries = False
    observer.record_action(object(), "collect", lambda: 1)
    callback = listener._QueryListener(observer)
    callback.onFailure("collectToPython", Query(Plan()), "secret error")
    record = observer.session.result.root_run.spark_executions[0]
    assert record.status == "failed"
    assert "query_wall_time_ns" not in record.metadata
    assert record.executed_plan
    assert not observer._pending


def test_query_metadata_failure_never_replaces_workload_result(monkeypatch):
    """Keep optional query and source attribution failures diagnostic.

    Execute the action once and retain its return value even when collecting
    final metadata or attaching it to the session fails.

    """
    observer = integration()
    observer._active = True
    monkeypatch.setattr(listener, "caller_location", Mock(side_effect=ValueError("source")))
    monkeypatch.setattr(listener, "capture_query", Mock(side_effect=ValueError("query")))
    dataframe = SimpleNamespace(_jdf=SimpleNamespace(queryExecution=lambda: Query(Plan())))
    observer.session.add_spark_execution = Mock(side_effect=ValueError("attach"))
    action = Mock(return_value=42)
    assert observer.record_action(dataframe, "count", action) == 42
    action.assert_called_once_with()
    observer._query_finished("count", Query(Plan()), 1, "success")
    assert observer._depth == 0
    assert any("ValueError" in item for item in observer.session.result.warnings)


def test_listener_shutdown_errors_restore_import_watcher(monkeypatch):
    """Restore import observation even when JVM draining and detach fail.

    Clear owned lineage and report restricted listener access without leaving
    an active observer or an import watcher behind.

    """
    observer = integration()
    monkeypatch.setattr(listener, "_install_patches", Mock())
    observer.start()
    observer.start()
    observer._listener_manager = SimpleNamespace(unregister=Mock(side_effect=OSError("denied")))
    observer._listener = object()
    observer.stop()
    assert not listener._ACTIVE
    assert listener._IMPORT_WATCHER is None
    assert not observer._lineage
    assert observer.session.result.warnings


def test_active_session_detection_failure_remains_unavailable(monkeypatch):
    """Treat a restricted active-session API as unavailable metadata.

    Do not create a Spark session or propagate optional JVM access failure.

    """
    observer = integration()
    observer.spark = None
    monkeypatch.setitem(
        listener.sys.modules,
        "pyspark.sql",
        SimpleNamespace(
            SparkSession=SimpleNamespace(getActiveSession=Mock(side_effect=OSError("denied"))),
        ),
    )
    assert observer._detect_session() is None


def test_lineage_rejects_nonweak_objects_and_stale_owners():
    """Retain only live object lineage and preserve writer ownership.

    A reused identity must not attach an unrelated object's transformations
    to a later Spark action.

    """

    class Frame:
        """Provide a weak-referenceable lazy plan without executing Spark.

        Instances carry no JVM state or actions.

        """

    class WriterV2(Frame):
        """Represent a writer whose DataFrame owner must be retained.

        Inherit weak-reference support without adding instance attributes.

        """

    observer = integration()
    parent, writer = Frame(), WriterV2()
    location = SourceLocation("main.py", 1)
    observer._remember(None, parent, (), location)
    observer._remember(1, parent, (), location)
    assert not observer._lineage
    observer._remember(writer, parent, (), location)
    assert observer._owners[id(writer)] is parent
    assert observer._locations(writer) == [location]
    observer._lineage[id(parent)] = observer._lineage[id(writer)]
    assert observer._locations(parent) == []


def test_loader_metadata_and_patch_failures_preserve_successful_import(monkeypatch):
    """Forward loader construction and preserve imports after patch failures.

    Handle modules without owned loader metadata and report optional patch
    errors to each active observer.

    """
    original = SimpleNamespace(exec_module=Mock())
    proxy = listener._SparkLoader(original)
    assert proxy.create_module(ModuleSpec("pyspark.sql", original)) is None
    observer = integration()
    monkeypatch.setattr(listener, "_ACTIVE", {1: [observer]})
    monkeypatch.setattr(listener, "_install_patches", Mock(side_effect=ValueError("patch")))
    module = ModuleType("pyspark.sql")
    proxy.exec_module(module)
    original.exec_module.assert_called_once_with(module)
    assert observer.session.result.warnings
    finder = listener._SparkFinder()
    monkeypatch.setattr(
        listener,
        "sys",
        SimpleNamespace(
            meta_path=[
                finder,
                SimpleNamespace(find_spec=Mock(return_value=None)),
                SimpleNamespace(find_spec=Mock(return_value=ModuleSpec("pyspark.sql", None))),
            ]
        ),
    )
    assert finder.find_spec("pyspark.sql.session") is not None
    listener.sys.meta_path = [finder]
    assert finder.find_spec("pyspark.sql.session") is None


def test_operator_metrics_skip_missing_names_and_unavailable_values():
    """Omit unnamed or unavailable operator metrics without guessing zeros.

    Read tuple-style Scala metric maps while preserving supplied native units.

    """
    entries = [
        SimpleNamespace(_1=lambda: None, _2=lambda: object()),
        SimpleNamespace(_1=lambda: "missing", _2=lambda: SimpleNamespace(value=None)),
        SimpleNamespace(_1=lambda: "rows", _2=lambda: SimpleNamespace(value=2)),
    ]
    result = metrics.operator_metrics(SimpleNamespace(metrics=lambda: entries))
    assert result == {"rows": {"value": 2, "name": "rows", "type": "unknown"}}


@pytest.mark.parametrize("kind", ["empty", "absent", "short", "error"])
def test_missing_task_summary_remains_unavailable(kind):
    """Keep absent or inaccessible task quantiles out of stage metadata.

    Return retained stage identity even when status-store quantiles are
    incomplete, and return no metadata when the stage itself is absent.

    """
    distribution = SimpleNamespace(
        isDefined=lambda: kind != "absent", get=lambda: SimpleNamespace(duration=lambda: [1])
    )
    store = SimpleNamespace(
        stageData=lambda *_args: [] if kind == "empty" else [SimpleNamespace(attemptId=1)],
        taskSummary=Mock(
            return_value=distribution,
            side_effect=OSError("restricted") if kind == "error" else None,
        ),
    )
    context = SimpleNamespace(
        _jsc=SimpleNamespace(sc=lambda: SimpleNamespace(statusStore=lambda: store)),
        _jvm=SimpleNamespace(
            java=SimpleNamespace(util=SimpleNamespace(ArrayList=list)), double=float
        ),
        _gateway=SimpleNamespace(new_array=lambda _kind, size: [0] * size),
    )
    result = metrics._stage_details(context, 1)
    assert "task_duration_ns" not in result
    assert not result if kind == "empty" else result["attempt"] == 1


def test_restricted_status_tracker_cannot_fabricate_executor_time():
    """Keep executor totals unknown when the status API cannot be read.

    Preserve stage-list failures as unavailable metadata without executing
    distributed work.

    """
    context = SimpleNamespace(statusTracker=Mock(side_effect=OSError("denied")))
    assert metrics._execution_executor_time(context, [1], []) is None
    tracker = SimpleNamespace(getJobInfo=Mock(side_effect=OSError("job unavailable")))
    context.statusTracker = lambda: tracker
    assert metrics.stage_statistics(context, [1]) == []


def test_plan_fallback_and_missing_aqe_child_preserve_available_source():
    """Retain readable plan names when descriptions and AQE children are absent.

    Preserve the bounded current-plan snapshot without requesting execution.

    """
    plan = SimpleNamespace(
        nodeName=lambda: "AdaptiveSparkPlan",
        id=lambda: 1,
        simpleString=Mock(side_effect=OSError("restricted")),
    )
    assert parse_plan(plan)[0].description == "AdaptiveSparkPlan"
    execution = SparkExecution("empty")
    assert execution.stats.executor_time_ns is None
