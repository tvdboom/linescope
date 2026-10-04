"""LineScope.

Author: Mavs
Description: Public lifecycle, configuration, timing, and failure regression
tests.

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
from tests.test_render import ReportDOM


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    """Provide isolated configuration.

    Keep each test independent of persistent API configuration.

    """
    monkeypatch.setattr("linescope.config._overrides", {})


def execute(tmp_path, text, *, namespace=None, **options):
    """Provide execute.

    Run a real temporary source file through the public session API.

    """
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
    """Check configuration.

    Validate precedence and useful error messages.

    """

    def test_defaults(self, tmp_path, monkeypatch):
        """Verify defaults.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        monkeypatch.chdir(tmp_path)
        assert resolve_config().backend == "trace"
        assert resolve_config().memory is False
        assert resolve_config().spark is True

    def test_project_global_explicit_precedence(self, tmp_path, monkeypatch):
        """Verify project global explicit precedence.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify project relative root.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        (tmp_path / "pyproject.toml").write_text('[tool.linescope]\nroot="src"\n')
        assert resolve_config(root=str(tmp_path)).root == str(tmp_path)

    def test_invalid_global_update_is_atomic(self):
        """Verify invalid global update is atomic.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        configure(backend="trace")
        with pytest.raises(ValueError, match="display"):
            configure(backend="invalid", display="bad")
        assert resolve_config().backend == "trace"

    @pytest.mark.parametrize(
        ("options", "error"),
        [
            ({"memory": "yes"}, TypeError),
            ({"notebooks": 1}, TypeError),
            ({"child_notebooks": "yes"}, TypeError),
            ({"include": "pkg"}, TypeError),
            ({"exclude": [""]}, ValueError),
            ({"spark": "all"}, ValueError),
            ({"spark": "auto"}, ValueError),
            ({"spark": 1}, ValueError),
            ({"backend": ""}, ValueError),
            ({"display": "always"}, ValueError),
            ({"output": ""}, ValueError),
            ({"extra": 1}, TypeError),
        ],
    )
    def test_invalid_values(self, options, error):
        """Verify invalid values.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with pytest.raises(error):
            Config(**options)

    def test_frozen_config(self):
        """Verify frozen config.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with pytest.raises(AttributeError):
            Config().memory = True

    def test_prevalidated_config(self, tmp_path):
        """Verify prevalidated config.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        config = Config(root=str(tmp_path), backend="trace")
        assert Session(config).config is config
        with pytest.raises(TypeError, match="not both"):
            Session(config, memory=False)


class TestSession:
    """Check session.

    Cover profiling contexts, explicit sessions, and cleanup.

    """

    def test_source_hits_navigation_and_unexecuted_lines(self, tmp_path):
        """Verify source hits navigation and unexecuted lines.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        source = (
            "def double(x):\n    return x * 2\n\ndef never():\n    return -1\n\nvalue ="
            " double(21)\n"
        )
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
        """Verify external work stays on calling line.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            """Provide the controlled behavior used by this test.

            Perform controlled external work for project attribution assertions.

            """
            clock[0] += 10_000_000

        session, _ = execute(
            tmp_path, "value = external()\nfinished = True\n", namespace={"external": external}
        )
        rows = {line.location.line: line for line in session.result.root_run.lines}
        assert rows[1].wall_time_ns == 10_000_000
        assert rows[2].wall_time_ns == 0
        assert len(session.result.sources) == 1

    def test_project_child_time_is_not_double_counted(self, tmp_path, monkeypatch):
        """Verify project child time is not double counted.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            """Provide the controlled behavior used by this test.

            Perform controlled external work for project attribution assertions.

            """
            clock[0] += 5_000_000

        session, _ = execute(
            tmp_path, "def child():\n    external()\nchild()\n", namespace={"external": external}
        )
        rows = {line.location.line: line for line in session.result.root_run.lines}
        assert rows[2].wall_time_ns == 5_000_000
        assert rows[3].wall_time_ns == 0

    def test_exception_restores_hooks_and_new_session_works(self, tmp_path):
        """Verify exception restores hooks and new session works.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify explicit controller and alias.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify overlapping sessions rejected.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ):
            with pytest.raises(RuntimeError, match="already running"):
                Session(backend="trace").start()

    def test_no_double_display_on_repeated_stop(self, tmp_path, monkeypatch):
        """Verify no double display on repeated stop.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify before start errors.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session = Session(backend="trace", root=str(tmp_path))
        with pytest.raises(RuntimeError, match="Start"):
            getattr(session, operation)(tmp_path / "out.html") if operation == "save" else getattr(
                session, operation
            )()

    def test_controller_without_session(self):
        """Verify controller without session.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with pytest.raises(RuntimeError, match=r"profile.start"):
            ProfileController().stop()

    def test_memory_disabled_stays_unavailable(self, tmp_path):
        """Verify memory disabled stays unavailable.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session, _ = execute(tmp_path, "value = bytearray(1000)\n")
        assert session.result.capabilities.memory is False
        assert all(line.memory is None for line in session.result.root_run.lines)

    def test_stop_from_wrong_thread_leaves_owner_in_control(self, tmp_path):
        """Verify stop from wrong thread leaves owner in control.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session = Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ).start()
        failures = []

        def stop():
            """Attempt collection cleanup from the thread selected by the test.

            Retain the result or exception for lifecycle and ownership
            assertions.

            """
            try:
                session.stop()
            except RuntimeError as error:
                failures.append(str(error))

        worker = threading.Thread(target=stop)
        worker.start()
        worker.join()
        assert failures
        assert "thread" in failures[0]
        assert session.state == "running"
        session.stop()

    def test_running_snapshot_and_save(self, tmp_path):
        """Verify running snapshot and save.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with Session(
            backend="trace", root=str(tmp_path), display="none", notebooks=False, spark=False
        ) as session:
            assert "LineScope" in session.html()
            assert session.save(tmp_path / "live.html").is_file()
            assert session.state == "running"

    def test_original_source_survives_edit(self, tmp_path):
        """Verify original source survives edit.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session, _ = execute(tmp_path, "answer = 42\n")
        (tmp_path / "workload.py").write_text("answer = 0\n")
        assert next(iter(session.result.sources.values())).source == "answer = 42\n"

    def test_previous_trace_restored_and_receives_events(self, tmp_path):
        """Verify previous trace restored and receives events.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        received = []
        prior = sys.gettrace()

        def tracer(frame, event, arg):
            """Record forwarded trace events and retain the previous callback.

            Return the callback so subsequent events continue through the same
            hook.

            """
            del arg
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
        """Verify generator resumptions count as one call.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify suspension not charged to generator.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        clock = [0]
        monkeypatch.setattr("linescope.backends.trace.perf_counter_ns", lambda: clock[0])

        def external():
            """Provide the controlled behavior used by this test.

            Perform controlled external work for project attribution assertions.

            """
            clock[0] += 10_000_000

        source = (
            "def numbers():\n    yield 1\n    yield 2\ng = numbers()\nnext(g)\nexternal()\n"
            "next(g)\n"
        )
        session, _ = execute(tmp_path, source, namespace={"external": external})
        function = next(
            item
            for item in session.result.root_run.functions
            if item.qualified_name.endswith("numbers")
        )
        assert function.total_time_ns == 0

    def test_coroutine_resumptions_count_as_one_call(self, tmp_path):
        """Verify coroutine resumptions count as one call.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        source = (
            "import asyncio\nasync def task():\n    await asyncio.sleep(0)\n    await"
            " asyncio.sleep(0)\n    return 42\nvalue = asyncio.run(task())\n"
        )
        session, namespace = execute(tmp_path, source)
        assert namespace["value"] == 42
        function = next(
            item
            for item in session.result.root_run.functions
            if item.qualified_name.endswith("task")
        )
        assert function.calls == 1

    def test_multiple_spark_executions_and_child_references(self, tmp_path):
        """Verify multiple spark executions and child references.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
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
        """Verify browser show and inline show.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session, _ = execute(tmp_path, "x = 1\n", output=str(tmp_path / "shown.html"))
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_kwargs: opened.append(url))
        monkeypatch.setattr("IPython.get_ipython", lambda: None)
        session.show()
        assert opened == [(tmp_path / "shown.html").as_uri()]
        displayed = []
        monkeypatch.setattr("IPython.get_ipython", lambda: SimpleNamespace())
        monkeypatch.setattr("IPython.display.display", displayed.append)
        session.show(inline=True)
        assert len(displayed) == 1
        assert "srcdoc=" in displayed[0].data
        assert len(opened) == 1

    @pytest.mark.parametrize("inline", [None, True])
    def test_inline_links_use_report_base(self, tmp_path, monkeypatch, inline):
        """Verify inline links use report base.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        source = 'value = \'</iframe><base href="https://example.invalid/"> & "quoted"\'\n'
        session, _ = execute(tmp_path, source, inline=True)
        displayed = []
        monkeypatch.setattr("IPython.get_ipython", lambda: SimpleNamespace())
        monkeypatch.setattr("IPython.display.display", displayed.append)

        html = session.show(inline=inline)
        wrapper = ReportDOM(displayed[0].data).root
        assert len(wrapper.find_all()) == 1
        iframe = wrapper.find_all("iframe")[0]
        assert iframe.attributes["sandbox"] == "allow-scripts allow-same-origin"
        inline_html = iframe.attributes["srcdoc"]
        embedded = ReportDOM(inline_html).root
        head = embedded.find_all("head")[0]
        base = head.find_all("base")[0]
        assert base.attributes["href"] == "about:srcdoc"
        assert len(embedded.find_all("base")) == 1
        policy = head.find_all("meta", **{"http-equiv": "Content-Security-Policy"})[0]
        assert head.children.index(base) < head.children.index(policy)

        ids = {node.attributes["id"] for node in embedded.find_all() if "id" in node.attributes}
        links = embedded.find_all("a")
        assert any(link.attributes["href"] == "#files" for link in links)
        for link in links:
            href = link.attributes["href"]
            if href.startswith("#"):
                assert href[1:] in ids
            else:
                assert href.startswith("https://tvdboom.github.io/linescope/")

        # Only the embedded document gets a base; source and standalone HTML survive.
        assert source.strip() in embedded.text()
        assert not ReportDOM(html).root.find_all("base")
        assert inline_html.replace('<base href="about:srcdoc">', "", 1) == html
        saved = session.save(tmp_path / "standalone.html")
        assert saved.read_text(encoding="utf-8") == html


class TestBackendProtocol:
    """Check backend protocol.

    Prove extensions can supply optional measurements independently of UI.

    """

    def test_unknown_and_duplicate_names(self):
        """Verify unknown and duplicate names.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        with pytest.raises(ValueError, match="Unknown backend"):
            create_backend("missing")
        with pytest.raises(ValueError, match="already registered"):
            register_backend("trace", lambda **_kwargs: None)

    def test_custom_collector_aggregation(self, tmp_path, monkeypatch):
        """Verify custom collector aggregation.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        path = tmp_path / "custom.py"
        path.write_text("value = 42\n")

        class Custom:
            """Exercise the registered custom collector lifecycle.

            Attributes
            ----------
            name : str
                Registered collector name selected by the test session.

            capabilities : [BackendCapabilities]
                Controlled measurements declared by the custom collector.

            options : dict[str, Any]
                Factory keyword options retained for assertions about
                forwarding.

            """

            name = "custom"
            capabilities = BackendCapabilities(memory=True)

            def __init__(self, **options):
                """Provide the controlled behavior used by this test.

                Initialize the controlled test state and recorded observations.

                """
                self.options = options

            def start(self):
                """Provide the controlled behavior used by this test.

                Start the controlled collector or simulate its configured
                startup failure.

                """
                self.options["on_source"](str(path))

            def stop(self):
                """Provide the controlled behavior used by this test.

                Attempt collection cleanup from the thread selected by the test.

                """

            def result(self):
                """Provide the controlled behavior used by this test.

                Return controlled normalized measurements for collector
                assertions.

                """
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
        """Verify backend failure releases session lock.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        backend = SimpleNamespace(
            name="broken",
            capabilities=BackendCapabilities(),
            start=lambda: (_ for _ in ()).throw(ValueError("broken start")),
            stop=lambda: None,
        )
        monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: backend)
        for _ in range(2):
            with pytest.raises(ValueError, match="broken start"):
                Session(notebooks=False, spark=False).start()

    def test_trace_result_is_detached(self, tmp_path):
        """Verify trace result is detached.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        session, _ = execute(tmp_path, "x = 1\n")
        raw = session._backend.result()
        raw.lines.clear()
        assert session._backend.result().lines

    def test_trace_stop_is_idempotent(self):
        """Verify trace stop is idempotent.

        Use explicit collector choices and controlled project source to inspect
        configuration, attribution, and lifecycle state.

        """
        backend = TraceBackend(accepts=lambda _: False, on_source=lambda _: None)
        backend.start()
        with pytest.raises(RuntimeError, match="already running"):
            backend.start()
        backend.stop()
        backend.stop()
