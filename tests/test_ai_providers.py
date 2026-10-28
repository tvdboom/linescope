"""LineScope.

Author: Mavs
Description: Verify provider request schemas, secrets, and failure handling.

"""

from http.client import IncompleteRead
from io import BytesIO
import json
from urllib.error import HTTPError, URLError

import pytest

from linescope.ai import providers


@pytest.fixture
def transport(monkeypatch):
    """Capture HTTP requests while supplying controlled JSON responses.

    Return mutable state so each test can change the body or failure.

    """
    state = {"body": b"{}", "requests": [], "error": None}

    class Opener:
        """Replace networking with a capture of the outgoing request.

        No sockets or external credentials are used by this test double.

        """

        def open(self, request, timeout):
            """Record request headers and return the configured response.

            Failures happen before a response stream is created.

            """
            state["requests"].append((request, timeout))
            if state["error"] is not None:
                raise state["error"]
            return BytesIO(state["body"])

    def build(*handlers):
        """Require the credential-preserving no-redirect policy.

        A missing policy fails the test before returning the fake transport.

        """
        assert len(handlers) == 1
        assert isinstance(handlers[0], providers._NoRedirect)
        return Opener()

    monkeypatch.setattr(providers, "build_opener", build)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LINESCOPE_AI_API_KEY", raising=False)
    return state


def test_openai_uses_responses_and_disables_storage(transport, monkeypatch):
    """Send captured source using the Responses schema with private settings.

    Read environment credentials only for this individual request.

    """
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    transport["body"] = json.dumps(
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Suggestion"}]},
            ],
        }
    ).encode()
    assert (
        providers.request_suggestion(provider="openai", model="my-model", prompt="source")
        == "Suggestion"
    )
    request, timeout = transport["requests"][0]
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.headers["Authorization"] == "Bearer environment-secret"
    assert json.loads(request.data) == {"model": "my-model", "input": "source", "store": False}
    assert timeout == 60


def test_compatible_local_model_can_omit_credentials(transport):
    """Allow a local model without assuming a cloud API key exists.

    Use the Chat Completions schema and preserve generated code as text.

    """
    transport["body"] = b'{"choices":[{"message":{"content":"<script>code</script>"}}]}'
    assert (
        providers.request_suggestion(
            provider="compatible",
            model="local",
            prompt="snapshot",
            endpoint="http://127.0.0.1:1234/v1/chat/completions",
        )
        == "<script>code</script>"
    )
    request, _ = transport["requests"][0]
    assert "Authorization" not in request.headers
    assert json.loads(request.data) == {
        "model": "local",
        "messages": [{"role": "user", "content": "snapshot"}],
        "stream": False,
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/chat",
        "https://secret@example.com/chat",
        "https://example.com/chat?key=secret",
        "https://example.com/chat#secret",
        "file:///secrets",
        "https://example.com:bad/chat",
        "https://example.com:0/chat",
    ],
)
def test_rejects_unsafe_endpoints_before_networking(endpoint, transport):
    """Keep credentials away from invalid or unencrypted remote URLs.

    Parameters
    ----------
    endpoint : str
        Invalid URL that must never reach the transport.

    transport : dict
        Isolated request recorder fixture.

    """
    with pytest.raises(providers.ProviderError):
        providers.request_suggestion(
            provider="compatible", model="m", prompt="p", endpoint=endpoint
        )
    assert transport["requests"] == []


@pytest.mark.parametrize(
    "body",
    [b"not JSON", b"[]", b'{"error":{"message":"secret"}}', b"{}", b"x" * 1_000_001],
    ids=["invalid-json", "list", "error", "empty", "oversized"],
)
def test_malformed_provider_bodies_are_safe_errors(body, transport):
    """Do not expose response bodies or mistake missing text for success.

    Parameters
    ----------
    body : bytes
        Invalid or oversized upstream response.

    transport : dict
        Isolated request recorder fixture.

    """
    transport["body"] = body
    with pytest.raises(providers.ProviderError) as error:
        providers.request_suggestion(
            provider="compatible", model="m", prompt="p", endpoint="https://example.com/chat"
        )
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "error",
    [
        URLError("secret"),
        TimeoutError("secret"),
        IncompleteRead(b"secret"),
        HTTPError("secret", 401, "secret", {}, BytesIO(b"secret")),
    ],
)
def test_transport_failures_hide_provider_secrets(error, transport):
    """Replace upstream errors with controlled diagnostics.

    Parameters
    ----------
    error : Exception
        Transport failure containing simulated private data.

    transport : dict
        Isolated request recorder fixture.

    """
    transport["error"] = error
    with pytest.raises(providers.ProviderError) as caught:
        providers.request_suggestion(
            provider="compatible", model="m", prompt="p", endpoint="https://example.com/chat"
        )
    assert "secret" not in str(caught.value)


def test_redirects_are_not_followed():
    """Never copy bearer headers to a redirect target.

    The handler intentionally returns no replacement request.

    """
    assert (
        providers._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.test")
        is None
    )


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("openai", {"status": "incomplete", "output": []}),
        (
            "compatible",
            {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        ),
    ],
)
def test_incomplete_suggestions_do_not_look_successful(provider, payload):
    """Surface truncated or unfinished replies as failures.

    Parameters
    ----------
    provider : str
        API protocol under test.

    payload : dict
        Response indicating an incomplete model generation.

    """
    with pytest.raises(providers.ProviderError, match="did not complete"):
        providers._response_text(payload, providers.Provider(provider))


def test_explicit_key_overrides_environment(transport, monkeypatch):
    """Use only the request's chosen credential when one is supplied.

    Separate providers do not inherit the OpenAI environment credential.

    """
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")
    monkeypatch.setenv("LINESCOPE_AI_API_KEY", "fallback")
    transport["body"] = b'{"choices":[{"message":{"content":"ok"}}]}'
    providers.request_suggestion(
        provider="compatible",
        model="m",
        prompt="p",
        endpoint="https://example.com/chat",
        api_key="explicit",
    )
    assert transport["requests"][0][0].headers["Authorization"] == "Bearer explicit"


@pytest.mark.parametrize(
    "settings",
    [
        {"provider": "other"},
        {"model": ""},
        {"prompt": ""},
        {"prompt": "x" * 500_001},
        {"api_key": "key\r\ninjection"},
    ],
)
def test_invalid_settings_never_reach_network(settings, transport):
    """Validate user settings before opening an upstream connection.

    Parameters
    ----------
    settings : dict
        Invalid field overrides for a valid baseline request.

    transport : dict
        Isolated request recorder fixture.

    """
    arguments = {
        "provider": "compatible",
        "model": "m",
        "prompt": "p",
        "endpoint": "https://example.com/chat",
    }
    arguments.update(settings)
    with pytest.raises(providers.ProviderError):
        providers.request_suggestion(**arguments)
    assert transport["requests"] == []


def test_openai_requires_a_key(transport):
    """Explain missing credentials before transmitting captured source.

    No provider request should be attempted without OpenAI authentication.

    """
    with pytest.raises(providers.ProviderError, match="API key"):
        providers.request_suggestion(provider="openai", model="m", prompt="p")
    assert transport["requests"] == []


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("openai", {"output": {"message": "invalid"}}),
        ("openai", {"output": [{"type": "message", "content": {"text": "invalid"}}]}),
        ("compatible", {"choices": [{"message": {"content": [{"text": "unsupported"}]}}]}),
    ],
)
def test_invalid_provider_text_shapes_are_not_suggestions(provider, payload):
    """Reject nontext content instead of fabricating an assistant response.

    Parameters
    ----------
    provider : str
        Inference protocol for the response fixture.

    payload : dict
        Structurally invalid model output that must not be displayed.

    """
    with pytest.raises(providers.ProviderError, match="no suggestion text"):
        providers._response_text(payload, providers.Provider(provider))


def test_preset_endpoint_cannot_forward_a_key_to_another_host(transport):
    """Reject endpoint overrides for a named API connection before sending.

    Custom endpoints require explicitly selecting the custom API connection.

    """
    with pytest.raises(providers.ProviderError, match="preset endpoint"):
        providers.request_suggestion(
            provider="openai",
            model="model",
            prompt="captured",
            api_key="private-key",
            endpoint="https://other-host.test/v1/responses",
        )
    assert not transport["requests"]


def test_provider_cannot_echo_the_active_credential(transport):
    """Redact the credential if an upstream suggestion echoes request headers.

    Treat model output as text without reflecting the connection's secret.

    """
    transport["body"] = b'{"choices":[{"message":{"content":"Use private-key here"}}]}'
    suggestion = providers.request_suggestion(
        provider="compatible",
        model="model",
        prompt="captured",
        api_key="private-key",
        endpoint="https://example.test/v1/chat/completions",
    )
    assert suggestion == "Use [redacted credential] here"


def test_anthropic_uses_messages_preset_and_versioned_authentication(transport, monkeypatch):
    """Send Claude suggestions through its native Messages API protocol.

    Preserve only final text blocks, bound generated tokens, and read the
    configured key without adding browser-only headers to server requests.

    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-secret")
    transport["body"] = json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "internal text"},
                {"type": "text", "text": "Use a generator."},
                {"type": "text", "text": "Keep claude-secret private."},
            ],
        }
    ).encode()
    suggestion = providers.request_suggestion(
        provider="anthropic", model="chosen-claude", prompt="captured code"
    )
    assert suggestion == "Use a generator.\nKeep [redacted credential] private."
    request, timeout = transport["requests"][0]
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    headers = {name.lower(): value for name, value in request.headers.items()}
    assert headers["authorization"] == "Bearer claude-secret"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "anthropic-dangerous-direct-browser-access" not in headers
    assert json.loads(request.data) == {
        "model": "chosen-claude",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "captured code"}],
        "stream": False,
    }
    assert timeout == 60


@pytest.mark.parametrize(
    "stop_reason",
    [None, "max_tokens", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"],
)
def test_incomplete_claude_output_is_not_a_finished_suggestion(stop_reason):
    """Reject truncated, paused, refused, or tool-dependent Claude replies.

    The Messages API guarantees a nonnull final stop reason for a completed
    nonstreaming reply. Partial text must not be presented as a final patch.

    """
    with pytest.raises(providers.ProviderError, match="did not complete"):
        providers._response_text(
            {
                "stop_reason": stop_reason,
                "content": [{"type": "text", "text": "partial"}],
            },
            providers.Provider.ANTHROPIC,
        )


@pytest.mark.parametrize(
    "content",
    [None, {}, [], [{"type": "thinking", "thinking": "private"}], [{"type": "text", "text": 1}]],
)
def test_claude_requires_final_text_blocks(content):
    """Ignore nontext Claude content and reject missing suggestion text.

    Thinking and tool metadata must never be used as the optimization reply.

    """
    with pytest.raises(providers.ProviderError, match="no suggestion text"):
        providers._response_text(
            {"stop_reason": "end_turn", "content": content}, providers.Provider.ANTHROPIC
        )


def test_claude_requires_a_key_and_rejects_endpoint_override(transport):
    """Keep Claude credentials restricted to its built-in API endpoint.

    Fail before networking when authentication is absent or the named preset
    is redirected to a browser-supplied host.

    """
    with pytest.raises(providers.ProviderError, match="ANTHROPIC_API_KEY"):
        providers.request_suggestion(provider="anthropic", model="claude", prompt="code")
    with pytest.raises(providers.ProviderError, match="preset endpoint"):
        providers.request_suggestion(
            provider="anthropic",
            model="claude",
            prompt="code",
            api_key="claude-secret",
            endpoint="https://other.test/messages",
        )
    assert not transport["requests"]
