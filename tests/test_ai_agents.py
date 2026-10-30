"""LineScope.

Author: Mavs
Description: Verify local agent handoff, output limits, and process cleanup.

"""

import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Timer
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from linescope.ai import agents
from linescope.ai.agents import LocalAgent, agent_command, request_agent_suggestion
from linescope.cli import main


def test_agent_receives_literal_stdin_and_isolated_directory():
    """Keep source outside command arguments and clean the temporary directory.

    A real child process checks stdin and its working directory without
    invoking a model or needing any credentials.

    """
    prompt = 'Improve this: $(whoami) & `danger` "quoted"\nUnicode: λ'
    command = (
        sys.executable,
        "-c",
        (
            "import json,os,sys; "
            "print(json.dumps([sys.stdin.buffer.read().decode('utf8'),os.getcwd(),sys.argv[1:]]))"
        ),
        "literal & argument",
    )
    reply = json.loads(request_agent_suggestion(prompt, command))
    assert reply[0] == prompt
    assert not Path(reply[1]).exists()
    assert reply[2] == ["literal & argument"]


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("import sys; print('secret-key',file=sys.stderr); sys.exit(1)", "agent failed"),
        ("pass", "no suggestion"),
        ("print('x'*1048577)", "output limit"),
    ],
)
def test_agent_failure_does_not_expose_diagnostics(script, message):
    """Return actionable errors without echoing process output or secrets.

    Cover failed execution, empty responses, and oversized responses.

    """
    with pytest.raises(ValueError, match=message) as error:
        request_agent_suggestion("review captured source", (sys.executable, "-c", script))
    assert "secret-key" not in str(error.value)


def test_agent_timeout_terminates_child_and_removes_context(tmp_path):
    """Reap a slow child and remove the captured prompt after a timeout.

    The child records its working directory before sleeping so cleanup can
    be checked without relying on exact runtime measurements.

    """
    marker = tmp_path / "directory.txt"
    script = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(os.getcwd()); time.sleep(30)"
    )
    with pytest.raises(ValueError, match="timed out"):
        request_agent_suggestion(
            "private snapshot",
            (sys.executable, "-c", script, str(marker)),
            timeout=2,
        )
    assert marker.exists()
    assert not Path(marker.read_text()).exists()


def test_agent_presets_are_noninteractive_and_restrict_tools():
    """Use existing authentication while selecting suggestion-only behavior.

    Commands send prompts on stdin instead of interpolating source into a
    shell command, and do not enable automatic write permission.

    """
    codex = agent_command(LocalAgent.CODEX)
    assert codex[-1] == "-"
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in codex
    assert "features.shell_tool=false" in codex
    assert "--available-tools=ask_user" in agent_command("copilot")
    assert "--no-ask-user" in agent_command("copilot")
    claude = agent_command(LocalAgent.CLAUDE)
    assert "--print" in claude
    assert "--safe-mode" in claude
    assert claude[claude.index("--disallowedTools") + 1] == "*"
    assert "--no-session-persistence" in claude


@pytest.mark.parametrize("path_command", [None, "codex.cmd"])
def test_windows_codex_finds_desktop_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path_command: str | None
):
    """Use the newest desktop CLI when a terminal lacks a native command.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore platform, PATH lookup, and installation directory overrides.

    tmp_path : Path
        Isolated desktop installation tree with two native CLI versions.

    path_command : str | None
        Missing command or unsupported batch wrapper returned by PATH lookup.

    """
    monkeypatch.setattr(agents, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(agents.shutil, "which", lambda _name: path_command)
    directory = tmp_path / "OpenAI" / "Codex" / "bin"
    old = directory / "old" / "codex.exe"
    recent = directory / "recent" / "codex.exe"
    for path in (old, recent):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"native CLI fixture")
    os.utime(old, (100, 100))
    os.utime(recent, (200, 200))

    assert agents._agent_executable(agent_command("codex")) == str(recent.resolve())
    assert agents._agent_executable(("custom-agent",)) == path_command


def test_windows_codex_prefers_native_command_on_path(monkeypatch: pytest.MonkeyPatch):
    """Respect a native CLI on PATH even when a batch wrapper shadows it.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Restore platform and executable lookup substitutions.

    """
    monkeypatch.setattr(agents, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        agents.shutil, "which", {"codex": "codex.cmd", "codex.exe": "codex.exe"}.get
    )
    assert agents._agent_executable(agent_command("codex")) == "codex.exe"


def test_agent_shutdown_cancels_running_process(tmp_path):
    """Remove context when the viewer shuts down during an agent request.

    Signal a real sleeping child without waiting for its normal timeout.

    """
    marker = tmp_path / "directory.txt"
    script = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(os.getcwd()); time.sleep(30)"
    )
    cancelled = Event()
    timer = Timer(1, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ValueError, match="stopped"):
            request_agent_suggestion(
                "private snapshot",
                (sys.executable, "-c", script, str(marker)),
                cancel_event=cancelled,
            )
    finally:
        timer.cancel()
        timer.join()
    assert marker.exists()
    assert not Path(marker.read_text()).exists()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_agent_rejects_unbounded_timeout(timeout):
    """Reject invalid deadlines before launching an external process.

    Non-finite values must not leave a hung custom agent running forever.

    """
    with pytest.raises(ValueError, match="timeout"):
        request_agent_suggestion("review", (sys.executable,), timeout=timeout)


def test_agent_start_failure_hides_operating_system_details(monkeypatch):
    """Report failed process creation without leaking system exception text.

    Resolve a real executable but simulate an unavailable runtime at launch.

    """

    def fail(*_args, **_kwargs):
        """Represent a runtime failure containing sensitive diagnostic text.

        No child process is created by this controlled failure.

        """
        raise OSError("private path and credentials")

    monkeypatch.setattr("linescope.ai.agents.subprocess.Popen", fail)
    with pytest.raises(ValueError, match="Could not start") as error:
        request_agent_suggestion("review", (sys.executable,))
    assert "private" not in str(error.value)


@pytest.mark.parametrize("command", [(), ("missing-linescope-agent-1287",)])
def test_unconfigured_or_missing_agent_has_clear_error(command):
    """Reject an unavailable executable before writing any source to disk.

    Missing installations should produce configuration guidance.

    """
    with pytest.raises(ValueError, match=r"Configure|not found"):
        request_agent_suggestion("review", command)


def test_viewer_cli_passes_literal_agent_arguments(tmp_path, monkeypatch):
    """Pass explicit command arguments without consuming profiling targets.

    The report viewer is replaced so the CLI test never starts a server.

    """
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    calls = []

    def capture(path, **kwargs):
        """Record the selected report and viewer configuration.

        Keep invocation independent of network or browser availability.

        """
        calls.append((path, kwargs))

    monkeypatch.setattr("linescope.ai.server.serve_report", capture)
    result = CliRunner().invoke(
        main,
        [
            "--serve",
            str(report),
            "--ai-command",
            sys.executable,
            "--ai-arg=-u",
            "--ai-arg",
            "literal & argument",
            "--no-open-browser",
            "--port",
            "8081",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        (
            report,
            {
                "port": 8081,
                "open_browser": False,
                "agent_command": (sys.executable, "-u", "literal & argument"),
            },
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--ai-agent", "codex"],
        ["--serve", "REPORT", "program.py"],
        ["--serve", "REPORT", "--backend", "trace"],
        ["--serve", "REPORT", "--ai-agent", "codex", "--ai-command", "python"],
        ["--serve", "REPORT", "--ai-arg=--flag"],
    ],
)
def test_viewer_rejects_ambiguous_cli_modes(tmp_path, arguments):
    """Reject conflicting options before launching an agent or profiler.

    Keep custom command configuration explicit and viewer-only.

    """
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    result = CliRunner().invoke(main, [str(report) if x == "REPORT" else x for x in arguments])
    assert result.exit_code == 2


def test_agent_request_credentials_and_model_leave_parent_unchanged(monkeypatch):
    """Keep explicit custom credentials and model choices scoped to one child.

    A second request inherits the original values rather than the first key.

    """
    monkeypatch.setenv("LINESCOPE_AI_API_KEY", "inherited-private-key")
    monkeypatch.setenv("LINESCOPE_AI_MODEL", "inherited-model")
    script = (
        "import json,os; "
        "print(json.dumps([os.environ.get('LINESCOPE_AI_API_KEY'),"
        "os.environ.get('LINESCOPE_AI_MODEL')]))"
    )
    command = (sys.executable, "-c", script)
    explicit = request_agent_suggestion(
        "review",
        command,
        api_key="request-private-key",
        credential_env="LINESCOPE_AI_API_KEY",
        model="chosen-model",
    )
    inherited = request_agent_suggestion("review", command)
    model_only = request_agent_suggestion("review", command, model="another-model")

    assert json.loads(explicit) == ["[redacted]", "chosen-model"]
    assert json.loads(inherited) == ["[redacted]", "inherited-model"]
    assert json.loads(model_only) == ["[redacted]", "another-model"]
    assert os.environ["LINESCOPE_AI_API_KEY"] == "inherited-private-key"
    assert os.environ["LINESCOPE_AI_MODEL"] == "inherited-model"
    assert "request-private-key" not in explicit


@pytest.mark.parametrize(
    ("agent", "credential_env"),
    [
        (LocalAgent.CODEX, "CODEX_API_KEY"),
        (LocalAgent.CLAUDE, "ANTHROPIC_API_KEY"),
        (LocalAgent.COPILOT, "COPILOT_GITHUB_TOKEN"),
    ],
)
def test_native_agent_key_and_model_use_request_scoped_settings(
    monkeypatch, agent, credential_env
):
    """Pass native credentials through env and models through literal arguments.

    Substitute a harmless Python child while observing the intended command.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Controlled environment and process substitutions.

    agent : LocalAgent
        Native preset whose argument convention is exercised.

    credential_env : str
        Approved credential variable for this native adapter.

    """
    monkeypatch.delenv(credential_env, raising=False)
    native_popen = subprocess.Popen
    observed = []

    def capture(command, **kwargs):
        """Record intended native arguments while starting an offline child.

        Return a real process so normal wait and cleanup behavior still runs.

        """
        observed.append((command, kwargs.get("env")))
        return native_popen([sys.executable, "-c", "print('suggestion')"], **kwargs)

    monkeypatch.setattr("linescope.ai.agents.shutil.which", lambda _command: sys.executable)
    monkeypatch.setattr("linescope.ai.agents.subprocess.Popen", capture)
    command = agent_command(agent)
    assert (
        request_agent_suggestion(
            "review",
            command,
            api_key="request-private-key",
            credential_env=credential_env,
            model="chosen-model",
        )
        == "suggestion"
    )
    assert (
        request_agent_suggestion("review", command, credential_env=credential_env) == "suggestion"
    )

    arguments, environment = observed[0]
    assert environment[credential_env] == "request-private-key"
    assert "request-private-key" not in " ".join(arguments)
    assert arguments[arguments.index("--model") + 1] == "chosen-model"
    if agent == LocalAgent.CODEX:
        assert arguments[-1] == "-"
    assert "--model" not in observed[1][0]
    assert observed[1][1] is None
    assert credential_env not in os.environ


@pytest.mark.parametrize(
    "options",
    [
        {"api_key": "key", "credential_env": "PATH"},
        {"api_key": "key"},
        {"api_key": "key\nsecond", "credential_env": "LINESCOPE_AI_API_KEY"},
        {"api_key": "key\x00", "credential_env": "LINESCOPE_AI_API_KEY"},
        {"api_key": "key key", "credential_env": "LINESCOPE_AI_API_KEY"},
        {"api_key": "x" * 16_385, "credential_env": "LINESCOPE_AI_API_KEY"},
        {"model": "--dangerous-option"},
        {"model": "model\nsecond"},
        {"model": "x" * 201},
    ],
)
def test_agent_rejects_unsafe_credential_and_model_fields(options):
    """Reject credential destination changes and malformed values before launch.

    Parameters
    ----------
    options : dict
        Invalid request fields that must never reach an external process.

    """
    with pytest.raises(ValueError, match=r"credential|model"):
        request_agent_suggestion("review", (sys.executable,), **options)


def test_agent_creation_failure_does_not_leak_request_key(monkeypatch):
    """Hide diagnostic text when process startup fails after key preparation.

    Keep the credential out of errors and leave the parent environment intact.

    """
    monkeypatch.delenv("LINESCOPE_AI_API_KEY", raising=False)

    def fail(*_args, **kwargs):
        """Fail with the configured key inside an untrusted diagnostic.

        The caller must replace this operating-system error with safe text.

        """
        raise OSError(kwargs["env"]["LINESCOPE_AI_API_KEY"])

    monkeypatch.setattr("linescope.ai.agents.subprocess.Popen", fail)
    with pytest.raises(ValueError, match="Could not start") as error:
        request_agent_suggestion(
            "review",
            (sys.executable,),
            api_key="request-private-key",
            credential_env="LINESCOPE_AI_API_KEY",
        )
    assert "request-private-key" not in str(error.value)
    assert "LINESCOPE_AI_API_KEY" not in os.environ


def test_agent_redaction_ignores_blank_environment_credentials(monkeypatch):
    """Leave normal output intact when unrelated credentials are blank.

    Still redact an inherited credential after surrounding whitespace is
    removed by the child command.

    """
    monkeypatch.setenv("OPENAI_API_KEY", " \t\n ")
    monkeypatch.setenv("LINESCOPE_AI_API_KEY", "  inherited-private-key  ")
    script = (
        "import os; print('A useful suggestion. ' + os.environ['LINESCOPE_AI_API_KEY'].strip())"
    )
    assert request_agent_suggestion("review", (sys.executable, "-c", script)) == (
        "A useful suggestion. [redacted]"
    )


def test_viewer_cli_accepts_claude_preset(tmp_path, monkeypatch):
    """Expose Claude through the same viewer option as the other native agents.

    Inspect dispatch without starting a server or an authenticated CLI.

    """
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    commands = []

    def capture(_path, **kwargs):
        """Record the native command selected by the CLI.

        Avoid starting network or browser work during this test.

        """
        commands.append(kwargs["agent_command"])

    monkeypatch.setattr("linescope.ai.server.serve_report", capture)
    result = CliRunner().invoke(main, ["--serve", str(report), "--ai-agent", "claude"])
    assert result.exit_code == 0, result.output
    assert commands == [agent_command(LocalAgent.CLAUDE)]
