"""LineScope.

Author: Mavs
Description: Notebook lifecycle, source, and Databricks correlation checks.

"""

from __future__ import annotations

import linecache
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from linescope.model import BackendCapabilities, ProfileResult, ProfileRun, SourceUnit
from linescope.notebooks.correlation import ChildContext, merge_child
from linescope.notebooks.databricks import (
    DatabricksIntegration,
    notebook_path,
    resolve_notebook_path,
    safe_parameters,
)
from linescope.notebooks.ipython import (
    NotebookIntegration,
    _local_notebook_path,
    load_ipython_extension,
    unload_ipython_extension,
)


def test_notebook_types_do_not_import_optional_dependencies():
    """Verify notebook types do not import optional dependencies.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    script = """
import importlib.abc
import sys

class BlockOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'databricks', 'IPython', 'pyspark', 'dbruntime'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, BlockOptionalImports())

from linescope import Session
from linescope.notebooks.databricks import DatabricksIntegration
from linescope.notebooks.ipython import NotebookIntegration
from linescope.notebooks.remote import ChildCollector, PreparedChild, WorkspaceStore

session = Session(backend='trace', notebooks=False, spark=False, display='none')
integration = NotebookIntegration(session)
assert integration.shell is None
integration.start()
integration.stop()
assert not any(name.split('.')[0] in {'databricks', 'IPython', 'pyspark', 'dbruntime'}
               for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


class Registry:
    """Capture notebook snapshots and runtime aliases for integration tests.

    Attributes
    ----------
    sources : dict[str, [SourceUnit]]
        Captured notebook source snapshots keyed by identifier.

    aliases : dict[str, [SourceUnit]]
        Compiler filenames mapped to their captured source snapshots.

    """

    def __init__(self):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.sources = {}
        self.aliases = {}

    def register(self, source, filename=None):
        """Record a source snapshot or shell callback for later assertions.

        Retain the exact source or callback identity supplied by the
        integration.

        """
        self.sources[source.id] = source
        if filename:
            self.aliases[filename] = source
        return source

    def snapshot(self, filename):
        """Return the source associated with a recorded runtime filename.

        Return None when the requested runtime alias was never captured.

        """
        return self.aliases.get(filename)


class Session:
    """Retain controlled integration results and observed source locations.

    Attributes
    ----------
    registry : object
        Minimal source registry used by the integration under test.

    config : SimpleNamespace
        Controlled session options used by notebook hook tests.

    result : [ProfileResult]
        Normalized result receiving observed notebook or Spark runs.

    locations : list[Any]
        Source locations supplied when child runs or actions are attached.

    shows : int
        Number of report display requests observed by the test double.

    """

    def __init__(self, display="end"):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.registry = Registry()
        self.config = SimpleNamespace(display=display)
        self.result = ProfileResult(
            ProfileRun(), self.registry.sources, "trace", BackendCapabilities()
        )
        self.locations = []
        self.shows = 0

    def add_child_run(self, child, location):
        """Attach a controlled child run and record its source location.

        Use controlled notebook utilities and event callbacks to inspect capture
        and correlation without a remote workspace.

        """
        self.result.root_run.children.append(child)
        self.locations.append(location)

    def show(self):
        """Record a report display request without opening a browser.

        Count the request while leaving display behavior under test control.

        """
        self.shows += 1


class Events:
    """Emulate shell event registration and cleanup.

    Attributes
    ----------
    callbacks : dict[str, list[Callable[..., Any]]]
        Registered shell callbacks grouped by event name.

    """

    def __init__(self):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.callbacks = {}

    def register(self, name, callback):
        """Record a source snapshot or shell callback for later assertions.

        Retain the exact source or callback identity supplied by the
        integration.

        """
        self.callbacks.setdefault(name, []).append(callback)

    def unregister(self, name, callback):
        """Remove the callback owned by the integration under test.

        Preserve unrelated registrations while removing the requested callback.

        """
        self.callbacks[name].remove(callback)


def shell():
    """Provide a controlled notebook shell with reversible event hooks.

    Keep event registrations, compiler aliases, and namespace changes available
    for cleanup assertions.

    """
    value = SimpleNamespace(
        events=Events(),
        user_ns={},
        execution_count=7,
        history_manager=SimpleNamespace(input_hist_raw=[]),
        transform_cell=lambda value: value,
        compile=SimpleNamespace(cache=lambda _value, count, **_kwargs: f"<ipython-input-{count}>"),
    )
    return value


def test_capture_preserves_full_cell_and_compiler_alias():
    """Verify capture preserves full cell and compiler alias.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    adapter = NotebookIntegration(session, shell())
    source = adapter.capture("# full cell\nx = 4\n", filename="<cell-7>")
    assert source.kind == "notebook"
    assert source.source == "# full cell\nx = 4\n"
    assert session.registry.snapshot("<cell-7>") is source
    assert linecache.getline("<cell-7>", 2) == "x = 4\n"


@pytest.mark.parametrize(
    ("namespace", "environment", "expected"),
    [
        ({"__vsc_ipynb_file__": "C:\\work\\example.ipynb"}, None, "C:\\work\\example.ipynb"),
        ({"__session__": "/work/example.ipynb"}, None, "/work/example.ipynb"),
        ({}, "/work/example.ipynb", "/work/example.ipynb"),
        (
            {"__vsc_ipynb_file__": "current.ipynb", "__session__": "old.ipynb"},
            "older.ipynb",
            "current.ipynb",
        ),
        ({"__vsc_ipynb_file__": None, "__session__": "session-id"}, "console.py", None),
        ({"__vsc_ipynb_file__": 42, "__session__": ""}, "example.IPYNB", "example.IPYNB"),
        ({}, None, None),
    ],
)
def test_local_notebook_identity_uses_only_frontend_paths(
    monkeypatch: pytest.MonkeyPatch,
    namespace: dict[str, object],
    environment: str | None,
    expected: str | None,
):
    """Resolve frontend filenames while rejecting non-notebook session labels.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned environment changes restored after each case.

    namespace : dict[str, object]
        Notebook metadata exposed by the execution frontend.

    environment : str | None
        Jupyter kernel-start path, when available.

    expected : str | None
        Preferred notebook filename, or None for unavailable identity.

    """
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    if environment is not None:
        monkeypatch.setenv("JPY_SESSION_NAME", environment)
    assert _local_notebook_path(SimpleNamespace(user_ns=namespace)) == expected
    assert _local_notebook_path(None) is None


@pytest.mark.parametrize("child_path", [None, "/Workspace/original-child"])
def test_workspace_notebook_identity_takes_priority(
    monkeypatch: pytest.MonkeyPatch, child_path: str | None
):
    """Retain workspace and original child paths ahead of local metadata.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Owned Databricks context and local environment replacements.

    child_path : str | None
        Original child path overriding a generated notebook's runtime context.

    """
    monkeypatch.setenv("JPY_SESSION_NAME", "local.ipynb")
    monkeypatch.setattr(
        "linescope.notebooks.databricks.notebook_path", lambda _dbutils: "/Workspace/runtime"
    )
    session = Session()
    session._notebook_path = child_path
    ipython = shell()
    ipython.user_ns["__vsc_ipynb_file__"] = "editor.ipynb"
    adapter = NotebookIntegration(session, ipython, SimpleNamespace())
    assert adapter.path == (child_path or "/Workspace/runtime")
    assert adapter.databricks.path == adapter.path


def test_edited_cell_keeps_previous_snapshot():
    """Verify edited cell keeps previous snapshot.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    adapter = NotebookIntegration(session, shell())
    first = adapter.capture("x=1", cell_id="stable")
    second = adapter.capture("x=2", cell_id="stable")
    assert first.id != second.id
    assert session.registry.sources[first.id].source == "x=1"


def test_multiple_cells_default_end_does_not_display():
    """Verify multiple cells default end does not display.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    ipython = shell()
    adapter = NotebookIntegration(session, ipython)
    adapter.start()
    adapter.start()
    assert len(ipython.events.callbacks["pre_run_cell"]) == 1
    for index in range(3):
        ipython.execution_count = index
        adapter._pre_run_cell(SimpleNamespace(raw_cell=f"x={index}", cell_id=str(index)))
        ipython.compile.cache(f"x={index}", index, raw_code=f"x={index}")
        adapter._post_run_cell(None)
    assert len(session.result.sources) == 3
    assert session.shows == 0
    adapter.stop()
    adapter.stop()
    assert not ipython.events.callbacks["pre_run_cell"]
    assert not ipython.events.callbacks["post_run_cell"]


def test_start_captures_current_cell_and_live_display_is_explicit(monkeypatch):
    """Verify start captures current cell and live display is explicit.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session("cell")
    ipython = shell()
    ipython.history_manager.input_hist_raw = ["", "profile.start()\nx=4"]
    frame = SimpleNamespace(
        f_code=SimpleNamespace(co_filename="<ipython-input-7-aabb>"), f_back=None
    )
    monkeypatch.setattr("linescope.notebooks.ipython.inspect.currentframe", lambda: frame)
    adapter = NotebookIntegration(session, ipython)
    adapter.start()
    assert len(session.result.sources) == 1
    adapter._post_run_cell(None)
    assert session.shows == 1
    adapter.stop()
    adapter._post_run_cell(None)
    assert session.shows == 1


@pytest.mark.parametrize(
    "filename", ["<command-123456-789>", "/root/.ipykernel/42/command-123456-789.py"]
)
def test_databricks_current_cell_uses_actual_cached_command_source(monkeypatch, filename):
    """Verify databricks current cell uses actual cached command source.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    ipython = shell()
    source = "with profile():\n    answer = 42\n"
    monkeypatch.setitem(
        linecache.cache, filename, (len(source), None, source.splitlines(keepends=True), filename)
    )
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename=filename), f_back=None)
    monkeypatch.setattr("linescope.notebooks.ipython.inspect.currentframe", lambda: frame)
    adapter = NotebookIntegration(session, ipython)
    adapter.path = "/Workspace/project/main"
    adapter.start()
    try:
        unit = session.registry.snapshot(filename)
        assert unit is not None
        assert unit.source == source
        assert unit.kind == "notebook"
        assert unit.path.startswith("/Workspace/project/main")
    finally:
        adapter.stop()


def test_inline_run_has_navigable_reference_without_fake_measurements():
    """Verify inline run has navigable reference without fake measurements.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    adapter = NotebookIntegration(session, shell())
    adapter.path = "/Workspace/project/main"
    source = adapter.capture('%run "./common notebook"\nx=1')
    child = session.result.root_run.children[0]
    assert child.source.path == "/Workspace/project/common notebook"
    assert child.status == "reference"
    assert child.lines == []
    assert child.metadata["inline"] is True
    assert session.locations[0].source_id == source.id
    assert session.locations[0].line == 1


@pytest.mark.parametrize("source", ["%run -i script.py", '%run "unterminated', "%run", "x=1"])
def test_unresolved_inline_run_is_not_guessed(source):
    """Verify unresolved inline run is not guessed.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    session = Session()
    NotebookIntegration(session, shell()).capture(source)
    assert session.result.root_run.children == []


def test_parameters_never_expose_values():
    """Verify parameters never expose values.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    data = safe_parameters({"password": "secret", "date": "sensitive", "API_KEY": "123"})
    assert data == {"password": "[redacted]", "date": "[value omitted]", "API_KEY": "[redacted]"}
    assert safe_parameters("something") == {}
    assert len(safe_parameters({str(i): i for i in range(100)})) == 50


@pytest.mark.parametrize(
    ("path", "parent", "expected"),
    [
        ("./child", "/Workspace/demo/main", "/Workspace/demo/child"),
        ("../shared", "/Workspace/demo/main", "/Workspace/shared"),
        ("/Workspace/child", "main", "/Workspace/child"),
    ],
)
def test_notebook_path_resolution(path, parent, expected):
    """Verify notebook path resolution.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    assert resolve_notebook_path(path, parent) == expected


def test_restricted_notebook_context_returns_unknown():
    """Verify restricted notebook context returns unknown.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    assert notebook_path(SimpleNamespace()) is None


def test_dbutils_success_preserves_call_result_and_restores_descriptor():
    """Verify dbutils success preserves call result and restores descriptor.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    calls = []

    class Notebook:
        """Emulate notebook invocation or exit behavior for integration tests.

        Expose a controlled notebook API for invocation and exit cleanup.

        """

        def run(self, *args, **kwargs):
            """Run the controlled workload or simulated notebook invocation.

            Use controlled notebook utilities and event callbacks to inspect
            capture and correlation without a remote workspace.

            """
            calls.append((args, kwargs))
            return "private returned value"

    dbutils = SimpleNamespace(notebook=Notebook())
    session = Session()
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    adapter.start()
    result = dbutils.notebook.run("./child", 30, {"token": "never store this"})
    adapter.stop()
    assert result == "private returned value"
    assert calls == [(("./child", 30, {"token": "never store this"}), {})]
    assert "run" not in vars(dbutils.notebook)
    child = session.result.root_run.children[0]
    assert child.status == "success"
    assert child.elapsed_ns > 0
    assert child.parent_id == session.result.root_run.id
    assert child.metadata["result_type"] == "str"
    assert "private returned value" not in str(child.metadata)
    assert "never store this" not in str(child.metadata)


def test_dbutils_exception_propagates_and_metadata_omits_message():
    """Verify dbutils exception propagates and metadata omits message.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """

    def failing(*args):
        """Provide the controlled behavior used by this test.

        Raise a controlled workload error to exercise instrumentation cleanup.

        """
        del args
        raise ValueError("sensitive exception detail")

    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=failing))
    session = Session()
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    with pytest.raises(ValueError, match="sensitive"):
        dbutils.notebook.run("child", 1)
    adapter.stop()
    child = session.result.root_run.children[0]
    assert child.status == "failed"
    assert child.metadata["error_type"] == "ValueError"
    assert "sensitive" not in str(child.metadata)
    assert dbutils.notebook.run is failing


def test_dbutils_honors_explicit_child_context_once_without_mutating_arguments():
    """Check the expected behavior in this regression case.

    Verify dbutils honors explicit child context once without mutating
    arguments.

    """
    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=lambda *_args: "ok"))
    session = Session()
    context = ChildContext.create(session.result.root_run.id)
    parameters = context.as_parameters()
    before = dict(parameters)
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    try:
        dbutils.notebook.run("child", 1, parameters)
        dbutils.notebook.run("child", 1, parameters)
    finally:
        adapter.stop()
    children = session.result.root_run.children
    assert children[0].id == context.correlation_id
    assert children[1].id != context.correlation_id
    assert parameters == before


def test_dbutils_ignores_other_thread_and_preserves_later_patch():
    """Verify dbutils ignores other thread and preserves later patch.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=lambda *_args: "ok"))
    session = Session()
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    thread = threading.Thread(target=lambda: dbutils.notebook.run("child", 1))
    thread.start()
    thread.join()
    assert session.result.root_run.children == []

    def replacement(*args):
        """Simulate a later notebook patch whose ownership must be preserved.

        Keep later instrumentation distinguishable from the observer-owned
        patch.

        """
        del args
        return "new"

    dbutils.notebook.run = replacement
    adapter.stop()
    assert dbutils.notebook.run is replacement


def test_child_context_tokens_and_immutable_merge():
    """Verify child context tokens and immutable merge.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    parent = Session().result
    context = ChildContext.create(parent.root_run.id)
    assert context != ChildContext.create(parent.root_run.id)
    assert context.as_parameters()["linescope_parent_id"] == parent.root_run.id
    parent.root_run.children.append(
        ProfileRun(id=context.correlation_id, parent_id=parent.root_run.id, metadata={"wait": 12})
    )
    child = Session().result
    child.root_run.parent_id = parent.root_run.id
    child.root_run.metadata["correlation_id"] = context.correlation_id
    source = SourceUnit("child-source", "child.py", "x=1")
    child.sources[source.id] = source
    merged = merge_child(parent, child, context.correlation_id)
    assert merged.root_run.children[0].metadata["wait"] == 12
    assert merged.root_run.children[0].metadata["parent_wait_time_ns"] == 0
    assert merged.root_run.children[0].metadata["collection"] == "child profile merged"
    assert merged.root_run.children[0].metadata["child_backend"] == "trace"
    assert merged.root_run.children[0].metadata["child_capabilities"]["memory"] is False
    assert source.id in merged.sources
    assert source.id not in parent.sources
    assert child.root_run.id != context.correlation_id


@pytest.mark.parametrize("failure", ["unknown", "parent", "correlation", "collision"])
def test_child_merge_rejects_ambiguous_or_conflicting_data(failure):
    """Verify child merge rejects ambiguous or conflicting data.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    parent, child = Session().result, Session().result
    parent.root_run.children.append(ProfileRun(id="call"))
    if failure == "parent":
        child.root_run.parent_id = "someone-else"
    elif failure == "correlation":
        child.root_run.metadata["correlation_id"] = "wrong"
    elif failure == "collision":
        parent.sources["one"] = SourceUnit("one", "test.py", "x=1")
        child.sources["one"] = SourceUnit("one", "test.py", "x=2")
    with pytest.raises(ValueError, match=r"Unknown|different parent|correlation|collision"):
        merge_child(parent, child, "unknown" if failure == "unknown" else "call")


def test_extension_restores_existing_magic():
    """Verify extension restores existing magic.

    Use controlled notebook utilities and event callbacks to inspect capture and
    correlation without a remote workspace.

    """
    old = object()
    ipython = SimpleNamespace(magics_manager=SimpleNamespace(magics={"cell": {"profile": old}}))
    ipython.register_magic_function = lambda func, kind, name: ipython.magics_manager.magics[
        kind
    ].__setitem__(name, func)
    load_ipython_extension(ipython)
    assert callable(ipython.magics_manager.magics["cell"]["profile"])
    load_ipython_extension(ipython)
    unload_ipython_extension(ipython)
    assert ipython.magics_manager.magics["cell"] == {"profile": old}
