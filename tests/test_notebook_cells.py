"""LineScope.

Author: Mavs
Description: Regress notebook positions, executed-cell selection, and wrappers.

"""

import json
import linecache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope import Session
from linescope.model import SourceUnit
from linescope.notebooks.ipython import NotebookIntegration
from linescope.notebooks.remote import notebook_sources
from linescope.notebooks.serialization import dumps_result, loads_result
from linescope.render import render_html
from linescope.source import SourceRegistry
from tests.test_notebook_integration import ipython_shell as ipython_shell
from tests.test_notebooks import shell
from tests.test_render import ReportDOM, source_rows


@pytest.mark.parametrize("frontend", ["jupyter", "databricks"])
def test_executed_cells_keep_notebook_positions_and_links(
    ipython_shell, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frontend: str
):
    """Show executed cells in notebook order across both notebook frontends.

    Count markdown cells, retain source with no line hits, omit uncalled old
    definitions, and keep called definitions and their source links. Restore
    compiler and trace ownership when the session ends.

    Parameters
    ----------
    ipython_shell : InteractiveShell
        Isolated real IPython shell running the selected notebook cells.

    monkeypatch : pytest.MonkeyPatch
        Owned frontend metadata and workspace export replacements.

    tmp_path : Path
        Directory containing the saved Jupyter document.

    frontend : str
        Notebook frontend whose position discovery is exercised.

    """
    cells = [
        ("markdown", "# Notebook heading"),
        ("code", "def uncalled_position_helper():\n    return -1"),
        ("code", "def called_position_helper():\n    return 42"),
        (
            "code",
            (
                "from linescope import profile\n"
                "session = profile.start(backend='trace', spark=False, display='none')"
            ),
        ),
        ("code", "ordered_answer = called_position_helper()"),
        ("code", "out_of_order = 1"),
        ("code", "# Executed cell with no traceable Python lines"),
        ("code", "profile.stop()"),
    ]
    path = str(tmp_path / "positions.ipynb")
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    monkeypatch.delitem(ipython_shell.user_ns, "__session__", raising=False)
    monkeypatch.delitem(ipython_shell.user_ns, "dbutils", raising=False)
    if frontend == "jupyter":
        Path(path).write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": kind, "id": f"cell-{index}", "source": source}
                        for index, (kind, source) in enumerate(cells)
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setitem(ipython_shell.user_ns, "__vsc_ipynb_file__", path)
    else:
        path = "/Workspace/positions"
        monkeypatch.delitem(ipython_shell.user_ns, "__vsc_ipynb_file__", raising=False)
        monkeypatch.setitem(ipython_shell.user_ns, "dbutils", SimpleNamespace())
        monkeypatch.setattr("linescope.notebooks.databricks.notebook_path", lambda _utils: path)
        exported = "# Databricks notebook source\n" + "\n# COMMAND ----------\n".join(
            source if kind == "code" else f"# MAGIC %md\n# MAGIC {source}"
            for kind, source in cells
        )
        store = Mock()
        store.read.return_value = exported.encode()
        monkeypatch.setattr("linescope.notebooks.ipython.WorkspaceStore", lambda: store)

    original_cache = ipython_shell.compile.cache
    for index in (1, 2, 3, 5, 4, 6, 7):
        result = ipython_shell.run_cell(
            cells[index][1], store_history=True, cell_id=f"cell-{index}"
        )
        assert result.error_in_exec is None
    session = ipython_shell.user_ns["session"]
    assert ipython_shell.user_ns["ordered_answer"] == 42
    assert ipython_shell.compile.cache == original_cache
    captured = [unit for unit in session.result.sources.values() if unit.kind == "notebook"]
    assert {unit.cell_number for unit in captured} == {3, 4, 5, 6, 7, 8}
    assert all("uncalled_position_helper" not in unit.source for unit in captured)
    document = ReportDOM(render_html(session.result)).root
    page = document.find_all("section", css="source-page")[0]
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == [
        f"Cell {number}" for number in (3, 4, 5, 6, 7, 8)
    ]
    assert "snapshot" not in " ".join(
        heading.text() for heading in page.find_all("tr", css="source-cell-heading")
    )
    ids = {
        element.attributes["id"] for element in document.find_all() if "id" in element.attributes
    }
    for link in document.find_all("a"):
        href = link.attributes.get("href", "")
        if href.startswith("#"):
            assert href[1:] in ids
    assert len(source_rows(page)) == sum(
        len(cells[index][1].splitlines()) for index in (2, 3, 4, 5, 6, 7)
    )
    assert loads_result(dumps_result(session.result)).sources == session.result.sources
    if frontend == "databricks":
        store.read.assert_called_once_with(path, "SOURCE")


def test_duplicate_source_requires_identity_and_edits_retain_position(
    monkeypatch: pytest.MonkeyPatch,
):
    """Resolve duplicate cells by frontend ID and retain executed revisions.

    Ambiguous text must not be assigned the next unused notebook number.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned current-cell stack frame restored after the test.

    """
    session = Session(backend="trace", spark=False, display="none")
    session._notebook_path = "/project/duplicates.ipynb"
    session._notebook_sources = [
        SourceUnit(
            f"notebook://{session._notebook_path}#cell-{number}",
            f"{session._notebook_path} · cell {number}",
            "value = 1",
            "notebook",
            cell_number=number,
            cell_id=identity,
        )
        for number, identity in ((2, "earlier"), (5, "later"))
    ]
    observer = NotebookIntegration(session, shell())
    unknown = observer.capture("value = 1", cell_id="42")
    assert unknown.cell_number is None
    assert unknown.path.endswith(" · execution 42")
    known = observer.capture("value = 1", cell_id="later")
    edited = observer.capture("value = 2", cell_id="later")
    assert known.cell_number == edited.cell_number == 5
    assert edited.id != known.id
    assert observer.capture("value = 2", cell_id="later") is edited
    page = ReportDOM(render_html(session.result)).root.find_all("section", css="source-page")[0]
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == [
        "Cell 5 · version 1",
        "Cell 5 · version 2",
        "Execution 42",
    ]

    # Starting inside a cell misses pre_run_cell, so use the kernel's current
    # execute request to disambiguate identical saved cells as well.
    notebook = shell()
    notebook.history_manager.input_hist_raw = ["", "value = 1"]
    notebook.get_parent = lambda: {
        "metadata": {"cellId": "later"},
        "content": {"code": "value = 1"},
    }
    frame = SimpleNamespace(
        f_code=SimpleNamespace(co_filename="<ipython-input-77-aabb>"), f_back=None
    )
    monkeypatch.setattr("linescope.notebooks.ipython.inspect.currentframe", lambda: frame)
    started = NotebookIntegration(session, notebook)
    try:
        started.start()
        assert started._cell_source is known
    finally:
        started.stop()


def test_databricks_wrapper_never_becomes_a_second_source(monkeypatch: pytest.MonkeyPatch):
    """Capture user code from a cached dispatch frame and ignore its wrapper.

    Keep the real body's compiler alias for measurements and avoid assigning
    the wrapper's line-one event to the user's first source line.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned stack frame and line-cache entries restored after the test.

    """
    body = "from linescope import profile\nsession = profile.start()"
    wrapper = f"get_ipython().run_cell_magic('python', '', {body!r})"
    filename = "<command-123-456>"
    monkeypatch.setitem(linecache.cache, filename, (len(wrapper), None, [wrapper], filename))
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename=filename), f_back=None)
    monkeypatch.setattr("linescope.notebooks.ipython.inspect.currentframe", lambda: frame)
    session = Session(backend="trace", spark=False, display="none")
    session._notebook_path = "/Workspace/wrapped"
    session._notebook_sources = notebook_sources(
        session._notebook_path, f"ignored = 1\n# COMMAND ----------\n{body}"
    )
    notebook = shell()
    original_cache = notebook.compile.cache
    observer = NotebookIntegration(session, notebook)
    try:
        observer.start()
        observer._pre_run_cell(SimpleNamespace(raw_cell=wrapper, cell_id="wrapper"))
        wrapper_alias = notebook.compile.cache(wrapper, 99, raw_code=wrapper)
        assert not session.registry.accepts(wrapper_alias)
        observer._pre_run_cell(SimpleNamespace(raw_cell=body, cell_id="body"))
        body_alias = notebook.compile.cache(body, 100, raw_code=body)
        assert session.registry.snapshot(body_alias).cell_number == 2
    finally:
        observer.stop()
    assert notebook.compile.cache == original_cache
    assert len(session.result.sources) == 1
    assert next(iter(session.result.sources.values())).source == body
    assert "run_cell_magic" not in render_html(session.result)
    assert all(not callbacks for callbacks in notebook.events.callbacks.values())


def test_definition_alias_is_accepted_before_its_source_is_observed():
    """Freeze old definitions privately until a collector observes execution.

    Ownership checks alone must not expose an unused notebook cell in reports.

    """
    registry = SourceRegistry()
    unit = SourceUnit(
        "notebook://demo#cell-8", "demo · cell 8", "def old():\n    return 1", "notebook"
    )
    registry.register(unit, filename="<old-cell>", deferred=True)
    assert registry.accepts("<old-cell>")
    assert registry.sources == {}
    assert registry.snapshot("<old-cell>") is unit
    assert registry.sources == {unit.id: unit}


def test_failed_source_capture_does_not_interrupt_cell_completion(monkeypatch: pytest.MonkeyPatch):
    """Keep source metadata failures diagnostic and restore notebook hooks.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned capture failure restored after the test.

    """
    session = Session(backend="trace", spark=False, display="none")
    notebook = shell()
    original_cache = notebook.compile.cache
    observer = NotebookIntegration(session, notebook)
    observer.start()
    monkeypatch.setattr(observer, "capture", Mock(side_effect=ValueError("private source detail")))
    try:
        observer._pre_run_cell(SimpleNamespace(raw_cell="invalid Python"))
        observer._post_run_cell(SimpleNamespace(execution_count=7))
    finally:
        observer.stop()
    assert notebook.compile.cache == original_cache
    assert session.result.warnings == ["Notebook source metadata unavailable (ValueError)."]
    assert all(not callbacks for callbacks in notebook.events.callbacks.values())


def test_denied_workspace_export_keeps_executed_cells(monkeypatch: pytest.MonkeyPatch):
    """Keep notebook profiling usable when Databricks denies source export.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned workspace context and export denial restored after the test.

    """
    monkeypatch.setattr(
        "linescope.notebooks.databricks.notebook_path", lambda _utils: "/Workspace/demo"
    )
    monkeypatch.setattr(
        "linescope.notebooks.ipython.WorkspaceStore",
        Mock(side_effect=PermissionError("private credentials")),
    )
    session = Session(backend="trace", spark=False, display="none")
    notebook = shell()
    original_cache = notebook.compile.cache
    observer = NotebookIntegration(session, notebook, SimpleNamespace())
    observer.start()
    try:
        observer._pre_run_cell(SimpleNamespace(raw_cell="value = 1"))
        notebook.compile.cache("value = 1", 7, raw_code="value = 1")
    finally:
        observer.stop()
    assert len(session.result.sources) == 1
    assert next(iter(session.result.sources.values())).cell_number is None
    assert session.result.warnings == ["Notebook cell positions unavailable (PermissionError)."]
    assert notebook.compile.cache == original_cache
    assert all(not callbacks for callbacks in notebook.events.callbacks.values())


@pytest.mark.parametrize("contents", [None, "invalid JSON", '{"cells": null}'])
def test_unavailable_notebook_positions_keep_source_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str | None
):
    """Retain executed source with honest labels when position lookup fails.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned frontend path metadata restored after the test.

    tmp_path : Path
        Directory containing an absent or malformed notebook document.

    contents : str | None
        Saved malformed content, or None for an absent notebook file.

    """
    path = tmp_path / "unavailable.ipynb"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    notebook = shell()
    monkeypatch.setitem(notebook.user_ns, "__vsc_ipynb_file__", str(path))
    original_cache = notebook.compile.cache
    session = Session(backend="trace", spark=False, display="none")
    observer = NotebookIntegration(session, notebook)
    observer.start()
    try:
        observer._pre_run_cell(SimpleNamespace(raw_cell="value = 1"))
        notebook.compile.cache("value = 1", 7, raw_code="value = 1")
    finally:
        observer.stop()
    assert len(session.result.sources) == 1
    assert next(iter(session.result.sources.values())).cell_number is None
    assert "Execution 7" in render_html(session.result)
    assert any("cell positions unavailable" in warning for warning in session.result.warnings)
    assert notebook.compile.cache == original_cache
    assert all(not callbacks for callbacks in notebook.events.callbacks.values())
