"""LineScope.

Author: Mavs
Description: Real IPython execution checks with the deterministic trace backend.

"""

import pytest

from linescope.api import Session
from linescope.notebooks.ipython import load_ipython_extension, unload_ipython_extension


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
