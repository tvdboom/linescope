"""LineScope.

Author: Mavs
Description: Describe configured AI connections without exposing credentials.

"""

from __future__ import annotations

from enum import StrEnum
import os
from typing import Any

from linescope.ai.agents import LocalAgent, agent_command


class Connection(StrEnum):
    """Identify a user-selectable AI connection independently of its protocol.

    Attributes
    ----------
    ANTHROPIC : str
        Claude API speaking the Anthropic Messages protocol.

    CLAUDE : str
        Explicitly configured Claude CLI preset.

    CODEX : str
        Explicitly configured Codex CLI preset.

    COPILOT : str
        Explicitly configured Copilot CLI preset.

    COMPATIBLE : str
        Custom API speaking the Chat Completions protocol.

    AGENT : str
        Explicitly configured custom agent command.

    OPENAI : str
        OpenAI API speaking the Responses protocol.

    """

    ANTHROPIC = "anthropic"
    CLAUDE = "claude"
    CODEX = "codex"
    COPILOT = "copilot"
    COMPATIBLE = "compatible"
    AGENT = "agent"
    OPENAI = "openai"


def configured_agent(command: tuple[str, ...]) -> Connection | None:
    """Identify the exact agent command chosen by the viewer owner.

    A native preset is recognized only when every argument matches. Custom
    commands retain their own identity and are never inferred from a name.

    Parameters
    ----------
    command : tuple[str, ...]
        Server-owned executable and arguments, or an empty tuple.

    Returns
    -------
    Connection | None
        Configured preset, custom agent, or None when agents are disabled.

    """
    if not command:
        return None
    for agent in LocalAgent:
        if command == agent_command(agent):
            return Connection(agent.value)
    return Connection.AGENT


def credential_environment(connection: str | Connection) -> str:
    """Choose the credential variable owned by a supported connection.

    Never accept a variable name from browser input. Local agents can also
    use their existing authentication when no request-specific key is sent.

    Parameters
    ----------
    connection : str | Connection
        Known connection identifier selected for the request.

    Returns
    -------
    str
        Fixed environment variable used for this connection's API key.

    """
    return {
        Connection.ANTHROPIC: "ANTHROPIC_API_KEY",
        Connection.CLAUDE: "ANTHROPIC_API_KEY",
        Connection.CODEX: "CODEX_API_KEY",
        Connection.COPILOT: "COPILOT_GITHUB_TOKEN",
        Connection.AGENT: "LINESCOPE_AI_API_KEY",
        Connection.COMPATIBLE: "LINESCOPE_AI_API_KEY",
        Connection.OPENAI: "OPENAI_API_KEY",
    }[Connection(connection)]


def browser_connections() -> dict[str, Any]:
    """Describe static browser connections without inspecting credentials.

    Saved reports can request API suggestions after the user configures a
    connection. Native agents require the local viewer. Only Custom API
    accepts an endpoint supplied by the user; other endpoints are presets.

    Returns
    -------
    dict[str, Any]
        Canonical protocol and endpoint metadata, with no selected or
        configured connection and no reads from the process environment.

    """
    labels = {
        Connection.ANTHROPIC: "Claude",
        Connection.CLAUDE: "Claude Code",
        Connection.CODEX: "Codex",
        Connection.COPILOT: "Copilot",
        Connection.COMPATIBLE: "Custom API",
        Connection.AGENT: "Custom agent",
        Connection.OPENAI: "OpenAI",
    }
    connections = []
    for connection in Connection:
        api = connection in (Connection.OPENAI, Connection.ANTHROPIC, Connection.COMPATIBLE)
        connections.append(
            {
                "id": connection.value,
                "label": labels[connection],
                "kind": "api" if api else "agent",
                "configured": False,
                "available": api,
                "requires_model": api,
                "custom_endpoint": connection == Connection.COMPATIBLE,
                "protocol": (
                    "responses"
                    if connection == Connection.OPENAI
                    else "anthropic-messages"
                    if connection == Connection.ANTHROPIC
                    else "chat-completions"
                    if connection == Connection.COMPATIBLE
                    else None
                ),
                "endpoint": (
                    "https://api.openai.com/v1/responses"
                    if connection == Connection.OPENAI
                    else "https://api.anthropic.com/v1/messages"
                    if connection == Connection.ANTHROPIC
                    else ""
                ),
            }
        )
    return {"connections": connections, "default_connection": None}


def describe_connections(command: tuple[str, ...] = ()) -> dict[str, Any]:
    """Describe connection availability using configuration flags only.

    The explicit local agent wins default selection because the viewer owner
    chose it. Otherwise select the sole configured API connection; leave the
    choice open when no connection or multiple APIs are configured. Native
    agent environment keys do not authorize starting an unconfigured agent.

    Parameters
    ----------
    command : tuple[str, ...], default=()
        Explicit local agent command, when configured by the viewer owner.

    Returns
    -------
    dict[str, Any]
        Connection descriptors and an unambiguous default, without keys,
        command arguments, environment contents, or authentication details.

    """
    selected_agent = configured_agent(command)
    descriptor = browser_connections()
    connections = descriptor["connections"]
    for entry in connections:
        if entry["kind"] == "api":
            entry["configured"] = bool(
                os.environ.get(credential_environment(entry["id"]), "").strip()
            )
        else:
            entry["configured"] = entry["available"] = entry["id"] == selected_agent
    configured_ids = [entry["id"] for entry in connections if entry["configured"]]
    default = (
        selected_agent.value
        if selected_agent is not None
        else configured_ids[0]
        if len(configured_ids) == 1
        else None
    )
    descriptor["default_connection"] = default
    return descriptor
