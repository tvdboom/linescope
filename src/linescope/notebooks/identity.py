"""LineScope.

Author: Mavs
Description: Resolve notebook filenames from kernel and execution ownership.

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import psutil

if TYPE_CHECKING:
    from IPython.core.interactiveshell import InteractiveShell


def _connection_file(shell: InteractiveShell) -> Path | None:
    """Locate the active kernel's connection file without starting a kernel.

    Parameters
    ----------
    shell : [InteractiveShell]
        Notebook shell with an existing kernel, when available.

    Returns
    -------
    [Path] | None
        Active connection file, or None for non-kernel shells.

    """
    if getattr(shell, "kernel", None) is None:
        return None

    try:
        from ipykernel import get_connection_file

        return Path(get_connection_file())
    except (ImportError, OSError, RuntimeError):
        return None


def _server_notebook_path(connection: Path) -> str | None:
    """Match the active kernel to a notebook in local Jupyter server sessions.

    Query only local runtime servers, using their existing authentication.
    Restricted, stale, malformed, and ambiguous sessions leave the filename
    unavailable. Server credentials never become report metadata.

    Parameters
    ----------
    connection : [Path]
        Connection file whose `kernel-` stem identifies the running kernel.

    Returns
    -------
    str | None
        Unique notebook path reported by the owning server, or None.

    """
    if not connection.stem.startswith("kernel-"):
        return None

    kernel_id = connection.stem.removeprefix("kernel-")
    directories = {connection.parent}
    try:
        from jupyter_core.paths import jupyter_runtime_dir

        directories.add(Path(jupyter_runtime_dir()))
    except (ImportError, OSError):
        pass

    matches: set[str] = set()
    for directory in directories:
        for pattern in ("jpserver-*.json", "nbserver-*.json"):
            for info_file in directory.glob(pattern):
                try:
                    server = json.loads(info_file.read_text(encoding="utf-8"))
                    if not isinstance(server, dict):
                        continue
                    url = server.get("url")
                    if not isinstance(url, str):
                        continue
                    address = urlsplit(url)
                    if address.scheme not in {"http", "https"} or address.hostname not in {
                        "localhost",
                        "127.0.0.1",
                        "::1",
                    }:
                        continue
                    request = Request(urljoin(url.rstrip("/") + "/", "api/sessions"))
                    token = server.get("token")
                    if isinstance(token, str) and token:
                        request.add_header("Authorization", f"token {token}")
                    with urlopen(request, timeout=1) as response:
                        sessions = json.load(response)
                    if not isinstance(sessions, list):
                        continue
                    for session in sessions:
                        if not isinstance(session, dict):
                            continue
                        kernel = session.get("kernel")
                        if not isinstance(kernel, dict) or kernel.get("id") != kernel_id:
                            continue
                        path = session.get("path")
                        if not isinstance(path, str) or not path.lower().endswith(".ipynb"):
                            continue
                        root = server.get("root_dir", server.get("notebook_dir"))
                        if isinstance(root, str) and root:
                            path = str(Path(root) / path)
                        matches.add(path)
                except (OSError, ValueError):
                    # Notebook naming must not interrupt profiling if a server
                    # has stopped or its session API is restricted.
                    continue

    return next(iter(matches)) if len(matches) == 1 else None


def _executed_notebook_path() -> str | None:
    """Read a unique input notebook from the owning nbconvert process.

    Batch execution kernels may have no Jupyter server or filename metadata.
    Inspect the nearest nbconvert launcher and resolve its explicit input
    against its own working directory. Multiple inputs remain ambiguous.

    Returns
    -------
    str | None
        Existing input notebook path, or None when ownership is unavailable.

    """
    try:
        process = psutil.Process().parent()
        for _ in range(4):
            if process is None:
                break
            arguments = process.cmdline()
            if any(
                Path(argument).stem in {"nbconvert", "jupyter-nbconvert"} for argument in arguments
            ):
                inputs: set[str] = set()
                for index, argument in enumerate(arguments):
                    if argument.startswith("-") or not argument.lower().endswith(".ipynb"):
                        continue
                    if index and arguments[index - 1] in {"--output", "--output-dir"}:
                        continue
                    candidate = Path(argument).expanduser()
                    if not candidate.is_absolute():
                        candidate = Path(process.cwd()) / candidate
                    if candidate.is_file():
                        inputs.add(str(candidate.resolve()))
                return next(iter(inputs)) if len(inputs) == 1 else None
            process = process.parent()
    except (psutil.Error, OSError, ValueError):
        pass

    return None


def _kernel_notebook_path(shell: InteractiveShell) -> str | None:
    """Resolve a notebook through its existing kernel's execution owner.

    Parameters
    ----------
    shell : [InteractiveShell]
        Notebook shell whose kernel belongs to a server or batch executor.

    Returns
    -------
    str | None
        Notebook filename supplied by an execution owner, or None.

    """
    connection = _connection_file(shell)
    if connection is None:
        return None

    return _server_notebook_path(connection) or _executed_notebook_path()
