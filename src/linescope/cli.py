"""LineScope.

Author: Mavs
Description: Script and module launchers preserving Python argument semantics.

"""

from __future__ import annotations

from pathlib import Path
import runpy
import sys
from typing import Any

import click

from linescope.api import Session
from linescope.config import resolve_config
from linescope.enums import DisplayMode, SessionState


@click.command(
    "linescope",
    context_settings={"allow_interspersed_args": False},
    help="Profile your Python source, line by line.\n\n"
    "Place options before the script path or -m MODULE. Arguments after the target "
    "are passed to your program. Open an HTML report after exit by default; "
    "use --display none -o report.html for headless collection.",
)
@click.version_option(package_name="linescope", prog_name="LineScope")
@click.option(
    "--backend",
    "-b",
    help="Collector name: scalene, trace, tachyon, or a registered custom backend. "
    "Defaults to trace on every supported Python version.",
)
@click.option(
    "--include", "-i", multiple=True, help="Package, path, or glob to include; repeatable."
)
@click.option(
    "--exclude", "-e", multiple=True, help="Package, path, or glob to exclude; repeatable."
)
@click.option(
    "--memory/--no-memory",
    default=None,
    help="Track process RAM and retained Python allocations separately.",
)
@click.option(
    "--gpu/--no-gpu",
    default=None,
    help="Collect GPU metrics with --backend scalene. Trace warns and continues without them.",
)
@click.option(
    "--sample-rate",
    type=click.IntRange(min=1),
    help="Target samples per second: Scalene and Tachyon default to 1000. Trace ignores it.",
)
@click.option(
    "--spark/--no-spark",
    default=None,
    help="Observe Spark driver actions lazily; requires existing PySpark. Disabled by default.",
)
@click.option(
    "--notebooks/--no-notebooks", default=None, help="Capture notebook source; enabled by default."
)
@click.option("--root", "-r", type=click.Path(file_okay=False), help="Project source root.")
@click.option(
    "--display",
    "-d",
    type=click.Choice([DisplayMode.NONE.value, DisplayMode.END.value]),
    help="Open the report after exit; defaults to end.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    help="Save a report explicitly; otherwise open a temporary report.",
)
@click.option("--module", "-m", is_flag=True, help="Run TARGET as a Python module.")
@click.option(
    "--serve",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Open a saved report in the local viewer with optional AI suggestions.",
)
@click.option(
    "--port", type=click.IntRange(0, 65535), default=0, help="Viewer port; 0 selects a free port."
)
@click.option(
    "--open-browser/--no-open-browser", default=True, help="Open the local report viewer."
)
@click.option(
    "--ai-agent",
    type=click.Choice(["codex", "claude", "copilot"]),
    help="Local agent preset for --serve.",
)
@click.option("--ai-command", help="Custom agent executable; receives the prompt on stdin.")
@click.option(
    "--ai-arg",
    multiple=True,
    help="Literal custom agent argument; repeatable, e.g. --ai-arg=--flag.",
)
@click.argument("target", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def main(
    ctx: click.Context,
    *,
    backend: str | None,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    memory: bool | None,
    gpu: bool | None,
    sample_rate: int | None,
    spark: bool | None,
    notebooks: bool | None,
    root: str | None,
    display: str | None,
    output: str | None,
    module: bool,
    serve: Path | None,
    port: int,
    open_browser: bool,
    ai_agent: str | None,
    ai_command: str | None,
    ai_arg: tuple[str, ...],
    target: tuple[str, ...],
):
    """Profile your Python source, line by line.

    Put profiler options before the target; subsequent arguments go to it.
    Reports retain measurements after workload errors. Use
    `--display none -o report.html` to save without opening a browser.
    Unspecified flags use project settings and API defaults.

    Parameters
    ----------
    --backend, -b : str | None, default=None
        Built-in or registered collector. Defaults to Trace.

    --include, -i : tuple[str, ...], default=()
        Package names, paths, or globs to include. Repeat for multiple entries.

    --exclude, -e : tuple[str, ...], default=()
        Package names, paths, or globs to exclude. Repeat for multiple entries.

    --memory/--no-memory : bool | None, default=None
        Enable or disable process RAM readings, the memory timeline, and
        retained Python allocation tracking.

    --gpu/--no-gpu : bool | None, default=None
        Enable or disable Scalene GPU metrics. Trace warns and continues
        Python profiling without GPU measurements.

    --sample-rate : int | None, default=None
        Target samples per second for Scalene or Tachyon; default: 1000.
        Trace ignores this setting. The achieved rate can vary.

    --spark/--no-spark : bool | None, default=None
        Observe existing Spark actions. Disabled by default; requires PySpark.

    --notebooks/--no-notebooks : bool | None, default=None
        Enable or disable notebook source capture. Enabled by default.

    --root, -r : str | None, default=None
        Project source root. Discover the root from the script when omitted.

    --display, -d : str | None, default=None
        Display mode: `end` or `none`. Default to `end`.

    --output, -o : str | None, default=None
        Report destination. Use a temporary report when omitted.

    --module, -m : bool, default=False
        Execute the target as an importable module instead of a script.

    --serve : pathlib.Path | None, default=None
        Serve an existing report on loopback for optional AI suggestions.

    --port : int, default=0
        Local viewer port. Zero selects an available port automatically.

    --open-browser/--no-open-browser : bool, default=True
        Open the viewer URL automatically when serving a report.

    --ai-agent : str | None, default=None
        Use the installed `codex`, `claude`, or `copilot` command. Existing
        authentication works when no request-specific key is supplied.

    --ai-command : str | None, default=None
        Custom native executable reading a prompt from stdin and writing its
        suggestion to stdout. The executable owns its permission policy.

    --ai-arg : tuple[str, ...], default=()
        Literal arguments for a custom executable, without shell expansion.

    target : tuple[str, ...]
        Script path or module name followed by its arguments.

    Examples
    --------
    ```console
    linescope --backend trace application.py --rows 50000
    linescope --backend tachyon application.py
    linescope --backend scalene --gpu application.py
    linescope --backend trace -m application.worker --rows 50000
    linescope --backend trace --display none -o report.html application.py
    linescope --serve report.html --ai-agent codex
    ```

    """
    if serve is not None:
        if target or module:
            raise click.UsageError("--serve cannot be combined with a profiling target", ctx)
        if any(
            ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
            for name in (
                "backend",
                "include",
                "exclude",
                "memory",
                "gpu",
                "sample_rate",
                "spark",
                "notebooks",
                "root",
                "display",
                "output",
            )
        ):
            raise click.UsageError("--serve cannot be combined with profiling options", ctx)
        if ai_agent and ai_command:
            raise click.UsageError("choose either --ai-agent or --ai-command", ctx)
        if ai_arg and not ai_command:
            raise click.UsageError("--ai-arg requires --ai-command", ctx)

        from linescope.ai.agents import agent_command
        from linescope.ai.server import serve_report

        command = (
            agent_command(ai_agent) if ai_agent else (ai_command, *ai_arg) if ai_command else ()
        )
        try:
            serve_report(serve, port=port, open_browser=open_browser, agent_command=command)
        except (OSError, ValueError) as error:
            raise click.ClickException(str(error)) from error
        return

    if ai_agent or ai_command or ai_arg or port or not open_browser:
        raise click.UsageError("viewer and AI options require --serve REPORT.html", ctx)

    if not target:
        message = "-m requires a module name" if module else "provide a script or -m MODULE"
        raise click.UsageError(message, ctx)

    options: dict[str, Any] = {
        name: value
        for name, value in {
            "backend": backend,
            "memory": memory,
            "gpu": gpu,
            "sample_rate": sample_rate,
            "spark": spark,
            "notebooks": notebooks,
            "root": root,
            "display": DisplayMode(display) if display is not None else None,
            "output": output,
        }.items()
        if value is not None
    }

    if include:
        options["include"] = include

    if exclude:
        options["exclude"] = exclude

    arguments = list(target[1:])

    if module and arguments and arguments[0] == "--":
        arguments.pop(0)

    if not module:
        script = Path(target[0]).expanduser().resolve()

        if not script.is_file():
            raise click.BadParameter(f"script does not exist: {script}", ctx, param_hint="TARGET")

        if "root" not in options:
            from linescope.source import discover_root

            options["root"] = str(discover_root(script))

    config = resolve_config(**options)
    options.setdefault("output", config.output)
    options.setdefault("display", config.display)
    session_options = dict(options)
    session_options["display"] = DisplayMode.NONE
    session = Session(**session_options)
    previous_argv, previous_path = sys.argv, sys.path[:]
    _namespace: dict[str, Any] | None = None

    try:
        with session:
            if module:
                sys.argv = [target[0], *arguments]
                sys.path.insert(0, str(Path.cwd()))
                # Keep module globals alive until the final memory snapshot.
                _namespace = runpy.run_module(target[0], run_name="__main__", alter_sys=True)
            else:
                session.registry.snapshot(script)
                sys.argv = [str(script), *arguments]
                sys.path.insert(0, str(script.parent))
                _namespace = runpy.run_path(str(script), run_name="__main__")
    finally:
        # Profiling has captured globals; release them before rendering reports.
        _namespace = None
        sys.argv = previous_argv
        sys.path[:] = previous_path

        if session.state == SessionState.STOPPED:
            workload_error = sys.exc_info()[1]

            try:
                if options["output"] is not None:
                    saved = session.save(options["output"])
                    click.echo(f"LineScope report: {saved}", err=True)

                if options["display"] == DisplayMode.END:
                    session.show()
            except Exception as error:
                if workload_error is None:
                    raise

                click.echo(f"LineScope could not display the report: {error}", err=True)
