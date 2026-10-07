"""LineScope.

Author: Mavs
Description: Verify cell display ownership and metadata failure diagnostics.

"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope import Session
from linescope.model import BackendCapabilities, LineStats, SourceLocation
from linescope.notebooks.ipython import INTERNAL_CELL, NotebookIntegration
from tests.test_notebooks import shell
from tests.test_render import ReportDOM


@pytest.mark.parametrize("failed", [False, True])
def test_cell_summary_excludes_prior_measurements_and_displays_once(monkeypatch, failed):
    """Show one detached summary for each completed or failed cell.

    Omit earlier cell measurements, preserve the original cell's outcome, and
    release compiler and event hooks when notebook observation stops.

    """
    session = Session(display="cell-summary", spark=False, notebooks=False)
    session._refresh = Mock()
    session._started = 0
    notebook = shell()
    observer = NotebookIntegration(session, notebook)
    shown = Mock()
    monkeypatch.setattr("IPython.display.display", shown)
    observer.start()
    observer._pre_run_cell(SimpleNamespace(raw_cell="value = 1", cell_id="first"))
    notebook.compile.cache("value = 1", 7, raw_code="value = 1")
    source = observer._cell_source
    session.result.root_run.lines = [LineStats(SourceLocation(source.id, 1), 5, hits=1)]
    observer._post_run_cell(
        SimpleNamespace(error_in_exec=ValueError("secret") if failed else None)
    )
    document = ReportDOM(shown.call_args.args[0].data).root
    assert document.find_all("section")[0].attributes["data-status"] == (
        "failed" if failed else "success"
    )
    assert document.find_all("code")[0].text() == "value = 1"
    text = document.text()
    assert "secret" not in text
    observer._post_run_cell(SimpleNamespace())
    shown.assert_called_once()
    observer._pre_run_cell(SimpleNamespace(raw_cell=INTERNAL_CELL + "stop()"))
    assert observer._cell_started == 0
    observer._pre_run_cell(SimpleNamespace(raw_cell=object()))
    observer.stop()
    assert observer._cell_baseline is None
    assert all(not values for values in notebook.events.callbacks.values())


@pytest.mark.parametrize("error_field", [None, "error_before_exec", "error_in_exec"])
def test_unmeasured_cell_shows_source_and_next_cell_collects_metrics(
    monkeypatch: pytest.MonkeyPatch, error_field: str | None
):
    """Show unmeasured source and collect the next cell's own measurements.

    Earlier cumulative observations must not appear as costs for an unmeasured
    cell, including failures. Keep collection active and restore owned hooks.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned display override restored after the test.

    error_field : str | None
        Execution error attribute, or None for a successful unmeasured cell.

    """
    session = Session(backend="trace", display="cell-summary", spark=False, notebooks=False)
    session._refresh = Mock()
    session.result.capabilities = BackendCapabilities(hit_counts=True)
    prior = LineStats(SourceLocation("prior", 1), wall_time_ns=5, hits=1)
    session.result.root_run.lines = [prior]
    notebook = shell()
    original_cache = notebook.compile.cache
    observer = NotebookIntegration(session, notebook)
    shown = Mock()
    monkeypatch.setattr("IPython.display.display", shown)
    observer.start()
    observer._pre_run_cell(SimpleNamespace(raw_cell="pass", cell_id="empty"))
    errors = {error_field: ValueError("secret")} if error_field is not None else {}
    observer._post_run_cell(SimpleNamespace(**errors))
    shown.assert_called_once()
    document = ReportDOM(shown.call_args.args[0].data).root
    assert document.find_all("code")[0].text() == "pass"
    assert [element.text() for element in document.find_all("td")] == ["1", "—", "—", "pass"]
    shown.reset_mock()
    assert observer._cell_started == 0
    assert observer._active
    assert session.result.root_run.lines == [prior]
    assert session.result.warnings == []

    observer._pre_run_cell(SimpleNamespace(raw_cell="value = 2", cell_id="measured"))
    notebook.compile.cache("value = 2", 8, raw_code="value = 2")
    source = observer._cell_source
    session.result.root_run.lines.append(
        LineStats(SourceLocation(source.id, 1), wall_time_ns=10, hits=1)
    )
    observer._post_run_cell(SimpleNamespace())
    shown.assert_called_once()
    document = ReportDOM(shown.call_args.args[0].data).root
    assert document.find_all("code")[0].text() == "value = 2"
    assert len(document.find_all("tbody")[0].find_all("tr")) == 1
    observer.stop()
    assert notebook.compile.cache == original_cache
    assert observer._cell_baseline is None
    assert all(not values for values in notebook.events.callbacks.values())


@pytest.mark.parametrize("failure", ["baseline", "refresh", "display"])
def test_cell_summary_failures_keep_diagnostics_without_sensitive_details(monkeypatch, failure):
    """Retain display failures as diagnostics without leaking exception text.

    A failed baseline disables the summary instead of reporting cumulative
    session costs as measurements from a new cell.

    """
    session = Session(display="cell-summary", spark=False, notebooks=False)
    session._started = 0
    session._refresh = Mock()
    observer = NotebookIntegration(session, shell())
    observer._active = True
    shown = Mock()
    monkeypatch.setattr("IPython.display.display", shown)
    if failure == "baseline":
        session._refresh.side_effect = ValueError("secret baseline")
    observer._begin_cell()
    if failure == "refresh":
        session._refresh.side_effect = ValueError("secret refresh")
    elif failure == "display":
        session.result.root_run.lines = [
            LineStats(SourceLocation("cell", 1), wall_time_ns=5, hits=1)
        ]
        shown.side_effect = ValueError("secret display")
    observer._post_run_cell(SimpleNamespace(error_before_exec=ValueError()))
    assert observer._cell_started == 0
    assert any("ValueError" in warning for warning in session.result.warnings)
    assert all("secret" not in warning for warning in session.result.warnings)
    if failure == "baseline":
        shown.assert_not_called()
    observer.stop()


def test_fallback_capture_without_compiler_observer_and_inactive_callbacks():
    """Capture raw cell text when compiler observation is unavailable.

    Ignore post-cell callbacks before activation and never capture internal
    control cells as workload source.

    """
    session = Session(display="none", spark=False, notebooks=False)
    observer = NotebookIntegration(session, shell())
    observer._post_run_cell(SimpleNamespace())
    observer._pre_run_cell(SimpleNamespace(raw_cell="value = 1", cell_id="fallback"))
    assert observer._cell_source.source == "value = 1"
    observer._pre_run_cell(SimpleNamespace(raw_cell=INTERNAL_CELL + "stop()"))
    assert observer._cell_source is None
