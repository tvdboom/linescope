"""LineScope.

Author: Mavs
Description: Verify default Spark observation activates lazily and restores
import hooks, methods, and listeners without requiring PySpark or Java.

"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from linescope import Config, Session
from linescope.model import SourceLocation
import linescope.spark.listener as listener


@pytest.fixture
def fake_spark(tmp_path, monkeypatch):
    """Provide unloaded or lazily loaded fake PySpark modules.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    for name in tuple(sys.modules):
        if name == "pyspark" or name.startswith("pyspark."):
            monkeypatch.delitem(sys.modules, name)

    files = {
        "pyspark/__init__.py": "events = []\n",
        "pyspark/sql/__init__.py": (
            "from .dataframe import DataFrame\nfrom .session import SparkSession\n"
        ),
        "pyspark/sql/dataframe.py": (
            "from pyspark import events\n"
            "class DataFrame:\n"
            "    def filter(self, condition):\n"
            "        events.append('filter')\n"
            "        return DataFrame()\n"
            "    def count(self):\n"
            "        events.append('count')\n"
            "        if getattr(self, 'fail', False):\n"
            "            raise RuntimeError('Spark action failed')\n"
            "        return 7\n"
        ),
        "pyspark/sql/session.py": (
            "from pyspark import events\n"
            "class SparkSession:\n"
            "    active = None\n"
            "    @staticmethod\n"
            "    def getActiveSession():\n"
            "        events.append('detect')\n"
            "        return SparkSession.active\n"
        ),
        "pyspark/java_gateway.py": (
            "from pyspark import events\n"
            "def ensure_callback_server_started(gateway):\n"
            "    events.append('callback')\n"
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path

    # Remove modules introduced by the fake imports before monkeypatch restores
    # any PySpark modules that were present when this test started.
    for name in tuple(sys.modules):
        if name == "pyspark" or name.startswith("pyspark."):
            sys.modules.pop(name)


def session(root, **options):
    """Provide a controlled profiling session for lazy Spark observation.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    return Session(backend="trace", root=str(root), notebooks=False, display="none", **options)


def attach_jvm(sql):
    """Attach controlled listener and query metadata without starting Java.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    events = sys.modules["pyspark"].events
    manager = SimpleNamespace(
        register=lambda _observer: events.append("register"),
        unregister=lambda _observer: events.append("unregister"),
    )
    bus = SimpleNamespace(waitUntilEmpty=lambda _timeout: events.append("drain"))
    sql.SparkSession.active = SimpleNamespace(
        sparkContext=SimpleNamespace(
            _gateway=object(),
            _jsc=SimpleNamespace(sc=lambda: SimpleNamespace(listenerBus=lambda: bus)),
        ),
        _jsparkSession=SimpleNamespace(listenerManager=lambda: manager),
    )
    return events


def test_default_profile_does_not_import_installed_but_unused_spark(fake_spark):
    """Verify default profile does not import installed but unused spark.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    finders = sys.meta_path[:]
    with session(fake_spark) as profile:
        assert profile.config.spark is True
        assert sum(range(10)) == 45
        assert not any(name.startswith("pyspark") for name in sys.modules)
    assert sys.meta_path == finders
    assert not profile.result.root_run.spark_executions
    assert not any("Spark" in warning for warning in profile.result.warnings)
    assert not listener._ACTIVE
    assert not listener._PATCHES


def test_loaded_but_unused_spark_does_not_inspect_session_or_start_listener(fake_spark):
    """Check the expected behavior in this regression case.

    Verify loaded but unused spark does not inspect session or start listener.

    """
    sql = importlib.import_module("pyspark.sql")
    events = attach_jvm(sql)
    original = sql.DataFrame.count
    finders = sys.meta_path[:]
    with session(fake_spark):
        assert sum(range(10)) == 45
        assert events == []
        assert "pyspark.java_gateway" not in sys.modules
    assert sql.DataFrame.count is original
    assert events == []
    assert sys.meta_path == finders


@pytest.mark.parametrize("import_before", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_first_action_activates_listener_and_cleanup_preserves_workload(
    fake_spark, monkeypatch, import_before, fail
):
    """Verify first action activates listener and cleanup preserves workload.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    sql = importlib.import_module("pyspark.sql") if import_before else None
    original = sql.DataFrame.count if sql is not None else None
    finders = sys.meta_path[:]
    location = SourceLocation("pipeline.py", 3)
    monkeypatch.setattr(listener, "caller_location", lambda _profile: location)
    profile = session(fake_spark)

    def workload():
        """Execute the controlled workload while collection is active.

        Control module loading, listener startup, and method patches to verify
        that observation leaves transformations lazy.

        """
        nonlocal sql
        sql = importlib.import_module("pyspark.sql")
        events = attach_jvm(sql)
        assert events == []
        assert "pyspark.java_gateway" not in sys.modules
        dataframe_module = sys.modules["pyspark.sql.dataframe"]
        assert not isinstance(dataframe_module.__loader__, listener._SparkLoader)
        assert dataframe_module.__loader__ is dataframe_module.__spec__.loader
        frame = sql.DataFrame().filter("id > 1")
        assert events == ["filter"]
        assert not profile.result.root_run.spark_executions
        assert frame.count() == frame.count() == 7
        assert events == ["filter", "detect", "callback", "register", "count", "count"]
        assert len(profile.result.root_run.spark_executions) == 2
        assert all(
            record.location == location for record in profile.result.root_run.spark_executions
        )
        if fail:
            raise RuntimeError("workload failed")

    if fail:
        with pytest.raises(RuntimeError, match="workload failed"), profile:
            workload()
    else:
        with profile:
            workload()

    assert sql is not None
    events = sys.modules["pyspark"].events
    assert events[-2:] == ["drain", "unregister"]
    assert sys.meta_path == finders
    assert not listener._ACTIVE
    assert not listener._PATCHES
    assert (
        sql.DataFrame.count is original
        if original is not None
        else not hasattr(sql.DataFrame.count, "__wrapped__")
    )
    assert sql.DataFrame().count() == 7
    assert len(profile.result.root_run.spark_executions) == 2


def test_disabled_spark_does_not_watch_imports_or_wrap_methods(fake_spark):
    """Verify disabled spark does not watch imports or wrap methods.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    finders = sys.meta_path[:]
    with session(fake_spark, spark=False) as profile:
        assert sys.meta_path == finders
        sql = importlib.import_module("pyspark.sql")
        events = attach_jvm(sql)
        assert sql.DataFrame().count() == 7
        assert events == ["count"]
    assert not profile.result.root_run.spark_executions


def test_first_action_failure_records_error_and_restores_lazy_observation(fake_spark):
    """Verify first action failure records error and restores lazy observation.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    finders = sys.meta_path[:]
    profile = session(fake_spark)
    sql = importlib.import_module("pyspark.sql")
    events = attach_jvm(sql)
    frame = sql.DataFrame()
    frame.fail = True
    with pytest.raises(RuntimeError, match="Spark action failed"), profile:
        frame.count()
    assert events == ["detect", "callback", "register", "count", "drain", "unregister"]
    assert profile.result.root_run.spark_executions[0].status == "failed"
    assert sys.meta_path == finders
    assert not hasattr(sql.DataFrame.count, "__wrapped__")


def test_listener_failure_preserves_first_action_and_reports_limitation(fake_spark):
    """Verify listener failure preserves first action and reports limitation.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    (fake_spark / "pyspark/java_gateway.py").write_text(
        "from pyspark import events\n"
        "def ensure_callback_server_started(gateway):\n"
        "    events.append('callback')\n"
        "    raise RuntimeError('Callbacks unavailable')\n",
        encoding="utf-8",
    )
    finders = sys.meta_path[:]
    with session(fake_spark) as profile:
        sql = importlib.import_module("pyspark.sql")
        events = attach_jvm(sql)
        assert sql.DataFrame().count() == sql.DataFrame().count() == 7
        assert events == ["detect", "callback", "count", "count"]
    assert len(profile.result.root_run.spark_executions) == 2
    assert any(
        "Spark metadata unavailable (RuntimeError)" in msg for msg in profile.result.warnings
    )
    assert sys.meta_path == finders
    assert not hasattr(sql.DataFrame.count, "__wrapped__")


def test_late_patch_failure_preserves_user_import_and_reports_limitation(fake_spark, monkeypatch):
    """Verify late patch failure preserves user import and reports limitation.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """

    def fail():
        """Raise a controlled error for the failure path under test.

        Keep the original exception observable so cleanup cannot silently
        replace it.

        """
        raise RuntimeError("Cannot wrap Spark")

    finders = sys.meta_path[:]
    with session(fake_spark) as profile:
        monkeypatch.setattr(listener, "_install_patches", fail)
        sql = importlib.import_module("pyspark.sql")
        assert sql.DataFrame().count() == 7
    assert any(
        "Spark metadata unavailable (RuntimeError)" in msg for msg in profile.result.warnings
    )
    assert sys.meta_path == finders
    assert not listener._ACTIVE


def test_failed_spark_import_preserves_exception_and_restores_watcher(fake_spark):
    """Verify failed spark import preserves exception and restores watcher.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    (fake_spark / "pyspark/sql/dataframe.py").write_text(
        "raise RuntimeError('Spark import failed')\n", encoding="utf-8"
    )
    finders = sys.meta_path[:]
    with pytest.raises(RuntimeError, match="Spark import failed"), session(fake_spark):
        importlib.import_module("pyspark.sql")
    assert "pyspark.sql.dataframe" not in sys.modules
    assert sys.meta_path == finders
    assert not listener._ACTIVE
    assert not listener._PATCHES


def test_partial_observer_startup_restores_methods_and_watcher(fake_spark, monkeypatch):
    """Verify partial observer startup restores methods and watcher.

    Control module loading, listener startup, and method patches to verify that
    observation leaves transformations lazy.

    """
    sql = importlib.import_module("pyspark.sql")
    original = sql.DataFrame.count
    finders = sys.meta_path[:]

    def install_then_fail():
        """Simulate partial observer startup followed by a cleanup error.

        Control module loading, listener startup, and method patches to verify
        that observation leaves transformations lazy.

        """
        listener._install_class(sql.DataFrame, {"count"}, listener._MethodKind.ACTION)
        raise RuntimeError("observer startup failed")

    monkeypatch.setattr(listener, "_install_patches", install_then_fail)
    with pytest.raises(RuntimeError, match="observer startup failed"):
        session(fake_spark).start()
    assert sql.DataFrame.count is original
    assert sys.meta_path == finders
    assert not listener._ACTIVE
    assert not listener._PATCHES


def test_auto_spark_configuration_is_rejected_in_project_and_global_settings(
    fake_spark, monkeypatch
):
    """Check the expected behavior in this regression case.

    Verify auto spark configuration is rejected in project and global settings.

    """
    monkeypatch.setattr("linescope.config._overrides", {})
    from linescope import configure
    from linescope.config import resolve_config

    with pytest.raises(ValueError, match="spark must be True or False"):
        configure(spark="auto")
    assert Config().spark is True
    (fake_spark / "pyproject.toml").write_text(
        '[tool.linescope]\nspark = "auto"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="spark must be True or False"):
        resolve_config(root=str(fake_spark))
