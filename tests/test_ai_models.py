"""LineScope.

Author: Mavs
Description: Verify model discovery, credential isolation, and process cleanup.

"""

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from linescope.ai import agents, models
from linescope.ai.agents import agent_command
from linescope.ai.providers import ProviderError


@pytest.fixture
def catalog_transport(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace catalog HTTP requests with bounded in-memory responses.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore transport and environment substitutions after each test.

    Returns
    -------
    dict
        Mutable response pages, request capture, and optional HTTP failure.

    """
    state = {"pages": [], "requests": [], "error": None}

    def open_request(request, timeout):
        """Record one request and return its configured JSON page.

        Parameters
        ----------
        request : urllib.request.Request
            Outgoing request whose URL, headers, and body are inspected.

        timeout : float
            Bounded network timeout in seconds.

        Returns
        -------
        BytesIO
            Response stream closed by the discovery routine.

        """
        state["requests"].append((request, timeout))
        if state["error"]:
            raise state["error"]
        page = state["pages"].pop(0)
        return BytesIO(page if isinstance(page, bytes) else json.dumps(page).encode())

    def opener(*handlers):
        """Require a no-redirect handler before returning the fake opener.

        Parameters
        ----------
        *handlers : object
            Handlers configured to prevent credential forwarding.

        Returns
        -------
        SimpleNamespace
            Minimal opener exposing the request capture callback.

        """
        assert len(handlers) == 1
        assert isinstance(handlers[0], models._NoRedirect)
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(models, "build_opener", opener)
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LINESCOPE_AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return state


def test_openai_catalog_uses_available_text_models_and_environment_key(
    catalog_transport: dict, monkeypatch: pytest.MonkeyPatch
):
    """Discover text models without sending source or exposing credentials.

    Parameters
    ----------
    catalog_transport : dict
        Controlled model response and outgoing request capture.

    monkeypatch : pytest.MonkeyPatch
        Temporary credential environment restored after the test.

    """
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    catalog_transport["pages"] = [
        {
            "data": [
                {"id": name}
                for name in (
                    "gpt-6-astra",
                    "gpt-6.1-sol",
                    "gpt-6.1-terra",
                    "gpt-6-luna",
                    "gpt-image-2",
                    "gpt-realtime-2",
                    "text-embedding-3-large",
                    "private-key",
                )
            ]
        }
    ]
    result = models.list_api_models(provider="openai")
    assert {model["id"] for model in result["models"]} == {
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6-luna",
    }
    assert result["default_model"] == "gpt-6.1-sol"
    assert "private-key" not in json.dumps(result)
    request, timeout = catalog_transport["requests"][0]
    assert request.full_url == "https://api.openai.com/v1/models"
    assert request.method == "GET"
    assert request.data is None
    assert request.headers["Authorization"] == "Bearer private-key"
    assert timeout == 15


def test_claude_catalog_follows_pages_and_preserves_display_names(catalog_transport: dict):
    """Load provider model names across bounded catalog pages.

    Parameters
    ----------
    catalog_transport : dict
        Two response pages and a capture of same-origin cursor requests.

    """
    catalog_transport["pages"] = [
        {
            "data": [{"id": "claude-opus", "display_name": "Opus"}],
            "has_more": True,
            "last_id": "next/id",
        },
        {"data": [{"id": "claude-sonnet", "display_name": "Sonnet"}], "has_more": False},
    ]
    result = models.list_api_models(provider="anthropic", api_key="request-key")
    assert {model["label"] for model in result["models"]} == {"Opus", "Sonnet"}
    request = catalog_transport["requests"][1][0]
    assert request.full_url == "https://api.anthropic.com/v1/models?limit=1000&after_id=next%2Fid"
    assert request.headers["Anthropic-version"] == "2023-06-01"


def test_custom_catalog_preserves_endpoint_prefix_and_can_omit_key(catalog_transport: dict):
    """Discover installed models from the selected local endpoint only.

    Parameters
    ----------
    catalog_transport : dict
        Local model response and outgoing request capture.

    """
    catalog_transport["pages"] = [{"data": [{"id": "local-model"}]}]
    result = models.list_api_models(
        provider="compatible", endpoint="http://127.0.0.1:11434/custom/v1/chat/completions"
    )
    assert result["models"] == [{"id": "local-model", "label": "local-model"}]
    request = catalog_transport["requests"][0][0]
    assert request.full_url == "http://127.0.0.1:11434/custom/v1/models"
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    "page",
    [b"invalid", {"data": {}}, {"data": []}, b" " * 1_000_001],
    ids=["invalid-json", "wrong-shape", "empty", "oversized"],
)
def test_invalid_catalogs_fail_without_echoing_response_content(catalog_transport: dict, page):
    """Reject malformed, empty, and oversized model catalogs.

    Parameters
    ----------
    catalog_transport : dict
        Response substitution restored after the test.

    page : bytes | dict
        Untrusted response to reject with a controlled diagnostic.

    """
    catalog_transport["pages"] = [page]
    with pytest.raises(ProviderError, match=r"catalog|text models"):
        models.list_api_models(provider="openai", api_key="secret")


def test_catalog_errors_and_preset_endpoints_cannot_leak_keys(catalog_transport: dict):
    """Refuse endpoint substitution and hide provider error bodies.

    Parameters
    ----------
    catalog_transport : dict
        HTTP error with a private body that must never reach the UI.

    """
    with pytest.raises(ProviderError, match="preset"):
        models.list_api_models(provider="openai", endpoint="https://other.test/responses")
    assert catalog_transport["requests"] == []
    catalog_transport["error"] = HTTPError(
        "https://private.test", 401, "secret", {}, BytesIO(b"secret")
    )
    with pytest.raises(ProviderError, match="HTTP 401") as caught:
        models.list_api_models(provider="openai", api_key="secret")
    assert "secret" not in str(caught.value)


def test_local_aliases_and_custom_agent_defaults_need_no_inference():
    """Offer Claude aliases and retain the default for arbitrary adapters.

    Native model discovery must not execute a custom command or request an
    agent suggestion to find a model name.

    """
    result = models.list_agent_models(agent_command("claude"))
    assert [item["id"] for item in result["models"]] == ["opus", "sonnet", "haiku"]
    assert result["default_model"] == ""
    assert models.list_agent_models(("custom-agent",)) == {"models": [], "default_model": ""}


@pytest.fixture
def codex_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Run an offline stdio model server in an owned Python subprocess.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore native executable and process-launch substitutions.

    tmp_path : Path
        Per-test directory for a protocol transcript.

    Yields
    ------
    dict
        Child process capture and replaceable fake server source.

    """
    transcript = tmp_path / "messages.jsonl"
    state = {
        "children": [],
        "commands": [],
        "script": """
import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    with open(sys.argv[1], "a", encoding="utf-8") as output:
        output.write(line)
    if message["method"] == "initialized":
        continue
    result = {}
    if message["method"] == "model/list":
        cursor = message["params"].get("cursor")
        result = {"data": [{"model": "gpt-6.1-sol" if not cursor else "gpt-6-astra",
                            "displayName": "Sol" if not cursor else "Astra"}],
                  "nextCursor": "page-2" if not cursor else None}
    print(json.dumps({"id": message["id"], "result": result}), flush=True)
""",
    }
    original = subprocess.Popen

    def launch(command, **kwargs):
        """Substitute the native server with a real, isolated fake child.

        Parameters
        ----------
        command : list[str]
            Native executable and fixed stdio server arguments to verify.

        **kwargs : Any
            Process isolation and stream settings forwarded unchanged.

        Returns
        -------
        subprocess.Popen
            Owned fake server that must be stopped before discovery returns.

        """
        if command[0] == "taskkill":
            return original(command, **kwargs)
        state["commands"].append(command)
        assert command[1:] == ["app-server", "--listen", "stdio://"]
        assert kwargs["shell"] is False
        child = original(
            [sys._base_executable, "-u", "-c", state["script"], str(transcript)], **kwargs
        )
        state["children"].append((child, kwargs))
        return child

    monkeypatch.setattr(agents.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(models.subprocess, "Popen", launch)
    state["transcript"] = transcript
    try:
        yield state
    finally:
        for child, _ in state["children"]:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_codex_catalog_finds_desktop_cli_without_terminal_path(
    codex_process: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Load models with the installed desktop CLI outside terminal PATH.

    Parameters
    ----------
    codex_process : dict
        Offline app server with protocol and child process captures.

    monkeypatch : pytest.MonkeyPatch
        Restore platform, installation location, and PATH substitutions.

    tmp_path : Path
        Isolated native desktop executable location.

    """
    executable = tmp_path / "OpenAI" / "Codex" / "bin" / "desktop" / "codex.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native CLI fixture")
    monkeypatch.setattr(agents, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(agents.shutil, "which", lambda _name: None)

    result = models.list_agent_models(agent_command("codex"))
    assert {item["label"] for item in result["models"]} == {"Sol", "Astra"}
    assert codex_process["commands"][0][0] == str(executable.resolve())
    child, options = codex_process["children"][0]
    assert child.poll() is not None
    assert not Path(options["cwd"]).exists()


def test_missing_codex_catalog_reports_availability(monkeypatch: pytest.MonkeyPatch):
    """Report an unavailable client without installation copy in the panel.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore the missing executable lookup after the request.

    """
    monkeypatch.setattr(models, "_agent_executable", lambda _command: None)
    with pytest.raises(ProviderError, match="Codex is unavailable in this viewer"):
        models.list_agent_models(agent_command("codex"))


def test_codex_catalog_initializes_lists_pages_and_cleans_up(codex_process: dict):
    """Discover models without creating a thread or inference turn.

    Parameters
    ----------
    codex_process : dict
        Offline app server and its owned process and protocol captures.

    """
    result = models.list_agent_models(agent_command("codex"), api_key="temporary-secret")
    assert {item["label"] for item in result["models"]} == {"Sol", "Astra"}
    assert result["default_model"] == ""
    messages = [json.loads(line) for line in codex_process["transcript"].read_text().splitlines()]
    assert [item["method"] for item in messages] == [
        "initialize",
        "initialized",
        "model/list",
        "model/list",
    ]
    assert "temporary-secret" not in json.dumps(messages)
    child, options = codex_process["children"][0]
    assert options["env"]["CODEX_API_KEY"] == "temporary-secret"
    assert child.poll() is not None
    assert not Path(options["cwd"]).exists()


@pytest.mark.parametrize("failure", ["timeout", "cancel", "invalid"])
def test_codex_catalog_cleans_up_on_failures(codex_process: dict, failure: str):
    """Stop the child and remove its temporary files on every failure path.

    Parameters
    ----------
    codex_process : dict
        Replaceable offline stdio server and owned child process capture.

    failure : str
        Timeout, cancellation, or invalid protocol response to exercise.

    """
    cancel = Event()
    codex_process["script"] = "import time; time.sleep(30)"
    if failure == "cancel":
        cancel.set()
    if failure == "invalid":
        codex_process["script"] = 'import time; print("invalid", flush=True); time.sleep(30)'
    with pytest.raises(ProviderError, match=r"timed out|stopped|invalid"):
        models._codex_models(
            agent_command("codex"), "", cancel, timeout=3 if failure == "invalid" else 0.3
        )
    child, options = codex_process["children"][0]
    assert child.poll() is not None
    assert not Path(options["cwd"]).exists()
