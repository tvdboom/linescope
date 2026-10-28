"""LineScope.

Author: Mavs
Description: Exercise local AI viewer authorization and request isolation.

"""

from http.client import HTTPConnection
import json
import select
import socket
from threading import Event, Thread

import pytest

from linescope.ai import server
from linescope.ai.agents import LocalAgent, agent_command
from linescope.ai.connections import (
    browser_connections,
    credential_environment,
    describe_connections,
)
from linescope.ai.providers import ProviderError
from linescope.model import (
    BackendCapabilities,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render.html import render_html
from tests.test_render import ReportDOM


def _html(context: str = "{}") -> str:
    """Build a minimal saved report with an embedded context payload.

    Parameters
    ----------
    context : str, default="{}"
        Raw JSON included in the report's inert script element.

    Returns
    -------
    str
        Self-contained report fixture.

    """
    return (
        "<html><head><title>Test</title></head><body><script "
        'id="linescope-ai-context" type="application/json">'
        f"{context}</script></body></html>"
    )


@pytest.fixture
def viewer():
    """Run a loopback viewer and release its socket after every test.

    Yields
    ------
    server._ReportServer
        Live viewer owning a synthetic saved context.

    """
    with server._ReportServer(
        _html('{"captured":"source"}'), 0, ("configured-agent",)
    ) as instance:
        thread = Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            thread.join(timeout=5)


def _request(viewer, path="/", *, method="GET", payload=None, headers=None):
    """Send one isolated request to the local viewer.

    Parameters
    ----------
    viewer : server._ReportServer
        Live fixture with a known session credential.

    path : str, default="/"
        Exact request target.

    method : str, default="GET"
        HTTP method used for the request.

    payload : object | None, default=None
        Value encoded as JSON when supplied.

    headers : dict | None, default=None
        Explicit overrides for request headers.

    Returns
    -------
    tuple
        HTTP status, response headers, and decoded text body.

    """
    connection = HTTPConnection("127.0.0.1", viewer.server_port, timeout=5)
    request_headers = {"X-LineScope-Token": viewer.token, "Content-Type": "application/json"}
    request_headers.update(headers or {})
    try:
        connection.request(
            method, path, None if payload is None else json.dumps(payload), request_headers
        )
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        connection.close()


def test_only_report_root_is_exposed(viewer):
    """Serve the selected report without creating a filesystem browser.

    Include transient capabilities and prevent browsers from caching them.

    """
    status, headers, body = _request(viewer)
    assert status == 200
    assert f'name="linescope-ai-token" content="{viewer.token}"' in body
    assert f'name="linescope-ai-origin" content="{viewer.origin}"' in body
    assert 'name="linescope-ai-agent" content="available"' in body
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers
    assert _request(viewer, "/../pyproject.toml")[0] == 404
    assert _request(viewer, "/?path=secrets")[0] == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.test"},
        {"Origin": "https://evil.test"},
        {"Origin": "null"},
        {"X-LineScope-Token": "wrong"},
        {"X-LineScope-Token": "é"},
    ],
)
def test_inference_rejects_foreign_requests(viewer, headers, monkeypatch):
    """Reject cross-origin, rebinding, and unauthenticated inference.

    Parameters
    ----------
    viewer : server._ReportServer
        Live local viewer fixture.

    headers : dict
        Request header that violates the viewer's access policy.

    monkeypatch : pytest.MonkeyPatch
        Test substitution manager.

    """

    def unexpected(**_kwargs):
        """Fail if an unauthorized request reaches a provider.

        This fixture must remain unreachable for rejected requests.

        """
        pytest.fail("Provider called for a foreign request")

    monkeypatch.setattr(server, "request_suggestion", unexpected)
    assert _request(viewer, "/api/suggest", method="POST", payload={}, headers=headers)[0] == 403


def test_prompt_uses_saved_context_only(viewer, monkeypatch):
    """Build prompts from the viewer-owned snapshot and selection.

    Client-supplied replacement context must never be consulted.

    """
    captured = {}

    def build_prompt(context, source_id, line, **kwargs):
        """Record context ownership and return a controlled prompt.

        The server must supply the stored snapshot, not request JSON.

        """
        captured.update(context=context, source_id=source_id, line=line, options=kwargs)
        return "reviewed source prompt"

    monkeypatch.setattr("linescope.ai.context.build_prompt", build_prompt)
    status, _, body = _request(
        viewer,
        "/api/prompt",
        method="POST",
        payload={
            "source_id": "source-id",
            "line": 3,
            "goal": "memory",
            "problem": "allocation",
            "include_related": False,
            "context": {"captured": "replacement"},
        },
    )
    assert status == 200
    assert json.loads(body) == {"prompt": "reviewed source prompt"}
    assert captured == {
        "context": {"captured": "source"},
        "source_id": "source-id",
        "line": 3,
        "options": {"goal": "memory", "problem": "allocation", "include_related": False},
    }


def test_suggestions_are_returned_as_text(viewer, monkeypatch):
    """Preserve model text in JSON without treating it as markup.

    Pass only the provider contract to transport; extra browser fields vanish.

    """
    captured = {}

    def suggest(**kwargs):
        """Capture the request and return untrusted model output.

        Generated text includes HTML to ensure JSON remains the boundary.

        """
        captured.update(kwargs)
        return "<script>untrusted</script>"

    monkeypatch.setattr(server, "request_suggestion", suggest)
    status, _, body = _request(
        viewer,
        "/api/suggest",
        method="POST",
        payload={
            "provider": "compatible",
            "model": "m",
            "prompt": "p",
            "endpoint": "https://test/chat",
            "api_key": "private",
            "command": ["unapproved"],
        },
    )
    assert status == 200
    assert json.loads(body) == {"suggestion": "<script>untrusted</script>"}
    assert "command" not in captured
    assert "private" not in body


def test_agent_command_is_owned_by_server(viewer, monkeypatch):
    """Ignore any executable requested by browser-controlled data.

    Only the command chosen when starting the viewer may run.

    """
    captured = []

    def suggest(prompt, command, *, cancel_event):
        """Record the exact local agent invocation.

        The synthetic executable is not actually started.

        """
        assert cancel_event is viewer.cancel_event
        captured.append((prompt, command))
        return "agent suggestion"

    monkeypatch.setattr("linescope.ai.agents.request_agent_suggestion", suggest)
    status, _, body = _request(
        viewer,
        "/api/suggest",
        method="POST",
        payload={
            "provider": "agent",
            "prompt": "p",
            "command": ["unapproved"],
        },
    )
    assert status == 200
    assert json.loads(body) == {"suggestion": "agent suggestion"}
    assert captured == [("p", ("configured-agent",))]


def test_provider_failure_is_an_actionable_json_error(viewer, monkeypatch):
    """Return controlled inference diagnostics without leaking input.

    Failed requests must not look like successful suggestions.

    """

    def fail(**_kwargs):
        """Simulate a sanitized provider authentication failure.

        The transport owns sanitization before errors reach this layer.

        """
        raise ProviderError("The provider returned HTTP 401.")

    monkeypatch.setattr(server, "request_suggestion", fail)
    status, _, body = _request(
        viewer, "/api/suggest", method="POST", payload={"api_key": "private"}
    )
    assert status == 502
    assert "HTTP 401" in json.loads(body)["error"]
    assert "private" not in body


@pytest.mark.parametrize("payload", [[], None, {"line": "invalid"}, {"provider": 3}])
def test_invalid_requests_return_errors(viewer, payload):
    """Reject malformed API bodies before reaching external systems.

    Parameters
    ----------
    viewer : server._ReportServer
        Live local viewer fixture.

    payload : object
        Invalid JSON shape or field type.

    """
    route = (
        "/api/suggest" if isinstance(payload, dict) and "provider" in payload else "/api/prompt"
    )
    assert _request(viewer, route, method="POST", payload=payload)[0] in (400, 413)


def test_oversized_request_is_rejected_without_reading(viewer):
    """Refuse a declared body beyond the local viewer's limit.

    The client need not send the oversized body for validation to finish.

    """
    assert (
        _request(
            viewer,
            "/api/suggest",
            method="POST",
            payload={},
            headers={"Content-Length": "1000001"},
        )[0]
        == 413
    )


@pytest.mark.parametrize(
    "html", ["<html></html>", _html("[]"), _html("invalid"), _html() + _html()]
)
def test_saved_report_requires_one_valid_context(html):
    """Reject old, malformed, or ambiguous saved report contexts.

    Parameters
    ----------
    html : str
        Invalid saved report fixture.

    """
    with pytest.raises(ValueError, match="context"):
        server._read_context(html)


def test_viewer_closes_socket_when_interrupted(tmp_path, monkeypatch):
    """Release the listening socket when the foreground viewer stops.

    Capture the real server instance and interrupt before serving requests.

    """
    report = tmp_path / "report.html"
    report.write_text(_html(), encoding="utf-8")
    instances = []

    def interrupt(instance, **_kwargs):
        """Retain the opened socket for cleanup assertions.

        A keyboard interruption represents the user's normal stop action.

        """
        instances.append(instance)
        raise KeyboardInterrupt

    monkeypatch.setattr(server._ReportServer, "serve_forever", interrupt)
    server.serve_report(report, open_browser=False)
    assert len(instances) == 1
    assert instances[0].socket.fileno() == -1


def test_viewer_closes_socket_if_browser_start_fails(tmp_path, monkeypatch):
    """Clean up the server when launching its browser raises an error.

    Startup failures must not leave a background listening socket behind.

    """
    report = tmp_path / "report.html"
    report.write_text(_html(), encoding="utf-8")
    instances = []
    original = server._ReportServer.server_close

    def close(instance):
        """Record closure while preserving the actual socket cleanup.

        This wrapper runs on exceptional context-manager exit.

        """
        instances.append(instance)
        original(instance)

    def fail(_url):
        """Simulate a browser startup failure.

        No external browser is launched during this test.

        """
        raise OSError("browser unavailable")

    monkeypatch.setattr(server._ReportServer, "server_close", close)
    monkeypatch.setattr(server.webbrowser, "open", fail)
    with pytest.raises(OSError, match="browser"):
        server.serve_report(report)
    assert len(instances) == 1
    assert instances[0].socket.fileno() == -1


def test_busy_inference_rejects_duplicate_requests(viewer, monkeypatch):
    """Serialize inference requests and release ownership after failure.

    Retrying while another request is active must not incur duplicate work.

    """
    calls = []

    def suggest(**_kwargs):
        """Record the sole allowed call and simulate provider failure.

        A later retry must remain possible after this failure.

        """
        calls.append("called")
        raise ProviderError("Controlled failure")

    monkeypatch.setattr(server, "request_suggestion", suggest)
    viewer.inference_lock.acquire()
    try:
        status, _, body = _request(viewer, "/api/suggest", method="POST", payload={})
        assert status == 502
        assert "already running" in body
        assert calls == []
    finally:
        viewer.inference_lock.release()
    assert _request(viewer, "/api/suggest", method="POST", payload={})[0] == 502
    assert calls == ["called"]
    assert viewer.inference_lock.acquire(blocking=False)
    viewer.inference_lock.release()


def test_server_shutdown_cancels_active_agent(monkeypatch):
    """Notify an active local agent before joining its request thread.

    The viewer must finish closing only after the agent releases ownership.

    """
    started, finished = Event(), Event()
    results = []

    def suggest(_prompt, _command, *, cancel_event):
        """Wait for viewer shutdown and then release the simulated agent.

        Use bounded waits so a broken cleanup path cannot hang the test.

        """
        started.set()
        assert cancel_event.wait(timeout=5)
        finished.set()
        raise ValueError("The viewer stopped the agent.")

    monkeypatch.setattr("linescope.ai.agents.request_agent_suggestion", suggest)
    instance = server._ReportServer(_html(), 0, ("agent",))
    serving = Thread(target=instance.serve_forever)
    serving.start()

    def request():
        """Run the pending inference request from a separate client.

        Capture its final response once cancellation has completed.

        """
        results.append(
            _request(
                instance,
                "/api/suggest",
                method="POST",
                payload={"provider": "agent", "prompt": "p"},
            )
        )

    client = Thread(target=request)
    client.start()
    try:
        assert started.wait(timeout=5)
        instance.shutdown()
        instance.server_close()
        assert finished.is_set()
    finally:
        instance.cancel_event.set()
        instance.shutdown()
        instance.server_close()
        serving.join(timeout=5)
        client.join(timeout=5)
    assert len(results) == 1
    assert results[0][0] == 502
    assert instance.socket.fileno() == -1


def test_saved_report_line_button_builds_real_prompt(viewer):
    """Round-trip a rendered line selection into its captured AI context.

    Hashes used by the UI must resolve to source snapshots with unknown
    measurements preserved, including source containing markup characters.

    """
    source = SourceUnit(
        "source:///private/main.py", "/private/main.py", "result = expensive()\n# </script>\n"
    )
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(source.id, 1), wall_time_ns=12345)]),
        {source.id: source},
        "trace",
        BackendCapabilities(),
    )
    report = render_html(result)
    viewer.context = server._read_context(report)
    buttons = ReportDOM(report).root.find_all("button", css="ai-line-button")
    assert buttons
    selection = buttons[0].attributes
    status, _, body = _request(
        viewer,
        "/api/prompt",
        method="POST",
        payload={
            "source_id": selection["data-ai-source"],
            "line": int(selection["data-ai-line"]),
            "goal": "both",
            "problem": "Reduce allocations",
        },
    )
    assert status == 200
    prompt = json.loads(body)["prompt"]
    assert "result = expensive()" in prompt
    assert "</script>" in prompt
    assert "12345" in prompt
    assert "null" in prompt
    assert "Reduce allocations" in prompt


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain"},
        {"Transfer-Encoding": "chunked"},
        {"Content-Length": "bad"},
    ],
)
def test_bad_request_framing_is_rejected(viewer, headers):
    """Refuse ambiguous body framing before parsing or invoking providers.

    Parameters
    ----------
    viewer : server._ReportServer
        Live local viewer fixture.

    headers : dict
        Invalid content type, transfer mode, or length header.

    """
    assert _request(viewer, "/api/suggest", method="POST", payload={}, headers=headers)[0] == 400


@pytest.mark.parametrize(("body", "declared_length"), [(b"{", 1), (b"{}", 10), (b"null", 4)])
def test_invalid_or_truncated_json_is_rejected(viewer, body, declared_length):
    """Reject invalid JSON and short reads with controlled responses.

    Parameters
    ----------
    viewer : server._ReportServer
        Live local viewer fixture.

    body : bytes
        Deliberately invalid or incomplete request payload.

    declared_length : int
        Length sent by the client, possibly larger than the actual body.

    """
    connection = HTTPConnection("127.0.0.1", viewer.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/suggest",
            body,
            {
                "X-LineScope-Token": viewer.token,
                "Content-Type": "application/json",
                "Content-Length": str(declared_length),
            },
        )
        connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        assert response.status == 400
        assert "error" in json.loads(response.read())
    finally:
        connection.close()


def test_unconfigured_agent_and_shutdown_have_controlled_errors(viewer):
    """Explain absent agent configuration and refuse inference during shutdown.

    Neither case is allowed to create a child process or network request.

    """
    viewer.agent_command = ()
    status, _, body = _request(
        viewer, "/api/suggest", method="POST", payload={"provider": "agent"}
    )
    assert status == 502
    assert "--ai-agent" in body
    viewer.cancel_event.set()
    status, _, body = _request(
        viewer, "/api/suggest", method="POST", payload={"provider": "agent"}
    )
    assert status == 502
    assert "shutting down" in body


def test_unknown_api_routes_and_foreign_report_hosts_are_rejected(viewer):
    """Restrict both API routing and report reads to the authorized viewer.

    The read route uses the same hostname policy as inference requests.

    """
    assert _request(viewer, "/api/unknown", method="POST", payload={})[0] == 404
    assert _request(viewer, headers={"Host": "foreign.test"})[0] == 403


@pytest.mark.parametrize("port", [True, -1, 65536, "8080"])
def test_invalid_viewer_ports_fail_before_reading_a_report(port):
    """Validate explicit listener configuration before opening any files.

    Parameters
    ----------
    port : object
        Out-of-range or incorrectly typed listener port.

    """
    with pytest.raises(ValueError, match="port must"):
        server.serve_report("report-does-not-exist.html", port=port, open_browser=False)


def test_rejected_headers_keep_response_when_body_arrives_later(viewer):
    """Deliver authorization errors even when the body arrives after headers.

    Wait for the server response before sending the body. Closing the socket
    without draining that body can otherwise reset the response on Windows.

    """
    headers = (
        "POST /api/suggest HTTP/1.1\r\n"
        f"Host: {viewer.authority}\r\n"
        "Origin: null\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 2\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", viewer.server_port), timeout=5) as client:
        client.sendall(headers.encode("ascii"))
        assert select.select([client], [], [], 5)[0]
        client.sendall(b"{}")
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while chunk := client.recv(65_536):
            response.extend(chunk)
    head, body = bytes(response).split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.0 403")
    assert json.loads(body)["error"] == "Requests must come from this report viewer."


def test_agent_temporary_file_failure_has_controlled_error(viewer, monkeypatch):
    """Convert local temporary-file failures into an actionable diagnostic.

    Filesystem exceptions may include source or private paths, so their raw
    messages must not be returned to the client or escape the HTTP handler.

    """

    def fail(_prompt, _command, **_kwargs):
        """Simulate a filesystem error containing private diagnostic data.

        The local agent never starts in this isolated failure case.

        """
        raise OSError("private-source-path")

    monkeypatch.setattr("linescope.ai.agents.request_agent_suggestion", fail)
    status, _, body = _request(
        viewer, "/api/suggest", method="POST", payload={"provider": "agent", "prompt": "p"}
    )
    assert status == 502
    assert "temporary working files" in body
    assert "private-source-path" not in body


@pytest.fixture
def clear_connection_keys(monkeypatch):
    """Isolate connection discovery from credentials in the test environment.

    Connection descriptors may expose presence flags, never actual values.

    """
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "LINESCOPE_AI_API_KEY",
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clear_connection_keys")
@pytest.mark.parametrize(
    ("keys", "default", "configured"),
    [
        ({}, None, set()),
        ({"OPENAI_API_KEY": "  ", "LINESCOPE_AI_API_KEY": "\t"}, None, set()),
        ({"OPENAI_API_KEY": "secret-one"}, "openai", {"openai"}),
        ({"ANTHROPIC_API_KEY": "secret-three"}, "anthropic", {"anthropic"}),
        ({"LINESCOPE_AI_API_KEY": "secret-two"}, "compatible", {"compatible"}),
        (
            {"OPENAI_API_KEY": "secret-one", "LINESCOPE_AI_API_KEY": "secret-two"},
            None,
            {"openai", "compatible"},
        ),
        (
            {
                "COPILOT_GITHUB_TOKEN": "secret-four",
                "CODEX_API_KEY": "secret-five",
            },
            None,
            set(),
        ),
        (
            {"OPENAI_API_KEY": "secret-one", "ANTHROPIC_API_KEY": "secret-three"},
            None,
            {"openai", "anthropic"},
        ),
    ],
)
def test_connection_metadata_has_no_vendor_default_or_secrets(
    keys, default, configured, monkeypatch
):
    """Select a sole configured API without preferring a vendor.

    Keys for local agents cannot authorize launching a command that was not
    configured by the viewer owner. Served metadata contains no key values.

    """
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    with server._ReportServer(_html(), 0) as instance:
        report = instance.report.decode()
        metadata = ReportDOM(report).root.find_all("meta", name="linescope-ai-connections")
        assert len(metadata) == 1
        descriptor = json.loads(metadata[0].attributes["content"])
        assert descriptor["default_connection"] == default
        assert {
            entry["id"] for entry in descriptor["connections"] if entry["configured"]
        } == configured
        assert {entry["id"] for entry in descriptor["connections"] if entry["available"]} == {
            "anthropic",
            "openai",
            "compatible",
        }
        assert {entry["id"] for entry in descriptor["connections"]} == {
            "anthropic",
            "claude",
            "codex",
            "copilot",
            "compatible",
            "agent",
            "openai",
        }
        for entry in descriptor["connections"]:
            assert set(entry) == {
                "id",
                "label",
                "kind",
                "configured",
                "available",
                "requires_model",
                "endpoint",
                "custom_endpoint",
                "protocol",
            }
        assert "secret-" not in report
        assert not any(name in report for name in keys)
    assert "linescope-ai-connections" not in _html()


@pytest.mark.usefixtures("clear_connection_keys")
@pytest.mark.parametrize("agent", list(LocalAgent))
def test_explicit_agent_wins_over_multiple_configured_apis(agent, monkeypatch):
    """Honor the user's explicit CLI choice when API credentials also exist.

    Every named agent follows the same descriptor and dispatch capability.

    """
    monkeypatch.setenv("OPENAI_API_KEY", "private-one")
    monkeypatch.setenv("LINESCOPE_AI_API_KEY", "private-two")
    descriptor = describe_connections(agent_command(agent))
    assert descriptor["default_connection"] == agent.value
    entries = {entry["id"]: entry for entry in descriptor["connections"]}
    assert entries[agent.value]["available"]
    assert entries[agent.value]["configured"]
    assert entries[agent.value]["kind"] == "agent"
    assert not entries[agent.value]["requires_model"]
    assert {
        entry["id"]
        for entry in descriptor["connections"]
        if entry["kind"] == "agent" and entry["available"]
    } == {agent.value}


@pytest.mark.usefixtures("clear_connection_keys")
def test_custom_command_is_not_misidentified_as_a_named_agent():
    """Recognize presets only when the full server-owned command matches.

    A command named codex with different arguments retains custom semantics.

    """
    descriptor = describe_connections(("codex", "custom-option", "private-command-argument"))
    assert descriptor["default_connection"] == "agent"
    entries = {entry["id"]: entry for entry in descriptor["connections"]}
    assert entries["agent"]["configured"]
    assert not entries["codex"]["available"]
    assert "private-command-argument" not in json.dumps(descriptor)


@pytest.mark.parametrize("agent", list(LocalAgent))
def test_named_agent_dispatch_uses_only_its_configured_command(viewer, monkeypatch, agent):
    """Route native request credentials through server-owned environment names.

    Browser command and environment overrides are ignored. Named connections
    never select another local executable merely because a key is present.

    """
    viewer.agent_command = agent_command(agent)
    captured = []

    def suggest(prompt, command, **kwargs):
        """Record the native agent request without launching an executable.

        The recorder captures the private key only inside the test process.

        """
        captured.append((prompt, command, kwargs))
        return "native suggestion"

    monkeypatch.setattr("linescope.ai.agents.request_agent_suggestion", suggest)
    status, _, body = _request(
        viewer,
        "/api/suggest",
        method="POST",
        payload={
            "provider": agent.value,
            "prompt": "captured code",
            "model": "chosen-model",
            "api_key": "request-secret",
            "command": ["other-executable"],
            "credential_env": "UNAPPROVED",
        },
    )
    assert status == 200
    assert json.loads(body) == {"suggestion": "native suggestion"}
    assert captured == [
        (
            "captured code",
            agent_command(agent),
            {
                "cancel_event": viewer.cancel_event,
                "api_key": "request-secret",
                "credential_env": credential_environment(agent.value),
                "model": "chosen-model",
            },
        )
    ]
    assert "request-secret" not in viewer.report.decode()


@pytest.mark.parametrize("provider", ["codex", "claude", "copilot"])
def test_browser_cannot_start_an_unconfigured_named_agent(viewer, monkeypatch, provider):
    """Reject named agents when only a custom server command is configured.

    A supplied key cannot grant permission to run a different executable.

    """

    def unexpected(*_args, **_kwargs):
        """Fail if an unconfigured agent reaches execution.

        Configuration validation must stop the request before this boundary.

        """
        pytest.fail("Unconfigured agent executed")

    monkeypatch.setattr("linescope.ai.agents.request_agent_suggestion", unexpected)
    status, _, body = _request(
        viewer,
        "/api/suggest",
        method="POST",
        payload={
            "provider": provider,
            "prompt": "captured",
            "api_key": "request-secret",
        },
    )
    assert status == 502
    assert "not configured" in body
    assert "request-secret" not in body


def test_browser_catalog_is_pure_and_presets_cannot_be_overridden(monkeypatch):
    """Create static connection metadata without inspecting local credentials.

    The browser receives only preset URLs and protocol capabilities. Mutating
    one returned catalog must not affect a later report's connection choices.

    """

    class ForbiddenEnvironment(dict):
        """Reject attempts to read credentials while generating static HTML.

        A saved report must behave identically regardless of the host's keys.

        """

        def get(self, _key, _default=None):
            """Fail if static connection discovery reads process state.

            Keep the regression independent of the machine's real credentials.

            """
            pytest.fail("Static catalog inspected the environment")

    monkeypatch.setattr("linescope.ai.connections.os.environ", ForbiddenEnvironment())
    catalog = browser_connections()
    entries = {entry["id"]: entry for entry in catalog["connections"]}
    assert catalog["default_connection"] is None
    assert not any(entry["configured"] for entry in entries.values())
    assert {identity for identity, entry in entries.items() if entry["available"]} == {
        "anthropic",
        "openai",
        "compatible",
    }
    assert {identity for identity, entry in entries.items() if entry["custom_endpoint"]} == {
        "compatible"
    }
    assert entries["openai"]["endpoint"] == "https://api.openai.com/v1/responses"
    assert entries["openai"]["protocol"] == "responses"
    assert entries["compatible"]["protocol"] == "chat-completions"
    assert entries["anthropic"]["endpoint"] == "https://api.anthropic.com/v1/messages"
    assert entries["anthropic"]["protocol"] == "anthropic-messages"
    assert entries["anthropic"]["label"] == "Claude"
    assert entries["claude"]["label"] == "Claude Code"
    entries["openai"]["endpoint"] = "https://untrusted.test"
    assert "untrusted" not in json.dumps(browser_connections())


def test_serving_a_saved_viewer_copy_prioritizes_fresh_capabilities():
    """Bind reopened viewer copies to their current listener and session.

    Browser Save Page copies can retain old viewer metadata. Fresh metadata
    must win the UI's first-element lookup while preserving early UTF-8.

    """
    original = _html().replace("<head>", '<head><meta charset="utf-8">')
    with server._ReportServer(original, 0) as first:
        saved = first.report.decode()
        with server._ReportServer(saved, 0, ("configured-agent",)) as second:
            reopened = second.report.decode()
            document = ReportDOM(reopened).root
            assert (
                document.find_all("meta", name="linescope-ai-token")[0].attributes["content"]
                == second.token
            )
            assert (
                document.find_all("meta", name="linescope-ai-origin")[0].attributes["content"]
                == second.origin
            )
            metadata = document.find_all("meta", name="linescope-ai-connections")[0]
            assert json.loads(metadata.attributes["content"])["default_connection"] == "agent"
            assert second.token != first.token
            assert second.origin != first.origin
            assert reopened.index('<meta charset="utf-8">') < 1024
