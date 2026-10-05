"""LineScope.

Author: Mavs
Description: Real IPython execution checks with the deterministic trace backend.

"""

from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from linescope.api import Session
from linescope.notebooks.ipython import (
    NotebookIntegration,
    load_ipython_extension,
    unload_ipython_extension,
)
from linescope.notebooks.remote import INTERNAL_CELL
from tests.test_render import ReportDOM


@pytest.fixture
def ipython_shell():
    """Provide an isolated IPython shell and restore its global instance.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    """
    ipython = pytest.importorskip("IPython.core.interactiveshell")
    shell = ipython.InteractiveShell.instance()
    previous = dict(shell.user_ns)
    yield shell
    running = shell.user_ns.get("session")
    if isinstance(running, Session) and running.state == "running":
        running.stop()
    unload_ipython_extension(shell)
    shell.user_ns.clear()
    shell.user_ns.update(previous)


def test_multi_cell_session_captures_sources_hits_and_one_final_display(
    ipython_shell,
    monkeypatch,
):
    """Verify multi cell session captures sources hits and one final display.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    """
    shown = []
    monkeypatch.setattr(Session, "show", lambda self: shown.append(self))
    result = ipython_shell.run_cell(
        (
            "from linescope import profile\nsession = profile(backend='trace', spark=False)\n"
            "session.start()"
        ),
        store_history=True,
    )
    assert result.error_in_exec is None
    ipython_shell.run_cell("import time\nanswer = 6 * 7\ntime.sleep(0.002)", store_history=True)
    ipython_shell.run_cell("answer += 1", store_history=True)
    assert shown == []
    ipython_shell.run_cell("session.stop()", store_history=True)
    session = ipython_shell.user_ns["session"]
    assert shown == [session]
    cells = [unit for unit in session.result.sources.values() if unit.kind == "notebook"]
    assert any("answer = 6 * 7" in unit.source for unit in cells)
    source = next(unit for unit in cells if "answer = 6 * 7" in unit.source)
    measured = [
        line for line in session.result.root_run.lines if line.location.source_id == source.id
    ]
    assert any(line.hits and line.location.line == 2 for line in measured)
    assert any(line.wall_time_ns and line.location.line == 3 for line in measured)
    assert not any(
        getattr(callback, "__self__", None).__class__.__name__ == "NotebookIntegration"
        for callback in ipython_shell.events.callbacks["pre_run_cell"]
    )


def test_real_cell_magic_profiles_full_source_and_preserves_namespace(ipython_shell, monkeypatch):
    """Verify real cell magic profiles full source and preserves namespace.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    """
    shown = []
    monkeypatch.setattr(Session, "show", lambda self: shown.append(self))
    load_ipython_extension(ipython_shell)
    result = ipython_shell.run_cell(
        (
            "%%profile --backend trace\n# complete source\nmagic_answer = sum(range(5))\n"
            "magic_answer += 1"
        ),
        store_history=True,
    )
    assert result.error_in_exec is None
    assert ipython_shell.user_ns["magic_answer"] == 11
    assert len(shown) == 1
    session = shown[0]
    sources = [
        unit
        for unit in session.result.sources.values()
        if unit.source.startswith("# complete source")
    ]
    assert len(sources) == 1
    assert any(
        line.location.source_id == sources[0].id and line.location.line == 2 and line.hits
        for line in session.result.root_run.lines
    )


def test_cell_magic_failed_workload_still_restores_session(ipython_shell, monkeypatch):
    """Verify cell magic failed workload still restores session.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    """
    shown = []
    monkeypatch.setattr(Session, "show", lambda self: shown.append(self))
    load_ipython_extension(ipython_shell)
    ipython_shell.run_cell(
        "%%profile --backend trace\nraise ValueError('intentional')", store_history=True
    )
    assert len(shown) == 1
    assert shown[0].state == "stopped"
    assert shown[0].result.root_run.status == "failed"
    # A new session verifies that the previous failed cell released resources.
    with Session(backend="trace", display="none", spark=False):
        pass


def test_previous_cell_function_and_method_get_measurements_and_navigation(ipython_shell):
    """Verify previous cell function and method get measurements and navigation.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    """
    definition = (
        "def earlier(value):\n"
        "    return value + 1\n"
        "\n"
        "class PreviousProcessor:\n"
        "    def transform(self, value):\n"
        "        return value * 2\n"
    )
    ipython_shell.run_cell(definition, store_history=True)
    body = (
        "from linescope import profile\n"
        "with profile(backend='trace', spark=False, display='none') as session:\n"
        "    processor = PreviousProcessor()\n"
        "    result = earlier(processor.transform(20))\n"
    )
    result = ipython_shell.run_cell(body, store_history=True)
    assert result.error_in_exec is None
    session = ipython_shell.user_ns["session"]
    source = next(unit for unit in session.result.sources.values() if unit.source == definition)
    lines = [
        line for line in session.result.root_run.lines if line.location.source_id == source.id
    ]
    assert any(line.location.line == 2 and line.hits == 1 for line in lines)
    assert any(line.location.line == 6 and line.hits == 1 for line in lines)
    links = [call for line in session.result.root_run.lines for call in line.calls]
    assert {"earlier", "PreviousProcessor", "transform"} <= {call.name for call in links}
    assert all(
        call.target.source_id == source.id
        for call in links
        if call.name in {"earlier", "PreviousProcessor", "transform"}
    )


@pytest.fixture
def compact_outputs(monkeypatch):
    """Capture compact HTML and its detached measurements before display.

    Fail if compact mode attempts to show the full cumulative report.

    """
    from linescope.render.cell import render_cell_summary

    fragments, snapshots = [], []

    def render(result, **options):
        """Retain the detached result and render its ordinary compact output.

        Keep assertions independent of display timing and formatting.

        """
        snapshots.append(deepcopy(result))
        return render_cell_summary(result, **options)

    def reject_full_report(self):
        """Reject unexpected automatic full report display in compact mode.

        A compact session displays the full report only on explicit request.

        """
        del self
        pytest.fail("Compact cell mode displayed a full report")

    monkeypatch.setattr("linescope.render.cell.render_cell_summary", render)
    monkeypatch.setattr("IPython.display.display", lambda value: fragments.append(value.data))
    monkeypatch.setattr(Session, "show", reject_full_report)
    return fragments, snapshots


def test_compact_cells_include_called_definitions_without_previous_cell_costs(
    ipython_shell, compact_outputs, tmp_path
):
    """Isolate each execution while preserving cumulative report measurements.

    Reexecuting the same frontend cell must retain only its current hits, and
    called functions defined before profiling remain visible.

    """
    fragments, snapshots = compact_outputs
    definition = "def compact_helper():\n    return 42\n"
    ipython_shell.run_cell(definition, store_history=True)
    started = ipython_shell.run_cell(
        "from linescope import profile\n"
        "session = profile.start(backend='trace', spark=False, display='cell-summary')",
        store_history=True,
    )
    assert started.error_in_exec is None
    fragments.clear()
    snapshots.clear()
    session = ipython_shell.user_ns["session"]
    helper = next(
        source for source in session.registry.sources.values() if source.source == definition
    )
    workload = "for _ in range(3):\n    compact_helper()"
    for _ in range(2):
        result = ipython_shell.run_cell(workload, store_history=True, cell_id="repeated")
        assert result.error_in_exec is None
        line = next(
            line for line in snapshots[-1].root_run.lines if line.location.source_id == helper.id
        )
        assert line.hits == 3
    assert len(fragments) == 2
    unrelated = ipython_shell.run_cell("unrelated_cell_value = 1", store_history=True)
    assert unrelated.error_in_exec is None
    assert all(line.location.source_id != helper.id for line in snapshots[-1].root_run.lines)
    assert all(not ReportDOM(fragment).root.find_all("iframe") for fragment in fragments)
    count = len(fragments)
    stopped = ipython_shell.run_cell("profile.stop()", store_history=True)
    assert stopped.error_in_exec is None
    assert len(fragments) == count
    cumulative = next(
        line for line in session.result.root_run.lines if line.location.source_id == helper.id
    )
    assert cumulative.hits == 6
    assert session.save(tmp_path / "full.html").is_file()


@pytest.mark.parametrize("cell", ["raise ValueError('expected')", "broken = ("])
def test_failed_compact_cell_allows_debugging_to_continue_and_restores_hooks(
    ipython_shell, compact_outputs, cell
):
    """Show failure results and keep collection usable until explicit stop.

    Handle both execution and syntax failures without leaking trace or notebook
    hooks into the next session.

    """
    fragments, snapshots = compact_outputs
    previous_trace = sys.gettrace()
    original_cache = ipython_shell.compile.cache
    ipython_shell.run_cell(
        "from linescope import profile\n"
        "session = profile.start(backend='trace', spark=False, display='cell-summary')",
        store_history=True,
    )
    fragments.clear()
    snapshots.clear()
    result = ipython_shell.run_cell(cell, store_history=True)
    assert result.error_in_exec or result.error_before_exec
    assert snapshots[-1].root_run.status == "failed"
    assert "Failed" in ReportDOM(fragments[-1]).root.text()
    result = ipython_shell.run_cell("recovered_answer = 42", store_history=True)
    assert result.error_in_exec is None
    assert snapshots[-1].root_run.status == "success"
    session = ipython_shell.user_ns["session"]
    session.stop()
    assert sys.gettrace() is previous_trace
    assert ipython_shell.compile.cache == original_cache
    assert not any(
        getattr(callback, "__self__", None).__class__.__name__ == "NotebookIntegration"
        for callback in ipython_shell.events.callbacks["post_run_cell"]
    )
    with Session(backend="trace", display="none", spark=False):
        pass


def test_compact_display_failure_is_diagnostic_and_next_cell_recovers(
    ipython_shell, compact_outputs, monkeypatch
):
    """Recover from optional display errors without interrupting collection.

    Keep the failure's type in diagnostics and allow the next ordinary cell to
    produce its own summary.

    """
    fragments, snapshots = compact_outputs
    ipython_shell.run_cell(
        "from linescope import profile\n"
        "session = profile.start(backend='trace', spark=False, display='cell-summary')",
        store_history=True,
    )
    session = ipython_shell.user_ns["session"]
    fragments.clear()

    def fail_display(_value):
        """Simulate an unavailable notebook output channel.

        Include sensitive error text to verify it does not enter diagnostics.

        """
        raise RuntimeError("private display error")

    with monkeypatch.context() as patch:
        patch.setattr("IPython.display.display", fail_display)
        result = ipython_shell.run_cell("display_failure_value = 1", store_history=True)
        assert result.error_in_exec is None
    assert session.state == "running"
    assert any(
        "summary unavailable (RuntimeError)" in warning for warning in session.result.warnings
    )
    assert all("private display error" not in warning for warning in session.result.warnings)
    result = ipython_shell.run_cell("display_recovered_value = 2", store_history=True)
    assert result.error_in_exec is None
    assert len(fragments) == 1
    assert snapshots[-1].root_run.status == "success"
    session.stop()


def test_cell_magic_can_request_one_compact_summary(ipython_shell, compact_outputs):
    """Support compact output for a single profiled magic cell.

    Display one body summary and finalize collection without a full iframe.

    """
    fragments, snapshots = compact_outputs
    load_ipython_extension(ipython_shell)
    result = ipython_shell.run_cell(
        "%%profile --backend trace --display cell-summary\ncompact_magic_answer = 42",
        store_history=True,
    )
    assert result.error_in_exec is None
    assert len(fragments) == len(snapshots) == 1
    assert ipython_shell.user_ns["compact_magic_answer"] == 42
    assert "compact_magic_answer = 42" in ReportDOM(fragments[0]).root.text()
    with Session(backend="trace", display="none", spark=False):
        pass


def test_compact_elapsed_uses_cell_boundaries_and_skips_internal_cells(
    compact_outputs, monkeypatch
):
    """Exclude idle and display time using controlled cell boundary clocks.

    Internal child-profiler cells must not generate user-facing summaries.

    """
    fragments, snapshots = compact_outputs
    session = Session(backend="trace", display="cell-summary", spark=False)
    adapter = NotebookIntegration(session, SimpleNamespace(execution_count=12))
    adapter._active = True
    now = iter([1_000_000_000, 1_010_000_000, 50_000_000_000, 50_002_000_000])
    monkeypatch.setattr("linescope.notebooks.ipython.perf_counter_ns", lambda: next(now))
    adapter._pre_run_cell(SimpleNamespace(raw_cell="answer = 1"))
    adapter._post_run_cell(SimpleNamespace())
    adapter._pre_run_cell(SimpleNamespace(raw_cell="answer = 2"))
    adapter._post_run_cell(SimpleNamespace())
    assert [snapshot.root_run.elapsed_ns for snapshot in snapshots] == [10_000_000, 2_000_000]
    adapter._pre_run_cell(SimpleNamespace(raw_cell=INTERNAL_CELL + "\npass"))
    adapter._post_run_cell(SimpleNamespace())
    assert len(fragments) == 2


def test_compact_baseline_failure_skips_cumulative_output_and_recovers(
    ipython_shell, compact_outputs, monkeypatch
):
    """Avoid showing session totals as cell costs after a baseline failure.

    A subsequent successful baseline must restore ordinary cell summaries.

    """
    fragments, snapshots = compact_outputs
    ipython_shell.run_cell(
        "from linescope import profile\n"
        "session = profile.start(backend='trace', spark=False, display='cell-summary')",
        store_history=True,
    )
    session = ipython_shell.user_ns["session"]
    fragments.clear()
    snapshots.clear()

    def fail_refresh():
        """Simulate a temporarily unavailable collector snapshot.

        Keep failure details out of notebook diagnostics.

        """
        raise ValueError("private collector state")

    with monkeypatch.context() as patch:
        patch.setattr(session, "_refresh", fail_refresh)
        result = ipython_shell.run_cell("baseline_failure_value = 1", store_history=True)
        assert result.error_in_exec is None
    assert fragments == snapshots == []
    assert any(
        "baseline unavailable (ValueError)" in warning for warning in session.result.warnings
    )
    assert all("private collector state" not in warning for warning in session.result.warnings)
    result = ipython_shell.run_cell("baseline_recovered_value = 2", store_history=True)
    assert result.error_in_exec is None
    assert len(fragments) == len(snapshots) == 1
    measured_sources = {
        snapshots[0].sources[line.location.source_id].source
        for line in snapshots[0].root_run.lines
    }
    assert "baseline_failure_value = 1" not in measured_sources
    session.stop()
