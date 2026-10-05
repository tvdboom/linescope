"""LineScope.

Author: Mavs
Description: Automatic child source, profile transport, and failure cleanup
checks using an offline workspace and real IPython execution.

"""

from __future__ import annotations

import ast
import json
import re
from types import SimpleNamespace

import pytest

from linescope import Session
from linescope.backends import RawBackendResult
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    GPUStats,
    LineStats,
    MemorySample,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SparkExecution,
    SparkOperator,
    SymbolDefinition,
    SymbolRef,
)
from linescope.notebooks.databricks import DatabricksIntegration
from linescope.notebooks.remote import (
    INTERNAL_CELL,
    ChildCollector,
    PreparedChild,
    WorkspaceStore,
    notebook_sources,
    start_child,
)
from linescope.notebooks.serialization import dumps_result, loads_result
from linescope.render import render_html
from tests.test_render import ReportDOM

PATH = "/Workspace/project/child"
SOURCE = (
    "# Databricks notebook source\n"
    "def child_work(value):\n    return value + 1\n"
    "\n# COMMAND ----------\n\n"
    "answer = child_work(41)\n"
    "\n# COMMAND ----------\n\n"
    "# unexecuted cell\nunused = 99\n"
)


class MemoryStore:
    """Emulate owned workspace files and failures without Databricks access.

    Attributes
    ----------
    files : dict[str, bytes]
        Workspace paths mapped to controlled notebook or profile content.

    language : str
        Notebook language reported by the simulated workspace status.

    deleted : list[str]
        Owned paths deleted during cleanup, retained for assertions.

    reject_notebook : bool
        Whether notebook writes simulate an SDK permission failure.

    reject_cleanup : bool
        Whether deletion simulates an SDK permission failure.

    """

    def __init__(self):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.files = {PATH: SOURCE.encode()}
        self.language = "PYTHON"
        self.deleted = []
        self.reject_notebook = False
        self.reject_cleanup = False

    def status(self, path):
        """Return controlled notebook status metadata for the requested path.

        Assert that the requested object exists before reporting its language
        and kind.

        """
        assert path in self.files
        return {"object_type": "NOTEBOOK", "language": self.language}

    def read(self, path, export_format="AUTO"):
        """Return stored workspace bytes in a supported export format.

        Preserve exact bytes so notebook and profile round trips can be checked.

        """
        assert export_format in {"SOURCE", "AUTO"}
        return self.files[path]

    def write(self, path, content, *, notebook=False, overwrite=False):
        """Store workspace content or simulate a configured permission failure.

        Preserve overwrite semantics and retain configured error paths for
        cleanup checks.

        """
        if notebook and self.reject_notebook:
            raise PermissionError("private credential detail")
        if path in self.files and not overwrite:
            raise FileExistsError(path)
        self.files[path] = content

    def delete(self, path):
        """Delete an owned workspace object or simulate a cleanup failure.

        Record the requested path and preserve the configured deletion failure.

        """
        if self.reject_cleanup:
            raise PermissionError("private credential detail")
        self.deleted.append(path)
        del self.files[path]


@pytest.fixture
def store(monkeypatch):
    """Provide an in-memory workspace store with controlled SDK behavior.

    Replace remote workspace operations with recorded reads, writes, and
    deletions.

    """
    value = MemoryStore()
    monkeypatch.setattr("linescope.notebooks.remote.WorkspaceStore", lambda: value)
    return value


@pytest.fixture
def shell(monkeypatch):
    """Provide a controlled notebook shell with reversible event hooks.

    Keep event registrations, compiler aliases, and namespace changes available
    for cleanup assertions.

    """
    ipython = pytest.importorskip("IPython.core.interactiveshell")
    value = ipython.InteractiveShell.instance()
    previous = dict(value.user_ns)
    monkeypatch.setattr("IPython.get_ipython", lambda: value)
    yield value
    value.user_ns.clear()
    value.user_ns.update(previous)


def context(tmp_path):
    """Provide child notebook source, options, and correlation metadata.

    Retain the original notebook path and independent parent and child
    identifiers.

    """
    return {
        "path": PATH,
        "source": SOURCE,
        "parent_id": "parent",
        "correlation_id": "invocation",
        "profile_path": "/Workspace/project/result.json",
        "options": {"backend": "trace", "root": str(tmp_path), "spark": False, "display": "none"},
    }


def test_json_round_trip_preserves_models_unknowns_and_nested_metrics():
    """Verify json round trip preserves models unknowns and nested metrics.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    source = notebook_sources(PATH, SOURCE)[0]
    location = SourceLocation(source.id, 2, 4, "child_work")
    line = LineStats(
        location,
        wall_time_ns=5,
        hits=None,
        memory=MemoryStats(None, 1024),
        ram=ProcessMemoryStats(2048, None, 4096),
        gpu=GPUStats(3, None),
        calls=[SymbolRef("child_work", 1, 0, 10, location)],
    )
    execution = SparkExecution(
        "sql", location=location, operators=[SparkOperator("scan", "Scan", locations=[location])]
    )
    result = ProfileResult(
        ProfileRun(
            source=source,
            lines=[line],
            spark_executions=[execution],
            children=[ProfileRun()],
            memory_samples=[MemorySample(0, None), MemorySample(5, 2048, location)],
        ),
        {source.id: source},
        "trace",
        BackendCapabilities(),
        symbols=[SymbolDefinition("function", "child_work", source.id, 1)],
    )
    assert loads_result(dumps_result(result)) == result


def test_json_rejects_unknown_schema():
    """Verify json rejects unknown schema.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    with pytest.raises(ValueError, match="format"):
        loads_result(b'{"version":2}')


def test_json_without_function_line_count_keeps_span_unavailable():
    """Restore older function records without inventing source line counts.

    Keep the existing JSON version readable when a function lacks the newly
    captured source span.

    """
    result = ProfileResult(
        ProfileRun(functions=[FunctionStats("source", "work", 1)]),
        {},
        "trace",
        BackendCapabilities(),
    )
    serialized = json.loads(dumps_result(result))
    del serialized["result"]["root_run"]["functions"][0]["line_count"]

    restored = loads_result(json.dumps(serialized).encode("utf-8"))
    assert restored.root_run.functions[0].line_count is None


def test_workspace_store_uses_sdk_auth_and_nonrecursive_owned_operations(monkeypatch):
    """Verify workspace store uses sdk auth and nonrecursive owned operations.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    calls = []

    def request(method, path, **kwargs):
        """Capture an SDK request and return a controlled workspace response.

        Record authentication-independent request arguments without making a
        network call.

        """
        calls.append((method, path, kwargs))
        if path.endswith("export"):
            return {"content": "dmFsdWUgPSAx"}
        return {"language": "PYTHON"}

    client = SimpleNamespace(api_client=SimpleNamespace(do=request))
    monkeypatch.setattr(
        "linescope.notebooks.remote.import_module",
        lambda _name: SimpleNamespace(WorkspaceClient=lambda: client),
    )
    workspace = WorkspaceStore()
    assert workspace.read(PATH, "SOURCE") == b"value = 1"
    assert workspace.status(PATH)["language"] == "PYTHON"
    workspace.write("/temporary", b"value = 1", notebook=True)
    workspace.write("/temporary.json", b"{}", overwrite=True)
    workspace.delete("/temporary")
    assert calls[2][2]["body"]["language"] == "PYTHON"
    assert calls[2][2]["body"]["overwrite"] is False
    assert calls[3][2]["body"]["format"] == "AUTO"
    assert calls[4][2]["body"] == {"path": "/temporary", "recursive": False}


@pytest.mark.parametrize("failed", [False, True])
def test_collector_captures_real_child_lines_and_restores_hooks(tmp_path, store, shell, failed):
    """Verify collector captures real child lines and restores hooks.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    settings = context(tmp_path)
    store.files[settings["profile_path"]] = b""
    collector = start_child(settings)
    try:
        cells = notebook_sources(PATH, SOURCE)
        shell.run_cell(cells[0].source, store_history=True)
        shell.run_cell(cells[1].source, store_history=True)
        assert shell.user_ns["answer"] == 42
        if failed:
            shell.run_cell("raise ValueError('private failure detail')", store_history=True)
        else:
            collector.finish()
        result = loads_result(store.files[settings["profile_path"]])
        assert result.root_run.status == ("failed" if failed else "success")
        assert result.root_run.parent_id == "parent"
        assert result.root_run.metadata["correlation_id"] == "invocation"
        assert cells[2].id in result.sources
        assert any(
            line.location.source_id == cells[0].id and line.location.line == 2 and line.hits == 1
            for line in result.root_run.lines
        )
        assert not any(INTERNAL_CELL in unit.source for unit in result.sources.values())
        assert collector.session.state == "stopped"
        assert collector._post_cell not in shell.events.callbacks["post_run_cell"]
        original = store.files[settings["profile_path"]]
        collector.finish()
        assert store.files[settings["profile_path"]] == original
    finally:
        collector.finish()


def test_exit_preserves_result_and_restores_descriptor(tmp_path, store, shell):
    """Verify exit preserves result and restores descriptor.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """

    class Notebook:
        """Emulate notebook invocation or exit behavior for integration tests.

        Expose a controlled notebook API for invocation and exit cleanup.

        """

        def exit(self, value):
            """Simulate notebook exit while retaining the original exit value.

            Keep the exit value observable by the surrounding parent invocation.

            """
            return value

    notebook = Notebook()
    shell.user_ns["dbutils"] = SimpleNamespace(notebook=notebook)
    settings = context(tmp_path)
    store.files[settings["profile_path"]] = b""
    collector = start_child(settings)
    try:
        shell.run_cell("answer = 42", store_history=True)
        assert notebook.exit("private return value") == "private return value"
        assert "exit" not in vars(notebook)
        assert collector.session.state == "stopped"
        assert b"private return value" not in store.files[settings["profile_path"]]
    finally:
        collector.finish()


def test_startup_failure_keeps_source_and_releases_partial_session(
    tmp_path, store, shell, monkeypatch
):
    """Verify startup failure keeps source and releases partial session.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    assert shell is not None
    original = ChildCollector.start

    def failing(self):
        """Provide the controlled behavior used by this test.

        Raise a controlled workload error to exercise instrumentation cleanup.

        """
        original(self)
        raise RuntimeError("private startup detail")

    monkeypatch.setattr(ChildCollector, "start", failing)
    settings = context(tmp_path)
    store.files[settings["profile_path"]] = b""
    collector = start_child(settings)
    result = loads_result(store.files[settings["profile_path"]])
    assert result.capabilities.line_time is False
    assert result.capabilities.hit_counts is False
    assert collector.session.state == "stopped"
    assert b"private startup detail" not in store.files[settings["profile_path"]]
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass


@pytest.mark.parametrize(
    "outcome", ["success", "failure", "missing", "wrong_owner", "readonly", "scala"]
)
def test_automatic_parent_merge_preserves_workload_and_source(
    tmp_path, store, monkeypatch, outcome
):
    """Verify automatic parent merge preserves workload and source.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    session = Session(backend="trace", root=str(tmp_path), display="none", spark=False)
    session.result.capabilities = BackendCapabilities(hit_counts=True)
    arguments = {"date": "2026-01-01", "token": "private argument"}
    before = dict(arguments)
    calls = []
    store.reject_notebook = outcome == "readonly"
    store.language = "SCALA" if outcome == "scala" else "PYTHON"

    def run(path, timeout, parameters):
        """Run the controlled workload or simulated notebook invocation.

        Use an in-memory workspace and controlled shell hooks to inspect child
        correlation, source capture, and cleanup without Databricks access.

        """
        calls.append((path, timeout, parameters))
        if "_linescope_" in path and outcome != "missing":
            profile_path = path + ".json"
            sources = notebook_sources(PATH, SOURCE)
            # Recover the generated context without executing any payload.
            bootstrap = re.split(
                r"(?m)^# COMMAND ----------$", store.files[path].decode(), maxsplit=1
            )[0]

            encoded = next(
                node.value
                for node in ast.walk(ast.parse(bootstrap))
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith('{"path":')
            )
            settings = json.loads(encoded)
            result = ProfileResult(
                ProfileRun(
                    source=sources[0],
                    parent_id="wrong" if outcome == "wrong_owner" else settings["parent_id"],
                    metadata={"correlation_id": settings["correlation_id"]},
                    lines=[LineStats(SourceLocation(sources[0].id, 2), 100, 1)],
                ),
                {source.id: source for source in sources},
                "trace",
                BackendCapabilities(hit_counts=True),
            )
            store.files[profile_path] = dumps_result(result)
        if outcome == "failure":
            raise ValueError("private workload failure")
        return "private return value"

    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=run))
    monkeypatch.setattr(
        "linescope.notebooks.databricks.notebook_path", lambda _dbutils: "/Workspace/project/main"
    )
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    try:
        if outcome == "failure":
            with pytest.raises(ValueError, match="private workload failure"):
                dbutils.notebook.run("./child", 60, arguments)
        else:
            assert dbutils.notebook.run("./child", 60, arguments) == "private return value"
    finally:
        adapter.stop()
    assert len(calls) == 1
    assert calls[0][1:] == (60, arguments)
    assert calls[0][2] is arguments
    assert arguments == before
    assert dbutils.notebook.run is run
    child = session.result.root_run.children[0]
    assert child.status == ("failed" if outcome == "failure" else "success")
    assert all(
        unit.source != "# Child notebook source unavailable.\n"
        for unit in session.result.sources.values()
    )
    assert store.files == {PATH: SOURCE.encode()}
    if outcome in {"success", "failure"}:
        assert child.metadata["collection"] == "child profile merged"
        assert child.lines[0].hits == 1
        assert child.metadata["parent_wait_time_ns"] > 0
        # Refresh the parent after the merge without duplicating child data.
        session._backend = SimpleNamespace(result=lambda: RawBackendResult())
        session._refresh()
        assert not session.result.root_run.lines
        assert not session.result.root_run.functions
        assert session.result.root_run.children[0].lines[0].hits == 1
    else:
        assert child.metadata["collection"] == "child source only"
        assert child.lines == []
        assert child.metadata["child_capabilities"]["hit_counts"] is False
        assert session.result.warnings
    html = render_html(session.result)
    document = ReportDOM(html).root
    assert "child_work" in document.text()
    assert "unused = 99" in document.text()
    if outcome not in {"success", "failure"}:
        assert all(header.text() != "Hits" for header in document.find_all("th"))
    assert "private return value" not in html
    assert "private argument" not in html
    assert "private credential detail" not in html


def test_preparation_owns_only_unique_siblings_and_cleanup_reports_failures(tmp_path, store):
    """Check the expected behavior in this regression case.

    Verify preparation owns only unique siblings and cleanup reports failures.

    """
    session = Session(backend="trace", root=str(tmp_path), display="none", spark=False)
    prepared = PreparedChild(session, PATH, "invocation", "parent")
    prepared.prepare()
    assert prepared.path.rsplit("/", 1)[0] == PATH.rsplit("/", 1)[0]
    text = store.files[prepared.path].decode()
    assert SOURCE.removeprefix("# Databricks notebook source\n") in text
    assert INTERNAL_CELL in text
    assert "private argument" not in text
    store.reject_cleanup = True
    prepared.cleanup()
    assert len(session.result.warnings) == 2
    assert all("private credential detail" not in warning for warning in session.result.warnings)
    assert store.files[PATH] == SOURCE.encode()


def test_opt_out_calls_original_without_workspace_operations(tmp_path, store, monkeypatch):
    """Verify opt out calls original without workspace operations.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    session = Session(backend="trace", root=str(tmp_path), child_notebooks=False)
    calls = []
    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=lambda *args: calls.append(args)))
    monkeypatch.setattr(
        "linescope.notebooks.databricks.notebook_path", lambda _dbutils: "/Workspace/project/main"
    )
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    try:
        dbutils.notebook.run("./child", 60, {})
    finally:
        adapter.stop()
    assert calls == [("./child", 60, {})]
    assert store.files == {PATH: SOURCE.encode()}
    assert session.result.root_run.children[0].metadata["collection"] == "parent wait only"


def test_magic_cells_remain_readable_and_unexecuted_cells_are_snapshotted():
    """Verify magic cells remain readable and unexecuted cells are snapshotted.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    source = (
        "# Databricks notebook source\n# MAGIC %run ./common\n"
        "\n# COMMAND ----------\n\n# MAGIC %sql\n# MAGIC SELECT 1\n"
    )
    sources = notebook_sources(PATH, source)
    assert [unit.source for unit in sources] == ["%run ./common", "%sql\nSELECT 1"]
    assert sources[0].path == PATH
    assert sources[1].path == PATH + " · cell 2"


def test_unavailable_sdk_preserves_one_original_call_and_unknown_metrics(tmp_path, monkeypatch):
    """Verify unavailable sdk preserves one original call and unknown metrics.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """

    def unavailable():
        """Simulate a browser failure without replacing the workload error.

        Keep the display error separate from the original workload exception.

        """
        raise ImportError("private SDK detail")

    monkeypatch.setattr("linescope.notebooks.remote.WorkspaceStore", unavailable)
    monkeypatch.setattr("linescope.notebooks.databricks.notebook_path", lambda _dbutils: None)
    session = Session(backend="trace", root=str(tmp_path))
    calls = []
    dbutils = SimpleNamespace(notebook=SimpleNamespace(run=lambda *args: calls.append(args)))
    adapter = DatabricksIntegration(session, dbutils)
    adapter.start()
    try:
        dbutils.notebook.run(PATH, 60, {})
    finally:
        adapter.stop()
    assert calls == [(PATH, 60, {})]
    child = session.result.root_run.children[0]
    assert child.lines == []
    assert child.metadata["child_capabilities"]["hit_counts"] is False
    assert "ImportError" in session.result.warnings[0]
    assert "private SDK detail" not in session.result.warnings[0]


def test_missing_shell_reports_source_only_instead_of_zero_hits(tmp_path, store, monkeypatch):
    """Verify missing shell reports source only instead of zero hits.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    settings = context(tmp_path)
    store.files[settings["profile_path"]] = b""
    start_child(settings)
    result = loads_result(store.files[settings["profile_path"]])
    assert result.capabilities.line_time is False
    assert result.capabilities.hit_counts is False
    assert result.root_run.lines == []
    assert len(result.sources) == 3


def test_completion_hook_failure_still_stops_session(tmp_path, store, shell, monkeypatch):
    """Verify completion hook failure still stops session.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """
    settings = context(tmp_path)
    store.files[settings["profile_path"]] = b""
    collector = start_child(settings)
    original = shell.events.unregister

    def unregister(event, callback):
        """Remove the callback owned by the integration under test.

        Preserve unrelated registrations while removing the requested callback.

        """
        original(event, callback)
        if callback == collector._post_cell:
            raise RuntimeError("private cleanup detail")

    monkeypatch.setattr(shell.events, "unregister", unregister)
    collector.finish()
    assert collector.session.state == "stopped"
    result = loads_result(store.files[settings["profile_path"]])
    assert any("hook cleanup failed" in warning for warning in result.warnings)
    assert all("private cleanup detail" not in warning for warning in result.warnings)
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass


@pytest.mark.parametrize("outcome", ["normal", "exit", "failure"])
def test_generated_notebook_executes_and_merges_real_measurements(
    tmp_path, store, shell, monkeypatch, outcome
):
    """Verify generated notebook executes and merges real measurements.

    Use an in-memory workspace and controlled shell hooks to inspect child
    correlation, source capture, and cleanup without Databricks access.

    """

    class NotebookExit(Exception):
        """Signal a simulated notebook exit while preserving its return value.

        Keep normal notebook exit distinguishable from ordinary cell failures.

        """

    class Notebook:
        """Emulate notebook invocation or exit behavior for integration tests.

        Attributes
        ----------
        calls : list[Any]
            Invocation or exit values retained for passthrough assertions.

        """

        def __init__(self):
            """Initialize the controlled test state and recorded observations.

            Retain only the state needed to observe arguments, results, and
            cleanup in the surrounding test.

            """
            self.calls = 0

        def exit(self, value):
            """Simulate notebook exit while retaining the original exit value.

            Keep the exit value observable by the surrounding parent invocation.

            """
            raise NotebookExit(value)

        def run(self, path, timeout, arguments):
            """Run the controlled workload or simulated notebook invocation.

            Use an in-memory workspace and controlled shell hooks to inspect
            child correlation, source capture, and cleanup without Databricks
            access.

            """
            assert timeout == 60
            assert arguments == {"date": "2026-01-01"}
            self.calls += 1
            source = store.files[path].decode().removeprefix("# Databricks notebook source\n")
            for cell in re.split(r"(?m)^# COMMAND ----------\s*$", source):
                result = shell.run_cell(cell.strip("\n"), store_history=True)
                error = result.error_before_exec or result.error_in_exec
                if isinstance(error, NotebookExit):
                    return error.args[0]
                if error is not None:
                    raise error
            return "normal result"

    original_source = SOURCE
    if outcome != "normal":
        extra = (
            "dbutils.notebook.exit(widget_value)"
            if outcome == "exit"
            else "raise ValueError('child failure')"
        )
        original_source = SOURCE + "\n# COMMAND ----------\n\n" + extra + "\n"
        store.files[PATH] = original_source.encode()
    notebook = Notebook()
    dbutils = SimpleNamespace(notebook=notebook)
    shell.user_ns["dbutils"] = dbutils
    shell.user_ns["widget_value"] = "private exit value"
    monkeypatch.setattr(
        "linescope.notebooks.databricks.notebook_path", lambda _dbutils: "/Workspace/project/main"
    )
    parent = Session(backend="trace", root=str(tmp_path), display="none", spark=False)
    adapter = DatabricksIntegration(parent, dbutils)
    adapter.start()
    try:
        if outcome == "failure":
            with pytest.raises(ValueError, match="child failure"):
                dbutils.notebook.run(PATH, 60, {"date": "2026-01-01"})
        else:
            returned = dbutils.notebook.run(PATH, 60, {"date": "2026-01-01"})
            assert returned == ("private exit value" if outcome == "exit" else "normal result")
    finally:
        adapter.stop()
    assert notebook.calls == 1
    child = parent.result.root_run.children[0]
    assert child.metadata["collection"] == "child profile merged"
    assert child.status == ("failed" if outcome == "failure" else "success")
    assert child.source.path == PATH
    assert any(line.hits == 1 and line.location.line == 2 for line in child.lines)
    assert all(INTERNAL_CELL not in source.source for source in parent.result.sources.values())
    assert store.files == {PATH: original_source.encode()}
    assert "run" not in vars(notebook)
    assert "exit" not in vars(notebook)
    assert all("Trace instrumentation" in warning for warning in parent.result.warnings)
