"""LineScope.

Author: Mavs
Description: Discover AI model choices without sending captured profile data.

"""

from __future__ import annotations

from http.client import HTTPException
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from linescope.ai.agents import LocalAgent, _stop_process, agent_command
from linescope.ai.connections import Connection, configured_agent, credential_environment
from linescope.ai.providers import ProviderError, _endpoint, _NoRedirect


def _catalog(records: list[Any], connection: Connection, key: str = "") -> dict[str, Any]:
    """Normalize a provider catalog to safe, readable text model choices.

    Parameters
    ----------
    records : list[Any]
        Untrusted model records returned by the configured provider.

    connection : Connection
        Provider whose naming and text-model filtering rules apply.

    key : str, default=""
        Request credential to exclude from echoed identifiers and labels.

    Returns
    -------
    dict[str, Any]
        Unique model choices and an available default identifier.

    """
    models = {}
    default = ""
    for record in records:
        if not isinstance(record, dict) or record.get("hidden") is True:
            continue
        identity = record.get("model", record.get("id"))
        if (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 200
            or identity.startswith("-")
            or any(character.isspace() or not character.isprintable() for character in identity)
            or (key and key in identity)
        ):
            continue
        if connection == Connection.OPENAI and (
            not identity.startswith(("gpt-", "o1", "o3", "o4"))
            or any(
                name in identity
                for name in ("image", "audio", "realtime", "transcribe", "search", "instruct")
            )
        ):
            continue
        family = next(
            (
                name
                for name in ("Astra", "Sol", "Terra", "Luna", "Opus", "Sonnet", "Haiku")
                if name.lower() in identity.split("-")
            ),
            "",
        )
        label = (
            record.get("displayName")
            or record.get("display_name")
            or (f"{family} · {identity}" if family else identity)
        )
        if not isinstance(label, str):
            label = identity
        if key:
            label = label.replace(key, "[redacted]")
        models[identity] = {"id": identity, "label": label[:200]}
        if record.get("isDefault") is True:
            default = identity
    choices = sorted(
        models.values(),
        key=lambda item: ("sol" in item["id"], item["id"]),
        reverse=True,
    )
    if not choices:
        raise ProviderError("No text models were returned by this provider.")
    return {"models": choices, "default_model": default or choices[0]["id"]}


def list_api_models(*, provider: str, endpoint: str = "", api_key: str = "") -> dict[str, Any]:
    """Load a bounded model catalog using request-scoped authentication.

    Use the provider's models endpoint on the same origin as inference.
    Refuse redirects and expose controlled failures without response bodies.
    No profile context is sent, and credentials are never persisted.

    Parameters
    ----------
    provider : str
        API connection identifier: `openai`, `anthropic`, or `compatible`.

    endpoint : str, default=""
        Complete inference endpoint; named providers use their preset.

    api_key : str, default=""
        Request-only key; blank uses the selected provider's environment key.

    Returns
    -------
    dict[str, Any]
        Available model choices and the automatically selected model.

    """
    connection = Connection(provider)
    presets = {
        Connection.OPENAI: "https://api.openai.com/v1/responses",
        Connection.ANTHROPIC: "https://api.anthropic.com/v1/messages",
    }
    if connection not in (*presets, Connection.COMPATIBLE):
        raise ProviderError("Choose an API connection to load models.")
    url = _endpoint(endpoint or presets.get(connection, ""))
    if connection in presets and url != presets[connection]:
        raise ProviderError("This connection uses its preset endpoint.")
    parts = urlsplit(url)
    suffix = (
        "/chat/completions"
        if connection == Connection.COMPATIBLE
        else parts.path.rsplit("/", 1)[-1]
    )
    if connection == Connection.COMPATIBLE and not parts.path.rstrip("/").endswith(suffix):
        raise ProviderError("Use a Chat Completions endpoint to discover its models.")
    path = parts.path.rstrip("/")
    path = (
        path[: -len(suffix)] + "/models"
        if connection == Connection.COMPATIBLE
        else path.rsplit("/", 1)[0] + "/models"
    )
    models_url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    key = api_key.strip() or os.environ.get(credential_environment(connection), "").strip()
    if connection in presets and not key:
        raise ProviderError("Enter a credential to load available models.")
    if len(key) > 8192 or any(ord(character) < 32 or ord(character) > 126 for character in key):
        raise ProviderError("Use a credential of at most 8192 printable ASCII characters.")
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if connection == Connection.ANTHROPIC:
        headers["anthropic-version"] = "2023-06-01"
    records = []
    cursor = ""
    for _ in range(10):
        query = urlencode({"limit": 1000, **({"after_id": cursor} if cursor else {})})
        address = models_url + ("?" + query if connection == Connection.ANTHROPIC else "")
        try:
            with build_opener(_NoRedirect()).open(
                Request(address, headers=headers, method="GET"), timeout=15
            ) as response:
                raw = response.read(1_000_001)
        except HTTPError as error:
            status = error.code
            error.close()
            raise ProviderError(
                f"Could not load models (HTTP {status}). Check the credential."
            ) from None
        except (URLError, TimeoutError, OSError, ValueError, HTTPException):
            raise ProviderError("Could not reach the provider's model catalog.") from None
        if len(raw) > 1_000_000:
            raise ProviderError("The model catalog exceeded the 1 MB limit.")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError("The model catalog contains invalid JSON.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("The provider returned an invalid model catalog.")
        records.extend(payload["data"])
        if connection != Connection.ANTHROPIC or not payload.get("has_more"):
            return _catalog(records, connection, key)
        following = payload.get("last_id")
        if not isinstance(following, str) or not following or following == cursor:
            raise ProviderError("The provider returned an invalid model cursor.")
        cursor = following
    raise ProviderError("The model catalog exceeded the pagination limit.")


def list_agent_models(
    command: tuple[str, ...], *, api_key: str = "", cancel_event: Event | None = None
) -> dict[str, Any]:
    """Discover choices for the exact local agent configured by the owner.

    Claude Code accepts stable model aliases. Codex exposes `model/list` over
    its stdio app server. Other adapters retain their default. No inference
    turn or project access is requested, and owned processes are cleaned up.

    Parameters
    ----------
    command : tuple[str, ...]
        Server-owned native preset or custom adapter command.

    api_key : str, default=""
        Request-only credential; blank preserves the agent's existing login.

    cancel_event : [Event] | None, default=None
        Viewer shutdown signal used to stop discovery promptly.

    Returns
    -------
    dict[str, Any]
        Model choices; an empty default preserves the CLI's configuration.

    """
    selected = configured_agent(command)
    if selected is None:
        raise ProviderError("Configure a local agent before loading its models.")
    if selected == Connection.CLAUDE:
        return {
            "models": [
                {"id": name, "label": name.title()} for name in ("opus", "sonnet", "haiku")
            ],
            "default_model": "",
        }
    if selected != Connection.CODEX:
        return {"models": [], "default_model": ""}
    return _codex_models(command, api_key, cancel_event)


def _codex_models(
    command: tuple[str, ...], api_key: str, cancel_event: Event | None, *, timeout: float = 15
) -> dict[str, Any]:
    """Read Codex model pages after initializing an isolated stdio connection.

    Parameters
    ----------
    command : tuple[str, ...]
        Exact Codex preset whose executable may be started.

    api_key : str
        Request-only credential, never included in protocol messages.

    cancel_event : [Event] | None
        Viewer-owned cancellation signal, or None for a standalone call.

    timeout : float, default=15
        Total discovery deadline in seconds, including pagination.

    Returns
    -------
    dict[str, Any]
        Discovered choices without account details or authentication tokens.

    """
    if command != agent_command(LocalAgent.CODEX):
        raise ProviderError("Configure the Codex preset before loading its models.")
    executable = shutil.which(command[0])
    if executable is None or Path(executable).suffix.lower() in {".bat", ".cmd"}:
        raise ProviderError("Install a native Codex CLI and run codex login first.")
    if len(api_key) > 8192 or any(
        ord(character) < 32 or ord(character) > 126 for character in api_key
    ):
        raise ProviderError("Use a credential of at most 8192 printable ASCII characters.")
    environment = None
    if api_key:
        environment = {**os.environ, "CODEX_API_KEY": api_key}
    with TemporaryDirectory(prefix="linescope-models-") as directory:
        output_path = Path(directory) / "catalog.jsonl"
        with output_path.open("wb") as output, output_path.open("rb") as incoming:
            process = subprocess.Popen(
                [executable, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=environment,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert process.stdin is not None
            try:
                with process.stdin as outgoing:
                    initial = {
                        "id": 0,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "linescope",
                                "title": "LineScope",
                                "version": version("linescope"),
                            }
                        },
                    }
                    outgoing.write((json.dumps(initial) + "\n").encode())
                    outgoing.flush()
                    deadline = time.monotonic() + timeout
                    pending = b""
                    request_id = 0
                    records = []
                    cursors = set()
                    while time.monotonic() < deadline:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ProviderError("The viewer stopped model discovery.")
                        if output_path.stat().st_size > 1_000_000:
                            raise ProviderError("The model catalog exceeded the 1 MB limit.")
                        pending += incoming.read(65_536)
                        while b"\n" in pending:
                            raw, pending = pending.split(b"\n", 1)
                            message = json.loads(raw)
                            if not isinstance(message, dict) or message.get("id") != request_id:
                                continue
                            result = message.get("result")
                            if message.get("error") or not isinstance(result, dict):
                                raise ProviderError(
                                    "Codex could not load models. Check codex login status."
                                )
                            if request_id == 0:
                                outgoing.write(b'{"method":"initialized","params":{}}\n')
                                cursor = None
                            else:
                                if not isinstance(result.get("data"), list):
                                    raise ProviderError("Codex returned an invalid model catalog.")
                                records.extend(result["data"])
                                cursor = result.get("nextCursor")
                                if cursor is None:
                                    catalog = _catalog(records, Connection.CODEX, api_key)
                                    catalog["default_model"] = ""
                                    return catalog
                                if (
                                    not isinstance(cursor, str)
                                    or cursor in cursors
                                    or len(cursors) >= 10
                                ):
                                    raise ProviderError("Codex returned an invalid model cursor.")
                                cursors.add(cursor)
                            request_id += 1
                            request = {
                                "id": request_id,
                                "method": "model/list",
                                "params": {"limit": 100, "includeHidden": False, "cursor": cursor},
                            }
                            outgoing.write((json.dumps(request) + "\n").encode())
                            outgoing.flush()
                        if process.poll() is not None:
                            raise ProviderError(
                                "Codex model discovery stopped. Check its installation and login."
                            )
                        time.sleep(0.05)
                    raise ProviderError("Codex model discovery timed out. Use Default or retry.")
            except (ValueError, OSError, RecursionError) as error:
                if isinstance(error, ProviderError):
                    raise
                raise ProviderError("Codex returned an invalid model catalog.") from None
            finally:
                _stop_process(process)
