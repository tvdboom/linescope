"""LineScope.

Author: Mavs
Description: Request suggestions from explicitly configured local agents.

"""

from __future__ import annotations

from enum import StrEnum
from math import isfinite
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time

_CREDENTIAL_VARIABLES = {
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "LINESCOPE_AI_API_KEY",
}


class LocalAgent(StrEnum):
    """Select a supported noninteractive command preset.

    Attributes
    ----------
    CODEX : str
        Codex CLI with a read-only sandbox and ephemeral session.

    COPILOT : str
        Copilot CLI with tools disabled and the prompt supplied on stdin.

    CLAUDE : str
        Claude CLI with customizations, tools, and session persistence disabled.

    """

    CODEX = "codex"
    COPILOT = "copilot"
    CLAUDE = "claude"


def agent_command(agent: str | LocalAgent) -> tuple[str, ...]:
    """Build a suggestion-only command using the agent's existing login.

    Parameters
    ----------
    agent : str | LocalAgent
        Installed agent preset. Authentication remains owned by its CLI.

    Returns
    -------
    tuple[str, ...]
        Executable and literal arguments; the prompt goes to stdin.

    """
    agent = LocalAgent(agent)
    if agent == LocalAgent.CLAUDE:
        return (
            "claude",
            "--print",
            "--safe-mode",
            "--disallowedTools",
            "*",
            "--no-session-persistence",
            "--output-format",
            "text",
            "--permission-mode",
            "dontAsk",
            "--no-chrome",
        )
    if agent == LocalAgent.CODEX:
        return (
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.hooks=false",
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'web_search="disabled"',
            "-c",
            "tools.view_image=false",
            "--color",
            "never",
            "-",
        )
    return (
        "copilot",
        "--available-tools=ask_user",
        "--deny-tool=read,write,shell,url,memory",
        "--disable-builtin-mcps",
        "--no-custom-instructions",
        "--no-ask-user",
        "--no-auto-update",
        "--no-color",
        "--silent",
        "--log-level=none",
    )


def _agent_executable(command: tuple[str, ...]) -> str | None:
    """Find the configured executable and an installed Windows Codex client.

    Prefer the caller's PATH. The Windows desktop app keeps its native CLI
    outside a normal terminal's PATH; use its newest installed executable
    when the exact Codex preset cannot find a native command. Custom agents
    never use this fallback and batch files remain unsupported.

    Parameters
    ----------
    command : tuple[str, ...]
        Validated executable name and literal agent arguments.

    Returns
    -------
    str | None
        Executable path, or None when no matching installation is available.

    """
    executable = shutil.which(command[0])
    if command != agent_command(LocalAgent.CODEX) or sys.platform != "win32":
        return executable
    if executable and Path(executable).suffix.lower() not in {".bat", ".cmd"}:
        return executable
    native = shutil.which("codex.exe")
    if native and Path(native).suffix.lower() == ".exe":
        return native
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        directory = Path(local_data) / "OpenAI" / "Codex" / "bin"
        candidates = [path for path in directory.glob("*/codex.exe") if path.is_file()]
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())
    return executable


def _stop_process(process: subprocess.Popen[bytes]):
    """Terminate the owned process tree after a timeout or interrupted request.

    Parameters
    ----------
    process : subprocess.Popen
        Child started in its own process group by this module.

    """
    if os.name == "nt":
        if process.poll() is not None:
            return
        # Windows has no killpg; taskkill addresses this owned PID only.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def request_agent_suggestion(
    prompt: str,
    command: tuple[str, ...],
    *,
    api_key: str = "",
    credential_env: str | None = None,
    model: str = "",
    timeout: float = 180,
    cancel_event: Event | None = None,
) -> str:
    """Send captured context to a local agent and return its plain-text reply.

    Run in an empty temporary directory, never the profiled checkout. Only
    the server operator configures the command. Custom commands own their
    permission policy; LineScope does not execute returned suggestions.
    Delete temporary input and output on success and failure. Do not expose
    agent diagnostics, which may contain credentials or captured source.

    Parameters
    ----------
    prompt : str
        Reviewed optimization request, supplied as UTF-8 on standard input.

    command : tuple[str, ...]
        Executable followed by literal arguments, without shell expansion.

    api_key : str, default=""
        Request-only credential passed through the approved environment name.
        Empty preserves the CLI's inherited environment and existing login.

    credential_env : str | None, default=None
        Server-selected credential variable: `CODEX_API_KEY`, `OPENAI_API_KEY`,
        `ANTHROPIC_API_KEY`, `COPILOT_GITHUB_TOKEN`, or `LINESCOPE_AI_API_KEY`.
        None permits native authentication but not a supplied credential.

    model : str, default=""
        Optional model override. Exact native presets receive a model flag;
        custom commands receive `LINESCOPE_AI_MODEL` in their environment.
        Empty preserves the agent's own model choice.

    timeout : float, default=180
        Maximum agent runtime in seconds before terminating its process tree.

    cancel_event : threading.Event | None, default=None
        Viewer-owned shutdown signal. Terminate the child when it is set.

    Returns
    -------
    str
        Nonempty UTF-8 suggestion from standard output, at most one MiB.

    """
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("Configure a local agent when starting the viewer.")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
        raise ValueError("Provide a nonempty prompt of at most 100000 characters.")
    if (
        not isinstance(api_key, str)
        or len(api_key) > 16_384
        or any(character.isspace() or not character.isprintable() for character in api_key)
    ):
        raise ValueError("Use a credential without whitespace or control characters.")
    if credential_env is not None and (
        not isinstance(credential_env, str) or credential_env not in _CREDENTIAL_VARIABLES
    ):
        raise ValueError("Use a supported credential environment variable.")
    if api_key and credential_env is None:
        raise ValueError("Configure the agent's credential environment variable.")
    if (
        not isinstance(model, str)
        or len(model) > 200
        or model.startswith("-")
        or any(character.isspace() or not character.isprintable() for character in model)
    ):
        raise ValueError("Use a model identifier without whitespace or control characters.")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("The agent timeout must be positive.")
    if cancel_event is not None and cancel_event.is_set():
        raise ValueError("The local viewer stopped the agent request.")
    executable = _agent_executable(command)
    if executable is None:
        raise ValueError("Agent executable not found. Install it and sign in before retrying.")
    if Path(executable).suffix.lower() in {".cmd", ".bat"}:
        raise ValueError("Use a native agent executable or a Python wrapper, not a batch file.")
    executable = str(Path(executable).resolve())

    environment = None
    if api_key and credential_env is not None:
        environment = os.environ.copy()
        # The server chooses this name; browser input cannot replace PATH or
        # another process setting. Parent credentials remain unchanged.
        environment[credential_env] = api_key
    if model:
        if command == agent_command(LocalAgent.CODEX):
            command = (*command[:-1], "--model", model, command[-1])
        elif command in (agent_command(LocalAgent.CLAUDE), agent_command(LocalAgent.COPILOT)):
            command = (*command, "--model", model)
        else:
            if environment is None:
                environment = os.environ.copy()
            environment["LINESCOPE_AI_MODEL"] = model

    limit = 1_048_576
    with TemporaryDirectory(prefix="linescope-ai-") as directory:
        root = Path(directory)
        input_path, output_path = root / "prompt.txt", root / "reply.txt"
        input_path.write_text(prompt, encoding="utf-8", newline="\n")
        with input_path.open("rb") as input_file, output_path.open("wb") as output_file:
            try:
                process = subprocess.Popen(
                    [executable, *command[1:]],
                    stdin=input_file,
                    stdout=output_file,
                    stderr=subprocess.DEVNULL,
                    cwd=root,
                    env=environment,
                    shell=False,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError:
                raise ValueError("Could not start the configured agent executable.") from None
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ValueError("The local viewer stopped the agent request.")
                    if time.monotonic() >= deadline:
                        raise ValueError("The agent timed out. Try a smaller context or retry.")
                    if output_path.stat().st_size > limit:
                        raise ValueError("The agent reply exceeded the one MiB output limit.")
                    try:
                        process.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                _stop_process(process)
        if process.returncode:
            raise ValueError("The agent failed. Check its installation and login in a terminal.")
        with output_path.open("rb") as reply_file:
            reply = reply_file.read(limit + 1)
        if len(reply) > limit:
            raise ValueError("The agent reply exceeded the one MiB output limit.")
        suggestion = reply.decode("utf-8", errors="replace").strip()
        if not suggestion:
            raise ValueError("The agent returned no suggestion.")
        # CLI output is untrusted: an accidental credential echo must not
        # become visible report content, including with inherited keys.
        credentials = {
            value for name in _CREDENTIAL_VARIABLES if (value := os.environ.get(name, "").strip())
        }
        if api_key:
            credentials.add(api_key)
        for credential in sorted(credentials, key=len, reverse=True):
            suggestion = suggestion.replace(credential, "[redacted]")
        return suggestion
