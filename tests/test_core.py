"""LineScope.

Author: Mavs
Description: Public lifecycle, configuration, timing, and failure regression tests.

"""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from linescope import Config, Session, configure, profile, profiler
from linescope.api import ProfileController
from linescope.backends import RawBackendResult, RawLine, register_backend
from linescope.backends.base import create_backend
from linescope.backends.trace import TraceBackend
from linescope.config import resolve_config
from linescope.model import (
    BackendCapabilities,
    MemoryStats,
    ProfileRun,
    SourceLocation,
    SparkExecution,
)


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    """Keep each test independent of persistent API configuration."""
    monkeypatch.setattr("linescope.config._overrides", {})


def execute(tmp_path, text, *, namespace=None, **options):
    """Run a real temporary source file through the public session API."""
    path = tmp_path / "workload.py"
    path.write_text(text, encoding="utf-8")
    scope = {} if namespace is None else namespace
    session = Session(
        backend="trace",
        root=str(tmp_path),
        display="none",
        notebooks=False,
        spark=False,
        **options,
    )
    with session:
        exec(compile(text, str(path), "exec"), scope)
    return session, scope


class TestConfiguration:
    """Validate precedence and useful error messages."""

    def test_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert resolve_config().backend == "scalene"
        assert resolve_config().memory is False
        assert resolve_config().spark == "auto"

    def test_project_global_explicit_precedence(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.linescope]\nbackend="trace"\nmemory=true\ninclude=["project"]\n'
        )
        child = tmp_path / "src"
        child.mkdir()
        monkeypatch.chdir(child)
        assert resolve_config().include == ("project",)
        configure(memory=False, display="none")
        assert resolve_config().memory is False
        assert resolve_config(memory=True).memory is True
        assert resolve_config(include=[]).include == ()
        assert resolve_config().root == str(tmp_path.resolve())

    def test_project_relative_root(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[tool.linescope]\nroot="src"\n')
        assert resolve_config(root=str(tmp_path)).root == str(tmp_path)

    def test_invalid_global_update_is_atomic(self):
        configure(backend="trace")
        with pytest.raises(ValueError, match="display"):
            configure(backend="invalid", display="bad")
        assert resolve_config().backend == "trace"

    @pytest.mark.parametrize(
        "options,error",
        [
            ({"memory": "yes"}, TypeError),
            ({"notebooks": 1}, TypeError),
            ({"include": "pkg"}, TypeError),
            ({"exclude": [""]}, ValueError),
            ({"spark": "all"}, ValueError),
            ({"spark": 1}, ValueError),
            ({"backend": ""}, ValueError),
            ({"display": "always"}, ValueError),
            ({"output": ""}, ValueError),
            ({"extra": 1}, TypeError),
        ],
    )
    def test_invalid_values(self, options, error):
        with pytest.raises(error):
            Config(**options)

    def test_frozen_config(self):
        with pytest.raises(AttributeError):
            Config().memory = True

    def test_prevalidated_config(self, tmp_path):
        config = Config(root=str(tmp_path), backend="trace")
        assert Session(config).config is config
        with pytest.raises(TypeError, match="not both"):
            Session(config, memory=False)


class TestSession:
    """Cover profiling contexts, explicit sessions, and cleanup."""

    def test_source_hits_navigation_and_unexecuted_lines(self, tmp_path):
        source = "def double(x):\n    return x * 2\n\ndef never():\n    return -1\n\nvalue = double(21)\n"
        session, scope = execute(tmp_path, source)
        assert scope["value"] == 42
        assert session.state == "stopped"
        assert len(session.result.sources) == 1
        assert next(iter(session.result.sources.values())).source == source
        rows = {line.location.line: line for line in session.result.root_run.lines}
        assert rows[2].hits == 1
        assert rows[7].calls[0].name == "double"
        assert rows[7].calls[0].target.line == 1
        assert 5 not in rows
        functions = {
            function.qualified_name.rsplit(".", 1)[-1]: function
            for function in session.result.root_run.functions
        }
        assert functions["double"].calls == 1
        assert functions["never"].calls == 0
        assert functions["never"].total_time_ns is None
        assert 'class="source-row"' in session.html()

    def test_external_work_stays_on_calling_line(self, tmp_path, monkeypatch):
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            clock[0] += 10_000_000

        session, _ = execute(
            tmp_path, "value = external()\nfinished = True\n", namespace={"external": external}
        )
        rows = {line.location.line: line for line in session.result.root_run.lines}
        assert rows[1].wall_time_ns == 10_000_000
        assert rows[2].wall_time_ns == 0
        assert len(session.result.sources) == 1

    def test_project_child_time_is_not_double_counted(self, tmp_path, monkeypatch):
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            clock[0] += 5_000_000

        session, _ = execute(
            tmp_path, "def child():\n    external()\nchild()\n", namespace={"external": external}
        )
        rows = {line.location.line: line for line in session.result.root_run.lines}
        assert rows[2].wall_time_ns == 5_000_000
        assert rows[3].wall_time_ns == 0

    def test_exception_restores_hooks_and_new_session_works(self, tmp_path):
        path = tmp_path / "raises.py"
        path.write_text('raise ValueError("workload failed")\n')
        prior = sys.gettrace()
        session = Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        )
        with pytest.raises(ValueError, match="workload failed"), session:
            exec(compile(path.read_text(), str(path), "exec"))
        assert session.result.root_run.status == "failed"
        assert session.state == "stopped"
        assert sys.gettrace() is prior
        assert execute(tmp_path, "x = 1\n")[1]["x"] == 1

    def test_explicit_controller_and_alias(self, tmp_path):
        assert profiler is profile
        controller = ProfileController()
        session = controller.start(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        )
        with pytest.raises(RuntimeError, match="already running"):
            controller.start()
        assert controller.stop() is session.result
        assert controller.stop() is session.result
        saved = controller.save(tmp_path / "result.html")
        assert saved.is_file()

    def test_overlapping_sessions_rejected(self, tmp_path):
        with Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ):
            with pytest.raises(RuntimeError, match="already running"):
                Session(backend="trace").start()

    def test_no_double_display_on_repeated_stop(self, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(Session, "show", lambda session: shown.append(session))
        session = Session(
            backend="trace", root=str(tmp_path), display="end", notebooks=False, spark=False
        )
        session.start()
        session.stop()
        session.stop()
        assert shown == [session]
        with pytest.raises(RuntimeError, match="only start once"):
            session.start()

    @pytest.mark.parametrize("operation", ["stop", "html", "save", "show"])
    def test_before_start_errors(self, tmp_path, operation):
        session = Session(backend="trace", root=str(tmp_path))
        with pytest.raises(RuntimeError, match="Start"):
            getattr(session, operation)(tmp_path / "out.html") if operation == "save" else getattr(
                session, operation
            )()

    def test_controller_without_session(self):
        with pytest.raises(RuntimeError, match="profile.start"):
            ProfileController().stop()

    def test_memory_not_faked_by_trace(self):
        with pytest.raises(ValueError, match="cannot measure memory"):
            Session(backend="trace", memory=True).start()
        # Failed startup must release the global collector ownership lock.
        session = Session(backend="trace", spark=False, notebooks=False, display="none")
        session.start().stop()

    def test_stop_from_wrong_thread_leaves_owner_in_control(self, tmp_path):
        session = Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ).start()
        failures = []

        def stop():
            try:
                session.stop()
            except RuntimeError as error:
                failures.append(str(error))

        worker = threading.Thread(target=stop)
        worker.start()
        worker.join()
        assert failures and "thread" in failures[0]
        assert session.state == "running"
        session.stop()

    def test_running_snapshot_and_save(self, tmp_path):
        with Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ) as session:
            assert "LineScope" in session.html()
            assert session.save(tmp_path / "live.html").is_file()
            assert session.state == "running"

    def test_original_source_survives_edit(self, tmp_path):
        session, _ = execute(tmp_path, "answer = 42\n")
        (tmp_path / "workload.py").write_text("answer = 0\n")
        assert next(iter(session.result.sources.values())).source == "answer = 42\n"

    def test_previous_trace_restored_and_receives_events(self, tmp_path):
        received = []
        prior = sys.gettrace()

        def tracer(frame, event, arg):
            if frame.f_code.co_filename.endswith("workload.py"):
                received.append(event)
            return tracer

        sys.settrace(tracer)
        try:
            execute(tmp_path, "x = 1\ny = 2\n")
            assert sys.gettrace() is tracer
            assert received.count("line") == 2
        finally:
            sys.settrace(prior)

    def test_generator_resumptions_count_as_one_call(self, tmp_path):
        session, _ = execute(
            tmp_path, "def numbers():\n    yield 1\n    yield 2\nvalues = list(numbers())\n"
        )
        function = next(
            item
            for item in session.result.root_run.functions
            if item.qualified_name.endswith("numbers")
        )
        assert function.calls == 1

    def test_suspension_not_charged_to_generator(self, tmp_path, monkeypatch):
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            clock[0] += 10_000_000

        source = "def numbers():\n    yield 1\n    yield 2\ng = numbers()\nnext(g)\nexternal()\nnext(g)\n"
        session, _ = execute(tmp_path, source, namespace={"external": external})
        function = next(
            item
            for item in session.result.root_run.functions
            if item.qualified_name.endswith("numbers")
        )
        assert function.total_time_ns == 0

    def test_coroutine_resumptions_count_as_one_call(self, tmp_path):
        source = "import asyncio\nasync def task():\n    await asyncio.sleep(0)\n    await asyncio.sleep(0)\n    return 42\nvalue = asyncio.run(task())\n"
        session, namespace = execute(tmp_path, source)
        assert namespace["value"] == 42
        function = next(
            item
            for item in session.result.root_run.functions
            if item.qualified_name.endswith("task")
        )
        assert function.calls == 1

    def test_multiple_spark_executions_and_child_references(self, tmp_path):
        session, _ = execute(tmp_path, "x = 1\n")
        source = next(iter(session.result.sources.values()))
        location = SourceLocation(source.id, 1)
        for identity in ("first", "second"):
            session.add_spark_execution(SparkExecution(identity), [location, location])
        child = ProfileRun(name="child")
        session.add_child_run(child, location)
        session._refresh()
        row = session.result.root_run.lines[0]
        assert row.spark_executions == ["first", "second"]
        assert row.notebook_runs == [child.id]
        assert child.parent_id == session.result.root_run.id

    def test_browser_show_and_inline_show(self, tmp_path, monkeypatch):
        session, _ = execute(tmp_path, "x = 1\n", output=str(tmp_path / "shown.html"))
        opened = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        monkeypatch.setattr("IPython.get_ipython", lambda: None)
        session.show()
        assert opened == [(tmp_path / "shown.html").as_uri()]
        displayed = []
        monkeypatch.setattr("IPython.get_ipython", lambda: SimpleNamespace())
        monkeypatch.setattr("IPython.display.display", displayed.append)
        session.show()
        assert len(displayed) == 1
        assert "srcdoc=" in displayed[0].data
        assert len(opened) == 1


class TestBackendProtocol:
    """Prove extensions can supply optional measurements independently of UI."""

    def test_unknown_and_duplicate_names(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            create_backend("missing")
        with pytest.raises(ValueError, match="already registered"):
            register_backend("trace", lambda **kwargs: None)

    def test_custom_collector_aggregation(self, tmp_path, monkeypatch):
        path = tmp_path / "custom.py"
        path.write_text("value = 42\n")

        class Custom:
            name = "custom"
            capabilities = BackendCapabilities(memory=True)

            def __init__(self, **options):
                self.options = options

            def start(self):
                self.options["on_source"](str(path))

            def stop(self):
                pass

            def result(self):
                return RawBackendResult(
                    [
                        RawLine(str(path), 1, 100, None, MemoryStats(10, 20)),
                        RawLine(str(path), 1, 200, None, MemoryStats(-2, 15)),
                    ],
                    ["custom note"],
                )

        monkeypatch.setattr("linescope.backends.base._factories", {})
        register_backend("custom", Custom)
        with Session(
            backend="custom", root=str(tmp_path), display="none", spark=False, notebooks=False
        ) as session:
            pass
        line = session.result.root_run.lines[0]
        assert line.wall_time_ns == 300
        assert line.hits is None
        assert line.memory == MemoryStats(8, 20)
        assert session.result.warnings == ["custom note"]

    def test_backend_failure_releases_session_lock(self, monkeypatch):
        backend = SimpleNamespace(
            name="broken",
            capabilities=BackendCapabilities(),
            start=lambda: (_ for _ in ()).throw(ValueError("broken start")),
            stop=lambda: None,
        )
        monkeypatch.setattr("linescope.api.create_backend", lambda *args, **kwargs: backend)
        for _ in range(2):
            with pytest.raises(ValueError, match="broken start"):
                Session(notebooks=False, spark=False).start()

    def test_trace_result_is_detached(self, tmp_path):
        session, _ = execute(tmp_path, "x = 1\n")
        raw = session._backend.result()
        raw.lines.clear()
        assert session._backend.result().lines

    def test_trace_stop_is_idempotent(self):
        backend = TraceBackend(accepts=lambda _: False, on_source=lambda _: None)
        backend.start()
        with pytest.raises(RuntimeError, match="already running"):
            backend.start()
        backend.stop()
        backend.stop()
