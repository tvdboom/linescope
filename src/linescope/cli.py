"""LineScope.

Author: Mavs
Description: Script and module launchers preserving Python argument semantics.

"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import subprocess
import sys
from typing import Any

from linescope.api import Session
from linescope.config import resolve_config
from linescope.enums import Backend, DisplayMode, SessionState


def _memory_bootstrap(options: dict[str, Any], argv: list[str]) -> int | None:
    """Launch native allocator profiling before any target code is executed.

    Return the child exit code when a preload is needed; otherwise keep
    execution in the current interpreter.

    """
    config = resolve_config(**options)

    if (
        config.backend != Backend.SCALENE
        or not config.memory
        or sys.platform == "win32"
        or os.environ.get("LINESCOPE_MEMORY_BOOTSTRAPPED") == "1"
    ):
        return None

    from linescope.backends.scalene import memory_preload_environment

    environment = {
        **os.environ,
        **memory_preload_environment(),
        "LINESCOPE_MEMORY_BOOTSTRAPPED": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", "from linescope.cli import main; raise SystemExit(main())", *argv],
        env=environment,
        check=False,
    )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser shared by documentation and entry points.

    Keep CLI defaults in one place so help text and launch behavior agree.

    """
    parser = argparse.ArgumentParser(
        prog="linescope", description="Profile your Python source, line by line."
    )
    parser.add_argument("--version", action="version", version="LineScope 0.1.0")
    parser.add_argument(
        "--backend",
        help="collector: scalene, trace, tachyon (default: scalene; trace on Python 3.15)",
    )
    parser.add_argument(
        "--include", action="append", help="package/path/glob to include; repeatable"
    )
    parser.add_argument(
        "--exclude", action="append", help="package/path/glob to exclude; repeatable"
    )
    parser.add_argument(
        "--memory", action="store_true", default=None, help="collect Python driver memory"
    )
    parser.add_argument(
        "--gpu", action="store_true", default=None, help="collect Scalene GPU metrics"
    )
    parser.add_argument(
        "--spark",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="observe Spark driver actions",
    )
    parser.add_argument("--no-notebooks", action="store_false", dest="notebooks", default=None)
    parser.add_argument("--root", help="project source root")
    parser.add_argument(
        "--display",
        choices=(DisplayMode.NONE, DisplayMode.END),
        default=None,
        help="open report after exit (default: end)",
    )
    parser.add_argument(
        "-o", "--output", help="save a report explicitly (default: open a temporary report)"
    )
    parser.add_argument(
        "-m", "--module", nargs=argparse.REMAINDER, help="module followed by its arguments"
    )
    parser.add_argument(
        "target", nargs=argparse.REMAINDER, help="script and arguments (options go before script)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Profile a script or module and open its HTML report in a new browser tab.

    Place LineScope options before the script path or `-m MODULE`; subsequent
    arguments are passed to the target. Reports also finalize on workload
    errors. Use `--display none -o report.html` for headless collection.
    Available options include `--backend`, `--root`, repeatable `--include`
    and `--exclude`, `--memory`, `--gpu`, `--spark` / `--no-spark`, and
    `--no-notebooks`.

    Parameters
    ----------
    argv : list[str] | None, default=None
        Command arguments excluding the executable; None reads `sys.argv`.

    Returns
    -------
    int
        Zero on success, or the workload's exit code.

    Examples
    --------
    ```console
    linescope --backend trace application.py --rows 50000
    linescope --backend tachyon application.py
    linescope --gpu application.py
    linescope --backend trace -m application.worker --rows 50000
    linescope --backend trace --display none -o report.html application.py
    ```

    """
    original_arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    parsed = vars(parser.parse_args(argv))
    target = parsed.pop("target")
    module_arguments = parsed.pop("module")
    module = None

    if module_arguments is not None:
        if not module_arguments:
            parser.error("-m requires a module name")

        module = module_arguments[0]
        target = [*module_arguments[1:], *target]

    if target and target[0] == "--":
        target.pop(0)

    if module is None and not target:
        parser.error("provide a script or -m MODULE")

    options: dict[str, Any] = {name: value for name, value in parsed.items() if value is not None}

    if module is None:
        script = Path(target[0]).expanduser().resolve()

        if not script.is_file():
            parser.error(f"script does not exist: {script}")

        if "root" not in options:
            from linescope.source import discover_root

            options["root"] = str(discover_root(script))

    options.setdefault("output", resolve_config(**options).output)
    options.setdefault("display", resolve_config(**options).display)
    child_status = _memory_bootstrap(options, original_arguments)

    if child_status is not None:
        return child_status

    session_options = dict(options)
    session_options["display"] = DisplayMode.NONE
    session = Session(**session_options)
    previous_argv, previous_path = sys.argv, sys.path[:]

    try:
        with session:
            if module is not None:
                sys.argv = [module, *target]
                sys.path.insert(0, str(Path.cwd()))
                runpy.run_module(module, run_name="__main__", alter_sys=True)
            else:
                session.registry.snapshot(script)
                sys.argv = [str(script), *target[1:]]
                sys.path.insert(0, str(script.parent))
                runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path

        if session.state == SessionState.STOPPED:
            workload_error = sys.exc_info()[1]

            try:
                if options["output"] is not None:
                    saved = session.save(options["output"])
                    sys.stderr.write(f"LineScope report: {saved}\n")

                if options["display"] == DisplayMode.END:
                    session.show()
            except Exception as error:
                if workload_error is None:
                    raise

                sys.stderr.write(f"LineScope could not display the report: {error}\n")

    return 0
