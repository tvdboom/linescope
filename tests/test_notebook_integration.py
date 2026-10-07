"""LineScope.

Author: Mavs
Description: Real IPython execution checks with the deterministic trace backend.

"""

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from linescope.api import ProfileController, Session
from linescope.notebooks.ipython import (
    NotebookIntegration,
    load_ipython_extension,
    unload_ipython_extension,
)
from linescope.notebooks.remote import INTERNAL_CELL
from linescope.render import render_html
from tests.test_render import ReportDOM, source_rows


@pytest.fixture
def ipython_shell(monkeypatch):
    """Provide an isolated IPython shell and restore its global instance.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation. Supply the kernel attribute
    present in real notebook shells so automatic display stays inline.

    """
    ipython = pytest.importorskip("IPython.core.interactiveshell")
    shell = ipython.InteractiveShell.instance()
    monkeypatch.setattr(shell, "kernel", object(), raising=False)
    previous = dict(shell.user_ns)
    yield shell
    running = shell.user_ns.get("session")
    if isinstance(running, Session) and running.state == "running":
        running.stop()
    unload_ipython_extension(shell)
    shell.user_ns.clear()
    shell.user_ns.update(previous)


@pytest.mark.parametrize("metadata", ["__vsc_ipynb_file__", "__session__", "JPY_SESSION_NAME"])
def test_multi_cell_session_captures_sources_hits_and_one_final_display(
    ipython_shell,
    monkeypatch,
    metadata: str,
):
    """Verify multi cell session captures sources hits and one final display.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    Parameters
    ----------
    ipython_shell : [InteractiveShell]
        Isolated shell with execution history and reversible notebook hooks.

    monkeypatch : pytest.MonkeyPatch
        Owned frontend metadata and display replacements.

    metadata : str
        VS Code namespace key or Jupyter kernel metadata supplying the path.

    """
    path = "/project/example.ipynb"
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    for name in ("__vsc_ipynb_file__", "__session__"):
        monkeypatch.delitem(ipython_shell.user_ns, name, raising=False)
    if metadata == "JPY_SESSION_NAME":
        monkeypatch.setenv(metadata, path)
    else:
        monkeypatch.setitem(ipython_shell.user_ns, metadata, path)
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
    assert all(unit.path.startswith(f"{path} · cell ") for unit in cells)
    document = ReportDOM(session.html()).root
    notebook_rows = document.find_all("section", id="files")[0].find_all(
        "tr", **{"data-kind": "Notebook"}
    )
    assert len(notebook_rows) == 1
    notebook_row = notebook_rows[0]
    assert notebook_row.find_all("a")[0].text() == "example.ipynb"
    assert (
        document.find_all("section", css="source-page")[0].find_all("h1")[0].text()
        == "example.ipynb"
    )
    measured = [
        line for line in session.result.root_run.lines if line.location.source_id == source.id
    ]
    assert any(line.hits and line.location.line == 2 for line in measured)
    assert any(line.wall_time_ns and line.location.line == 3 for line in measured)
    assert not any(
        getattr(callback, "__self__", None).__class__.__name__ == "NotebookIntegration"
        for callback in ipython_shell.events.callbacks["pre_run_cell"]
    )


@pytest.mark.parametrize("stop_expression", ["profile.stop()", "session.stop()"])
@pytest.mark.parametrize("display", ["end", "none"])
def test_stop_displays_only_html_and_retains_result(
    ipython_shell, monkeypatch, stop_expression, display
):
    """Suppress expression output when stopping a notebook-wide session.

    Display the configured report once, retain programmatic result access,
    and release notebook callbacks before repeated stop calls.

    Parameters
    ----------
    ipython_shell : [InteractiveShell]
        Isolated notebook shell owning execution history and hooks.

    monkeypatch : pytest.MonkeyPatch
        Reversible controller and display replacements.

    stop_expression : str
        Controller or session call executed as the cell's final expression.

    display : str
        Display mode controlling whether the stop call emits a report.

    """
    from IPython.display import IFrame

    controller = ProfileController()
    monkeypatch.setattr("linescope.profile", controller)
    displayed = []
    monkeypatch.setattr("IPython.display.display", displayed.append)
    previous_trace = sys.gettrace()
    started = ipython_shell.run_cell(
        "from linescope import profile\n"
        f"session = profile.start(backend='trace', spark=False, display='{display}')",
        store_history=True,
    )
    assert started.error_in_exec is None
    ipython_shell.run_cell("stop_answer = 42", store_history=True)
    stopped = ipython_shell.run_cell(stop_expression, store_history=True)
    session = ipython_shell.user_ns["session"]
    assert stopped.error_in_exec is None
    assert stopped.result is None
    assert len(displayed) == (1 if display == "end" else 0)
    if displayed:
        assert isinstance(displayed[0], IFrame)
    assert controller.result is session.result
    assert controller.result.root_run.elapsed_ns is not None
    source = next(
        unit for unit in controller.result.sources.values() if unit.source == "stop_answer = 42"
    )
    assert any(
        line.location.source_id == source.id and line.hits == 1
        for line in controller.result.root_run.lines
    )
    assert sys.gettrace() is previous_trace
    assert not any(
        isinstance(getattr(callback, "__self__", None), NotebookIntegration)
        for callback in ipython_shell.events.callbacks["post_run_cell"]
    )
    repeated = ipython_shell.run_cell(stop_expression, store_history=True)
    assert repeated.error_in_exec is None
    assert repeated.result is None
    assert len(displayed) == (1 if display == "end" else 0)


@pytest.mark.parametrize(
    "show_expression", ["session.show(inline=True)", "profile.show(inline=True)"]
)
def test_show_displays_only_html_without_expression_output(
    ipython_shell, monkeypatch, show_expression
):
    """Display a report without echoing its HTML string in a notebook cell.

    Execute direct session and controller calls as final expressions and
    retain separate access to the result and standalone HTML.

    Parameters
    ----------
    ipython_shell : [InteractiveShell]
        Isolated notebook shell owning execution history and hooks.

    monkeypatch : pytest.MonkeyPatch
        Reversible controller and display replacements.

    show_expression : str
        Session or controller call displaying the report inline.

    """
    from IPython.display import IFrame

    controller = ProfileController()
    monkeypatch.setattr("linescope.profile", controller)
    displayed = []
    monkeypatch.setattr("IPython.display.display", displayed.append)
    started = ipython_shell.run_cell(
        "from linescope import profile\n"
        "session = profile.start(backend='trace', spark=False, display='none')",
        store_history=True,
    )
    assert started.error_in_exec is None
    ipython_shell.run_cell("show_answer = 42", store_history=True)
    ipython_shell.run_cell("profile.stop()", store_history=True)
    session = ipython_shell.user_ns["session"]
    result = session.result
    assert displayed == []

    shown = ipython_shell.run_cell(show_expression, store_history=True)

    assert shown.error_in_exec is None
    assert shown.result is None
    assert len(displayed) == 1
    assert isinstance(displayed[0], IFrame)
    assert session.result is controller.result is result
    assert "show_answer = 42" in ReportDOM(session.html()).root.text()
    assert len(displayed) == 1


def test_compact_demo_displays_full_report_only_in_final_cell(
    ipython_shell,
    monkeypatch,
    tmp_path,
):
    """Execute the demo with compact cell outputs and one final full overview.

    Use the default Trace collector without allocation tracking for
    deterministic execution. Show source even in the setup cell, expose line
    sorting on Source only, and ensure the final cell emits no result or HTML
    string expression.

    """
    from IPython.display import HTML, IFrame

    path = Path(__file__).resolve().parents[1] / "examples/notebooks/notebook_example.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cells = [
        cell
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and cell["id"].startswith("quickstart-")
    ]
    controller = ProfileController()
    monkeypatch.setattr("linescope.profile", controller)
    monkeypatch.chdir(tmp_path)
    displayed = []
    monkeypatch.setattr("IPython.display.display", displayed.append)
    original_cache = ipython_shell.compile.cache
    previous_trace = sys.gettrace()

    for index, cell in enumerate(cells):
        displayed.clear()
        code = "".join(cell["source"]).replace("memory=True", "memory=False")
        executed = ipython_shell.run_cell(code, store_history=True)
        assert executed.error_before_exec is None
        assert executed.error_in_exec is None
        assert executed.result is None
        assert len(displayed) == 1

        if index < len(cells) - 1:
            assert isinstance(displayed[0], HTML)
            assert "linescope-cell-summary" in displayed[0].data
            summary = ReportDOM(displayed[0].data).root
            assert not summary.find_all("iframe")
            headings = summary.find_all("thead")[0].find_all("th")
            assert headings[0].attributes["aria-label"] == "Line number"
            assert not headings[0].find_all("button")
            assert not headings[0].find_all("svg")
            source = headings[-1]
            assert source.text() == "Source"
            assert source.attributes["aria-sort"] == "ascending"
            control = source.find_all("button")[0]
            assert control.attributes["data-sort"] == "line"
            assert control.attributes["data-sort-type"] == "number"
            assert control.attributes["data-sort-direction"] == "ascending"
            assert control.find_all("svg", css="table-sort-icon")
            if index == 0:
                assert "session = profile.start(" in displayed[0].data
        else:
            assert isinstance(displayed[0], IFrame)

    session = ipython_shell.user_ns["session"]
    assert session.state == "stopped"
    assert session.result.backend == "trace"
    assert session.result.capabilities.hit_counts
    assert ipython_shell.user_ns["result"] is controller.result is session.result
    assert ipython_shell.user_ns["report_path"] == tmp_path / "notebook.html"
    saved = ReportDOM((tmp_path / "notebook.html").read_text(encoding="utf-8")).root
    assert "def tokenize" in saved.text()
    assert sys.gettrace() is previous_trace
    assert ipython_shell.compile.cache == original_cache


def test_quick_start_end_mode_displays_only_when_collection_stops(
    ipython_shell,
    monkeypatch,
    tmp_path,
):
    """Display one complete report after the quick start's end-mode workload.

    Execute the actual notebook cells with Trace and check that intermediate
    cells emit no profiler display. Restore tracing and notebook hooks after
    the final cell and retain the collected source in the saved report.

    """
    from IPython.display import IFrame

    notebook = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples/notebooks/notebook_example.ipynb"
        ).read_text(encoding="utf-8")
    )
    cells = [
        cell
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and cell["id"].startswith("end-")
    ]
    controller = ProfileController()
    monkeypatch.setattr("linescope.profile", controller)
    monkeypatch.chdir(tmp_path)
    displayed = []
    monkeypatch.setattr("IPython.display.display", displayed.append)
    original_cache = ipython_shell.compile.cache
    previous_trace = sys.gettrace()

    for index, cell in enumerate(cells):
        displayed.clear()
        code = "".join(cell["source"]).replace("memory=True", "memory=False")
        executed = ipython_shell.run_cell(code, store_history=True)
        assert executed.error_before_exec is None
        assert executed.error_in_exec is None
        assert executed.result is None
        if index < len(cells) - 1:
            assert displayed == []
        else:
            assert len(displayed) == 1
            assert isinstance(displayed[0], IFrame)

    session = ipython_shell.user_ns["end_session"]
    assert session.state == "stopped"
    assert ipython_shell.user_ns["end_result"] is controller.result is session.result
    report = ReportDOM((tmp_path / "notebook-end.html").read_text(encoding="utf-8")).root
    assert "mean_square = sum(squared) / len(squared)" in report.text()
    assert sys.gettrace() is previous_trace
    assert ipython_shell.compile.cache == original_cache


@pytest.mark.parametrize("store_history", [False, True])
@pytest.mark.parametrize("magic", ["profile", "linescope"])
@pytest.mark.parametrize("kernel_compiler", [False, True])
def test_real_cell_magic_profiles_full_source_and_preserves_namespace(
    ipython_shell, monkeypatch, magic, *, store_history, kernel_compiler
):
    """Verify real cell magic profiles full source and preserves namespace.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    Parameters
    ----------
    ipython_shell : [InteractiveShell]
        Isolated shell with its execution counter and namespace preserved.

    monkeypatch : pytest.MonkeyPatch
        Owned display and compiler overrides restored after the test.

    magic : str
        Registered profiling magic used to execute the workload.

    store_history : bool
        Whether IPython advances its counter and stores this execution.

    kernel_compiler : bool
        Whether to use Jupyter's filesystem compiler rather than IPython's
        virtual compiler filenames.

    """
    shown = []
    monkeypatch.setattr(Session, "show", lambda self: shown.append(self))
    if kernel_compiler:
        compiler = pytest.importorskip("ipykernel.compiler")
        monkeypatch.setattr(ipython_shell, "compile", compiler.XCachingCompiler())
    load_ipython_extension(ipython_shell)
    execution_count = ipython_shell.execution_count
    original_cache = ipython_shell.compile.cache
    result = ipython_shell.run_cell(
        (
            f"%%{magic} --backend trace\n# complete source\nmagic_answer = sum(range(5))\n"
            "magic_answer += 1"
        ),
        store_history=store_history,
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
    assert sources[0].path == f"interactive · cell {execution_count}"
    assert ipython_shell.execution_count == execution_count + store_history
    assert ipython_shell.compile.cache == original_cache
    assert any(
        line.location.source_id == sources[0].id and line.location.line == 2 and line.hits
        for line in session.result.root_run.lines
    )


@pytest.mark.parametrize("notebooks", [False, True])
@pytest.mark.parametrize("notebook_path", [None, "example.ipynb"])
def test_cell_magic_failed_workload_still_restores_session(
    ipython_shell, monkeypatch, notebook_path, *, notebooks
):
    """Verify cell magic failed workload still restores session.

    Execute cells in an isolated IPython shell and inspect captured snapshots,
    navigation, and restored instrumentation.

    Parameters
    ----------
    ipython_shell : [InteractiveShell]
        Isolated shell used to raise a controlled workload exception.

    monkeypatch : pytest.MonkeyPatch
        Owned session-factory and display overrides restored after the test.

    notebook_path : str | None
        Frontend filename, or None to exercise the interactive fallback.

    notebooks : bool
        Whether the session installs notebook hooks automatically; the magic
        still owns source capture when that setting is disabled.

    """
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    monkeypatch.delitem(ipython_shell.user_ns, "__session__", raising=False)
    monkeypatch.delitem(ipython_shell.user_ns, "__vsc_ipynb_file__", raising=False)
    if notebook_path is not None:
        monkeypatch.setitem(ipython_shell.user_ns, "__vsc_ipynb_file__", notebook_path)
    shown = []
    monkeypatch.setattr(Session, "show", lambda self: shown.append(self))
    monkeypatch.setattr(
        "linescope.profile", lambda **options: Session(notebooks=notebooks, **options)
    )
    load_ipython_extension(ipython_shell)
    execution_count = ipython_shell.execution_count
    original_cache = ipython_shell.compile.cache
    ipython_shell.run_cell(
        "%%profile --backend trace\nraise ValueError('intentional')", store_history=True
    )
    assert len(shown) == 1
    assert shown[0].state == "stopped"
    assert shown[0].result.root_run.status == "failed"
    source = next(
        source for source in shown[0].result.sources.values() if source.source.startswith("raise")
    )
    assert source.path == f"{notebook_path or 'interactive'} · cell {execution_count}"
    assert any(
        line.location.source_id == source.id and line.location.line == 1 and line.hits
        for line in shown[0].result.root_run.lines
    )
    assert ipython_shell.compile.cache == original_cache
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
    document = ReportDOM(render_html(session.result)).root
    page = document.find_all("section", css="source-page")[0]
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == [
        "Cell 1",
        "Cell 2",
    ]
    assert source_rows(page)[0].find_all("td", css="source-code")[0].text() == body.splitlines()[0]


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
    definition = (
        'def compact_helper():\n    """Return a captured answer."""\n'
        "    # Called project context\n    return 42\n"
    )
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
        table = ReportDOM(fragments[-1]).root.find_all("table")[0]
        assert table.attributes["aria-label"].startswith(
            f"LineScope cell {result.execution_count}:"
        )
        shown_source = [element.text() for element in table.find_all("code")]
        assert "def compact_helper():" in shown_source
        assert '    """Return a captured answer."""' not in shown_source
        assert "    # Called project context" not in shown_source
        return_row = next(
            row
            for row in table.find_all("tbody")[0].find_all("tr")
            if row.find_all("code")[0].text() == "    return 42"
        )
        assert return_row.find_all("td")[0].text() == "4"
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
    assert stopped.result is None
    assert len(fragments) == count
    cumulative = next(
        line for line in session.result.root_run.lines if line.location.source_id == helper.id
    )
    assert cumulative.hits == 6
    assert session.result.sources[helper.id].source == definition
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
    if result.error_before_exec:
        assert not snapshots[-1].root_run.lines
    document = ReportDOM(fragments[-1]).root
    assert document.find_all("section")[0].attributes["data-status"] == "failed"
    assert document.find_all("code")[0].text() == cell
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
    execution_count = ipython_shell.execution_count
    result = ipython_shell.run_cell(
        "%%profile --display cell-summary\ncompact_magic_answer = 42",
        store_history=True,
    )
    assert result.error_in_exec is None
    assert len(fragments) == len(snapshots) == 1
    assert snapshots[0].backend == "trace"
    assert snapshots[0].capabilities.hit_counts
    assert ipython_shell.user_ns["compact_magic_answer"] == 42
    assert "compact_magic_answer = 42" in ReportDOM(fragments[0]).root.text()
    table = ReportDOM(fragments[0]).root.find_all("table")[0]
    assert table.attributes["aria-label"].startswith(f"LineScope cell {execution_count}:")
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
    assert [ReportDOM(fragment).root.find_all("code")[0].text() for fragment in fragments] == [
        "answer = 1",
        "answer = 2",
    ]


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
