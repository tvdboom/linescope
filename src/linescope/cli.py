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
    help="Collector name: scalene, trace, tachyon, or a registered custom backend. "
    "Defaults to trace on every supported Python version.",
)
@click.option("--include", multiple=True, help="Package, path, or glob to include; repeatable.")
@click.option("--exclude", multiple=True, help="Package, path, or glob to exclude; repeatable.")
@click.option(
    "--memory/--no-memory",
    default=None,
    help="Track process RAM and retained Python allocations separately.",
)
@click.option("--gpu/--no-gpu", default=None, help="Collect Scalene GPU metrics.")
@click.option(
    "--sample-rate",
    type=click.IntRange(min=1),
    help="Target samples per second: Scalene defaults to 100; Tachyon to 1000. Trace ignores it.",
)
@click.option(
    "--spark/--no-spark",
    default=None,
    help="Observe Spark driver actions lazily; enabled by default.",
)
@click.option(
    "--notebooks/--no-notebooks", default=None, help="Capture notebook source; enabled by default."
)
@click.option("--root", type=click.Path(file_okay=False), help="Project source root.")
@click.option(
    "--display",
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
    target: tuple[str, ...],
) -> None:
    """Profile your Python source, line by line.

    Place LineScope options before the script path or `-m MODULE`; subsequent
    arguments are passed to the target. Reports also finalize on workload
    errors. Open the HTML report in a new browser tab by default. Use
    `--display none -o report.html` for headless collection.
    Unspecified options follow the project configuration and API defaults.

    Parameters
    ----------
    --backend : str | None, default=None
        Collector name. Default to Scalene on Python 3.11-3.14 and Trace on
        Python 3.15. Registered custom backend names are also accepted.

    --include : tuple[str, ...], default=()
        Package names, paths, or globs to include. Repeat for multiple entries.

    --exclude : tuple[str, ...], default=()
        Package names, paths, or globs to exclude. Repeat for multiple entries.

    --memory/--no-memory : bool | None, default=None
        Enable or disable process RAM readings, the memory timeline, and
        retained Python allocation tracking.

    --gpu/--no-gpu : bool | None, default=None
        Enable or disable Scalene GPU metrics.

    --sample-rate : int | None, default=None
        Target samples per second for Scalene or Tachyon. Use the backend
        default when omitted. Trace ignores this setting.

    --spark/--no-spark : bool | None, default=None
        Enable or disable lazy Spark action observation. Enabled by default.

    --notebooks/--no-notebooks : bool | None, default=None
        Enable or disable notebook source capture. Enabled by default.

    --root : str | None, default=None
        Project source root. Discover the root from the script when omitted.

    --display : str | None, default=None
        Display mode: `end` or `none`. Default to `end`.

    --output, -o : str | None, default=None
        Report destination. Use a temporary report when omitted.

    --module, -m : bool, default=False
        Execute the target as an importable module instead of a script.

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
    ```

    """
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
