"""LineScope.

Author: Mavs
Description: Verify retained Python memory attribution and tracer ownership.

"""

import sys
import threading
import tracemalloc

from click.testing import CliRunner
import pytest

from linescope import Backend, Session, cli
from linescope.backends.trace import TraceBackend
from tests.test_core import execute
from tests.test_render import ReportDOM


@pytest.fixture(autouse=True)
def isolated_memory_tracing(monkeypatch):
    """Keep tests independent of process configuration and memory tracing.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    monkeypatch.setattr("linescope.config._overrides", {})
    if tracemalloc.is_tracing():
        pytest.skip("Requires ownership of a fresh tracemalloc session")
    yield
    if tracemalloc.is_tracing():
        tracemalloc.stop()


def test_retained_allocations_transients_and_unknown_peaks(tmp_path):
    """Keep retained bytes separate from transient allocations and peaks.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    session, scope = execute(
        tmp_path,
        "retained = bytearray(100_000)\n"
        "temporary = bytearray(200_000)\n"
        "del temporary\n"
        "unchanged = 1\n"
        "if False:\n"
        "    never = bytearray(400_000)\n",
        memory=True,
    )
    assert len(scope["retained"]) == 100_000
    assert session.result.capabilities.memory is True
    rows = {line.location.line: line for line in session.result.root_run.lines}
    assert rows[1].memory.delta_bytes >= 100_000
    assert rows[2].memory.delta_bytes == 0
    assert rows[3].memory.delta_bytes == 0
    assert rows[4].memory.delta_bytes == 0
    assert 6 not in rows
    assert all(line.memory.peak_bytes is None for line in rows.values())
    assert all(line.samples is None for line in rows.values())
    assert not tracemalloc.is_tracing()
    assert "Python allocation Δ" in ReportDOM(session.html()).root.text()
    assert any("net retained Python allocations" in warning for warning in session.result.warnings)


def test_abandoned_generator_allocations_are_released_before_final_snapshot(tmp_path):
    """Exclude allocations retained only by profiler cleanup references.

    A suspended frame can retain local objects after its generator is deleted.
    Final measurements must release the profiler's frame references first.

    """
    session, _ = execute(
        tmp_path,
        "def work():\n"
        "    temporary = bytearray(100_000)\n"
        "    yield None\n"
        "generator = work()\n"
        "next(generator)\n"
        "del generator\n",
        memory=True,
    )
    row = next(line for line in session.result.root_run.lines if line.location.line == 2)
    assert row.memory.delta_bytes == 0


def test_library_memory_stays_on_nearest_project_line(tmp_path):
    """Attribute library allocations to the nearest project caller once.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """

    def external():
        """Allocate retained bytes outside the profiling scope.

        Expose a workload outside accepted source so the nearest project caller
        receives its cost.

        """
        return bytearray(100_000)

    session, _ = execute(
        tmp_path,
        "def child():\n    return external()\nvalue = child()\n",
        namespace={"external": external},
        memory=True,
    )
    rows = {line.location.line: line for line in session.result.root_run.lines}
    assert rows[2].memory.delta_bytes >= 100_000
    assert rows[3].memory.delta_bytes < 10_000
    assert len(session.result.sources) == 1


def test_existing_tracer_preserves_depth_history_peak_and_freed_allocation_sites(
    tmp_path,
    monkeypatch,
):
    """Preserve caller tracing and charge frees to their allocation sites.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    tracemalloc.start(7)
    marker = bytearray(256)
    marker_traceback = tracemalloc.get_object_traceback(marker)
    initial = tmp_path / "initial.py"
    initial.write_text("old = bytearray(150_000)\n", encoding="utf-8")
    scope = {}
    exec(compile(initial.read_text(), str(initial), "exec"), scope)
    previous_peak = tracemalloc.get_traced_memory()[1]

    def forbidden_reset():
        """Reject attempts to clear caller-owned history.

        Inspect allocation tracer ownership and detached memory results while
        preserving existing trace hooks.

        """
        pytest.fail("Caller-owned memory history must not be reset")

    monkeypatch.setattr(tracemalloc, "reset_peak", forbidden_reset)
    monkeypatch.setattr(tracemalloc, "clear_traces", forbidden_reset)

    def rewrite_source():
        """Edit the allocation source after its baseline is captured.

        Preserve the report's original source even when no allocation line
        executes again during the session.

        """
        initial.write_text("# replaced after allocation\n", encoding="utf-8")

    scope["rewrite_source"] = rewrite_source
    session, _ = execute(
        tmp_path,
        "rewrite_source()\ndel old\nnew = bytearray(100_000)\n",
        namespace=scope,
        memory=True,
    )
    assert tracemalloc.is_tracing()
    assert tracemalloc.get_traceback_limit() == 7
    assert tracemalloc.get_object_traceback(marker) == marker_traceback
    assert tracemalloc.get_traced_memory()[1] >= previous_peak
    rows = {
        (session.result.sources[line.location.source_id].path, line.location.line): line
        for line in session.result.root_run.lines
    }
    released = rows[(initial.as_posix(), 1)]
    assert released.memory.delta_bytes <= -150_000
    assert released.memory.peak_bytes is None
    assert released.hits is None
    assert released.wall_time_ns is None
    assert (
        session.result.sources[released.location.source_id].source == "old = bytearray(150_000)\n"
    )


def test_live_snapshots_do_not_accumulate_or_mutate_previous_results(tmp_path):
    """Keep live memory snapshots detached and relative to the same baseline.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    path = tmp_path / "workload.py"
    path.write_text("value = bytearray(100_000)\n", encoding="utf-8")
    scope = {}
    with Session(
        memory=True, root=str(tmp_path), display="none", notebooks=False, spark=False
    ) as session:
        exec(compile(path.read_text(), str(path), "exec"), scope)
        first = session._memory._tracer.result()
        initial = next(line.memory.delta_bytes for line in first.lines if line.line == 1)
        session._refresh()
        session._refresh()
        assert session.result.root_run.lines[0].memory.delta_bytes == initial
        scope.clear()
        latest = session._memory._tracer.result()
        assert next(line.memory.delta_bytes for line in latest.lines if line.line == 1) == 0
        assert next(line.memory.delta_bytes for line in first.lines if line.line == 1) == initial
    assert session.result.root_run.lines[0].memory.delta_bytes == 0


def test_memory_disabled_never_touches_tracemalloc(tmp_path, monkeypatch):
    """Leave memory instrumentation untouched when collection is disabled.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """

    def forbidden(*_args):
        """Reject unexpected memory instrumentation.

        Fail immediately when code invokes an operation the case expects to
        avoid.

        """
        pytest.fail("Memory collection is opt-in")

    for name in ("start", "stop", "take_snapshot"):
        monkeypatch.setattr(tracemalloc, name, forbidden)
    session, _ = execute(tmp_path, "value = bytearray(1000)\n")
    assert all(line.memory is None for line in session.result.root_run.lines)


@pytest.mark.parametrize("existing", [False, True])
def test_workload_failure_restores_trace_and_memory_ownership(tmp_path, existing):
    """Restore instrumentation ownership after the workload raises.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    if existing:
        tracemalloc.start(3)
    prior = sys.gettrace()
    path = tmp_path / "workload.py"
    path.write_text("value = bytearray(100_000)\nraise ValueError('workload')\n", encoding="utf-8")
    scope = {}
    session = Session(
        memory=True, root=str(tmp_path), display="none", notebooks=False, spark=False
    )
    with pytest.raises(ValueError, match="workload"), session:
        exec(compile(path.read_text(), str(path), "exec"), scope)
    assert sys.gettrace() is prior
    assert tracemalloc.is_tracing() is existing
    assert session.result.root_run.status == "failed"
    assert session.result.root_run.lines[0].memory.delta_bytes >= 100_000
    execute(tmp_path, "value = 1\n")


def test_startup_failure_releases_memory_and_trace_hooks():
    """Release partially installed instrumentation when startup fails.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    prior = sys.gettrace()

    def fail(_filename):
        """Fail while capturing an active source frame.

        Keep the original exception observable so cleanup cannot silently
        replace it.

        """
        raise ValueError("source snapshot")

    collector = TraceBackend(accepts=lambda _: True, on_source=fail, memory=True)
    with pytest.raises(ValueError, match="source snapshot"):
        collector.start()
    assert sys.gettrace() is prior
    assert not tracemalloc.is_tracing()
    collector.stop()


def test_final_snapshot_failure_still_releases_owned_tracer(monkeypatch):
    """Release instrumentation even when final memory collection fails.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    prior = sys.gettrace()
    collector = TraceBackend(accepts=lambda _: False, on_source=lambda _: None, memory=True)
    collector.start()

    def fail():
        """Fail during final memory collection.

        Keep the original exception observable so cleanup cannot silently
        replace it.

        """
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(collector, "_memory_totals", fail)
    with pytest.raises(RuntimeError, match="snapshot failed"):
        collector.stop()
    assert sys.gettrace() is prior
    assert not tracemalloc.is_tracing()
    collector.stop()


@pytest.mark.parametrize("existing", [False, True])
def test_session_final_snapshot_failure_releases_both_trace_collectors(
    tmp_path,
    monkeypatch,
    existing,
):
    """Restore both tracing layers when deferred allocation collection fails.

    Keep the session lock reusable and preserve caller-owned memory tracing.

    """
    if existing:
        tracemalloc.start(3)
    previous = sys.gettrace()
    session = Session(
        memory=True, root=str(tmp_path), display="none", notebooks=False, spark=False
    ).start()

    def fail():
        """Fail after both collectors release their trace hooks.

        Preserve the error so assertions can inspect instrumentation cleanup.

        """
        raise RuntimeError("final allocation snapshot")

    monkeypatch.setattr(session._memory._tracer, "_memory_totals", fail)
    with pytest.raises(RuntimeError, match="final allocation snapshot"):
        session.stop()
    assert sys.gettrace() is previous
    assert tracemalloc.is_tracing() is existing
    assert not session._backend._running
    assert not session._memory._tracer._running
    assert not any(thread.name == "linescope-ram" for thread in threading.enumerate())
    execute(tmp_path, "value = 1\n", memory=True)
    assert tracemalloc.is_tracing() is existing


def test_externally_stopped_memory_is_unknown(tmp_path):
    """Keep memory unknown when the workload stops the allocation tracer.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    session, _ = execute(
        tmp_path,
        "value = bytearray(1000)\ntracemalloc.stop()\n",
        namespace={"tracemalloc": tracemalloc},
        memory=True,
    )
    assert all(line.memory is None for line in session.result.root_run.lines)
    assert any("memory is unavailable" in warning for warning in session.result.warnings)


def test_wrong_thread_cannot_release_owned_memory(tmp_path):
    """Keep the owning thread in control of memory instrumentation.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    session = Session(
        memory=True, root=str(tmp_path), display="none", notebooks=False, spark=False
    ).start()
    failures = []

    def stop():
        """Attempt cleanup from an unsupported thread.

        Retain the result or exception for lifecycle and ownership assertions.

        """
        try:
            session.stop()
        except RuntimeError as error:
            failures.append(str(error))

    try:
        worker = threading.Thread(target=stop)
        worker.start()
        worker.join()
        assert failures
        assert "thread" in failures[0]
        assert tracemalloc.is_tracing()
    finally:
        session.stop()
    assert not tracemalloc.is_tracing()


def test_default_cli_memory_needs_no_scalene_bootstrap(tmp_path, monkeypatch):
    """Collect memory through the default CLI without native preloading.

    Inspect allocation tracer ownership and detached memory results while
    preserving existing trace hooks.

    """
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "workload.py"
    script.write_text("value = bytearray(100_000)\n", encoding="utf-8")
    report = tmp_path / "report.html"

    def forbidden():
        """Reject unexpected native allocator setup.

        Fail immediately when code invokes an operation the case expects to
        avoid.

        """
        pytest.fail("Trace memory must not require native Scalene preloading")

    monkeypatch.setattr("linescope.backends.scalene.memory_preload_environment", forbidden)
    result = CliRunner().invoke(
        cli.main,
        [
            "--memory",
            "--no-spark",
            "--no-notebooks",
            "--display",
            "none",
            "--root",
            str(tmp_path),
            "-o",
            str(report),
            str(script),
        ],
    )
    assert result.exit_code == 0, result.output
    document = ReportDOM(report.read_text(encoding="utf-8"))
    assert "Python allocation Δ" in document.root.text()
    assert any(
        row.attributes["data-memory"] != ""
        for row in document.root.find_all("tr", css="source-row")
    )
    assert any(
        int(row.attributes["data-allocation"] or 0) >= 100_000
        for row in document.root.find_all("tr", css="source-row")
    )
    assert not tracemalloc.is_tracing()
    assert Session().config.backend is Backend.TRACE


@pytest.mark.parametrize("backend", list(Backend))
def test_shared_allocation_and_ram_collection_is_independent_of_timing_backend(
    tmp_path,
    monkeypatch,
    backend,
):
    """Keep allocation and RAM measurements available with each timing backend.

    Use a controlled timing collector to keep sampling platform-independent.

    """
    from types import SimpleNamespace

    from linescope.backends.base import RawBackendResult
    from linescope.model import BackendCapabilities

    options = []
    raw = RawBackendResult()

    def timing(name, **kwargs):
        """Provide a timing collector without native memory instrumentation.

        Record factory arguments to verify memory ownership is shared.

        """
        options.append(kwargs)
        return SimpleNamespace(
            name=name,
            capabilities=BackendCapabilities(sampled=name is not Backend.TRACE),
            start=lambda: None,
            stop=lambda: None,
            result=lambda: raw,
        )

    monkeypatch.setattr("linescope.api.create_backend", timing)
    path = tmp_path / "workload.py"
    path.write_text("value = bytearray(100_000)\n", encoding="utf-8")
    scope = {}
    with Session(
        backend=backend,
        memory=True,
        root=str(tmp_path),
        display="none",
        notebooks=False,
        spark=False,
    ) as session:
        exec(compile(path.read_text(), str(path), "exec"), scope)
        session._refresh()
        allocated = session.result.root_run.lines[0].memory.delta_bytes
        session._refresh()
        assert session.result.root_run.lines[0].memory.delta_bytes == allocated
        assert raw.lines == []
        assert raw.warnings == []
    row = next(line for line in session.result.root_run.lines if line.location.line == 1)
    assert options[0]["memory"] is False
    assert row.memory.delta_bytes >= 100_000
    assert row.memory.peak_bytes is None
    assert row.ram.rss_bytes > 0
    assert session.result.root_run.memory_samples
    assert not tracemalloc.is_tracing()


def test_worker_allocations_do_not_fabricate_worker_timing_or_hits(tmp_path):
    """Measure retained worker memory while keeping worker timing unknown.

    Memory snapshots cover the process; execution tracing covers its owner.

    """
    session, _ = execute(
        tmp_path,
        "def work():\n"
        "    retained.append(bytearray(100_000))\n"
        "worker = threading.Thread(target=work)\n"
        "worker.start()\n"
        "worker.join()\n",
        namespace={"threading": threading, "retained": []},
        memory=True,
    )
    row = next(line for line in session.result.root_run.lines if line.location.line == 2)
    assert row.memory.delta_bytes >= 100_000
    assert row.hits is None
    assert row.wall_time_ns is None
