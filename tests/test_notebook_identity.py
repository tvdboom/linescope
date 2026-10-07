"""LineScope.

Author: Mavs
Description: Verify notebook execution identity and report location labels.

"""

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import psutil
import pytest
from pytest_mock import MockerFixture

from linescope import Session
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    MemorySample,
    ProcessMemoryStats,
    SourceLocation,
    SparkExecution,
)
from linescope.notebooks.identity import (
    _connection_file,
    _executed_notebook_path,
    _kernel_notebook_path,
    _server_notebook_path,
)
from linescope.notebooks.ipython import NotebookIntegration
from linescope.render import render_html
from linescope.render.cell import render_cell_summary
from tests.test_render import ReportDOM


@pytest.fixture
def server_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    """Provide one local Jupyter server and isolate its runtime directory.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory containing only this test's server metadata.

    monkeypatch : pytest.MonkeyPatch
        Owned runtime-directory replacement restored after the test.

    Returns
    -------
    tuple[Path, dict[str, str]]
        Kernel connection path and writable local server metadata.

    """
    monkeypatch.setattr("jupyter_core.paths.jupyter_runtime_dir", lambda: str(tmp_path))
    server = {
        "url": "http://localhost:8888/prefix/",
        "token": "private-server-token",
        "root_dir": str(tmp_path / "project"),
    }
    (tmp_path / "jpserver-1.json").write_text(json.dumps(server), encoding="utf-8")
    return tmp_path / "kernel-running-id.json", server


def test_kernel_session_names_every_report_location(
    server_context: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
):
    """Carry a kernel-resolved filename through every report location.

    Resolve the notebook without namespace or environment filename metadata.
    Keep exact source links and use the same identity in Files, Functions,
    Memory, Spark, Overview, and the grouped source title.

    Parameters
    ----------
    server_context : tuple[Path, dict[str, str]]
        Isolated Jupyter kernel and local server ownership metadata.

    monkeypatch : pytest.MonkeyPatch
        Owned connection lookup and environment replacements.

    mocker : pytest_mock.MockerFixture
        Offline session API response and request recorder.

    """
    connection, server = server_context
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    monkeypatch.setattr("linescope.notebooks.identity._connection_file", lambda _shell: connection)
    response = BytesIO(
        json.dumps(
            [
                {"kernel": {"id": "another-id"}, "path": "unrelated.ipynb"},
                {"kernel": {"id": "running-id"}, "path": "example.ipynb"},
            ]
        ).encode()
    )
    request = mocker.patch("linescope.notebooks.identity.urlopen", return_value=response)
    session = Session(backend="trace", display="none", spark=False)
    notebook = NotebookIntegration(session, SimpleNamespace(user_ns={}))
    unit = notebook.capture("def build():\n    return 42\nvalue = build()", cell_id="demo")
    expected = str(Path(server["root_dir"]) / "example.ipynb")
    assert notebook.path == expected
    assert unit.path == f"{expected} · cell demo"
    call = request.call_args
    assert call.args[0].full_url == "http://localhost:8888/prefix/api/sessions"
    assert call.args[0].get_header("Authorization") == "token private-server-token"
    assert call.kwargs == {"timeout": 1}
    assert response.closed

    location = SourceLocation(unit.id, 2)
    session.result.capabilities = BackendCapabilities(hit_counts=True, memory=True)
    session.result.root_run.lines = [
        LineStats(location, 1000, hits=1, ram=ProcessMemoryStats(200, 100, 200))
    ]
    session.result.root_run.functions = [FunctionStats(unit.id, "build", 1, 1000, 1)]
    session.result.root_run.memory_samples = [MemorySample(0, 200, location)]
    session.result.root_run.spark_executions = [SparkExecution("action", location=location)]
    html = render_html(session.result)
    assert "interactive" not in html
    assert "private-server-token" not in html
    document = ReportDOM(html).root
    for page_id in ("overview", "files", "functions", "memory", "spark"):
        page = document.find_all("section", id=page_id)[0]
        assert any("example.ipynb" in link.text() for link in page.find_all("a"))
        assert all(expected not in link.text() for link in page.find_all("a"))
    page = document.find_all("section", css="source-page")[0]
    assert page.find_all("h1")[0].text() == "example.ipynb"
    targets = {row.attributes["id"] for row in page.find_all("tr", css="source-row")}
    for link in document.find_all("a"):
        href = link.attributes.get("href") or ""
        if "example.ipynb" in link.text() and href.startswith("#source-"):
            assert href[1:] in targets
    summary = ReportDOM(render_cell_summary(session.result)).root
    assert [
        row.find_all("td")[0].attributes["title"]
        for row in summary.find_all("tbody")[0].find_all("tr")
    ] == [f"example.ipynb · cell demo:{number}" for number in (1, 2, 3)]
    assert unit.path == f"{expected} · cell demo"


@pytest.mark.parametrize(
    "payload",
    [
        b"invalid json",
        b"{}",
        b"[null, 1, {}]",
        b'[{"kernel":{"id":"other"},"path":"other.ipynb"}]',
        b'[{"kernel":{"id":"running-id"},"path":"console.py"}]',
        (
            b'[{"kernel":{"id":"running-id"},"path":"one.ipynb"},'
            b'{"kernel":{"id":"running-id"},"path":"two.ipynb"}]'
        ),
    ],
)
def test_unavailable_or_ambiguous_sessions_do_not_guess(
    server_context: tuple[Path, dict[str, str]], mocker: MockerFixture, payload: bytes
):
    """Leave notebook identity unavailable for invalid or ambiguous sessions.

    Parameters
    ----------
    server_context : tuple[Path, dict[str, str]]
        Isolated kernel and local server metadata.

    mocker : pytest_mock.MockerFixture
        Offline API replacement with cleanup observations.

    payload : bytes
        Invalid, unmatched, non-notebook, or ambiguous API response.

    """
    response = BytesIO(payload)
    mocker.patch("linescope.notebooks.identity.urlopen", return_value=response)
    assert _server_notebook_path(server_context[0]) is None
    assert response.closed


def test_restricted_server_does_not_interrupt_collection(
    server_context: tuple[Path, dict[str, str]], mocker: MockerFixture
):
    """Keep notebook capture usable when the local server rejects requests.

    Parameters
    ----------
    server_context : tuple[Path, dict[str, str]]
        Isolated kernel and local server metadata.

    mocker : pytest_mock.MockerFixture
        Controlled connection failure and process lookup fallback.

    """
    connection, _ = server_context
    mocker.patch("linescope.notebooks.identity._connection_file", return_value=connection)
    mocker.patch("linescope.notebooks.identity.urlopen", side_effect=URLError("restricted"))
    mocker.patch("linescope.notebooks.identity._executed_notebook_path", return_value=None)
    assert _kernel_notebook_path(SimpleNamespace()) is None


@pytest.mark.parametrize("url", ["https://remote.example/", "file:///tmp/", "malformed"])
def test_runtime_lookup_uses_only_local_servers(
    server_context: tuple[Path, dict[str, str]], mocker: MockerFixture, url: str
):
    """Keep server authentication confined to local Jupyter runtime endpoints.

    Parameters
    ----------
    server_context : tuple[Path, dict[str, str]]
        Isolated kernel and metadata file to update.

    mocker : pytest_mock.MockerFixture
        Request recorder that must remain unused.

    url : str
        Remote or invalid endpoint whose metadata must be ignored.

    """
    connection, server = server_context
    server["url"] = url
    (connection.parent / "jpserver-1.json").write_text(json.dumps(server), encoding="utf-8")
    request = mocker.patch("linescope.notebooks.identity.urlopen")
    assert _server_notebook_path(connection) is None
    request.assert_not_called()


@pytest.mark.parametrize("multiple", [False, True])
def test_nbconvert_identity_uses_launcher_input_and_working_directory(
    tmp_path: Path, mocker: MockerFixture, *, multiple: bool
):
    """Resolve batch notebook input without guessing from the kernel directory.

    Parameters
    ----------
    tmp_path : Path
        Execution owner's directory containing notebook input files.

    mocker : pytest_mock.MockerFixture
        Owned process ancestry replacement.

    multiple : bool
        Whether multiple notebook inputs make the launching command ambiguous.

    """
    notebook = tmp_path / "example.ipynb"
    notebook.write_text("{}", encoding="utf-8")
    other = tmp_path / "other.ipynb"
    other.write_text("{}", encoding="utf-8")
    arguments = ["python", "-m", "nbconvert", "--output", "other.ipynb", "example.ipynb"]
    if multiple:
        arguments.append("other.ipynb")
    launcher = SimpleNamespace(
        cmdline=lambda: arguments, cwd=lambda: str(tmp_path), parent=lambda: None
    )
    wrapper = SimpleNamespace(cmdline=lambda: ["python"], parent=lambda: launcher)
    process = mocker.patch("linescope.notebooks.identity.psutil.Process")
    process.return_value.parent.return_value = wrapper
    assert _executed_notebook_path() == (None if multiple else str(notebook))


def test_non_kernel_shell_and_unavailable_process_keep_identity_unknown(
    mocker: MockerFixture,
):
    """Avoid starting kernels or failing collection for unavailable owners.

    Parameters
    ----------
    mocker : pytest_mock.MockerFixture
        Controlled kernel absence and denied process access.

    """
    get_connection = mocker.patch("ipykernel.get_connection_file", side_effect=RuntimeError)
    assert _connection_file(SimpleNamespace()) is None
    get_connection.assert_not_called()
    assert _connection_file(SimpleNamespace(kernel=object())) is None
    mocker.patch("linescope.notebooks.identity.psutil.Process", side_effect=psutil.AccessDenied)
    assert _executed_notebook_path() is None


def test_batch_kernel_without_server_uses_execution_owner(mocker: MockerFixture, tmp_path: Path):
    """Resolve batch kernels whose temporary connection names lack a server ID.

    Parameters
    ----------
    mocker : pytest_mock.MockerFixture
        Owned kernel connection and executor lookup replacements.

    tmp_path : Path
        Temporary connection path without a Jupyter server kernel prefix.

    """
    connection = tmp_path / "temporary-connection.json"
    mocker.patch("ipykernel.get_connection_file", return_value=str(connection))
    owner = mocker.patch(
        "linescope.notebooks.identity._executed_notebook_path", return_value="example.ipynb"
    )
    shell = SimpleNamespace(kernel=object())
    assert _connection_file(shell) == connection
    assert _kernel_notebook_path(shell) == "example.ipynb"
    owner.assert_called_once_with()
