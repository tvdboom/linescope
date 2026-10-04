"""LineScope.

Author: Mavs
Description: Conservative Databricks invocation observation and workspace
metadata.

"""

from __future__ import annotations

import functools
import inspect
import posixpath
import re
import threading
from time import perf_counter_ns
from typing import Any

from linescope.enums import RunStatus, SourceKind
from linescope.model import ProfileRun, SourceLocation, SourceUnit
from linescope.notebooks.correlation import ChildContext

_SECRET = re.compile(
    r"secret|password|passwd|token|credential|private|api.?key|authorization", re.I
)


def safe_parameters(arguments: Any) -> dict[str, str]:
    """Return bounded notebook metadata with sensitive parameter names redacted.

    Parameters
    ----------
    arguments : object
        Notebook arguments. Non-dictionary values produce no metadata.

    Returns
    -------
    dict[str, str]
        Up to 50 keys. Values are omitted by default because even ordinary
        parameter names can contain personal information or credentials.

    """
    if not isinstance(arguments, dict):
        return {}

    return {
        str(key)[:100]: "[redacted]" if _SECRET.search(str(key)) else "[value omitted]"
        for key in list(arguments)[:50]
    }


def notebook_path(dbutils: Any) -> str | None:
    """Read the workspace notebook path when the context API permits access.

    Parameters
    ----------
    dbutils : object
        Databricks utilities object exposing the notebook context API.

    Returns
    -------
    str | None
        Workspace notebook path, or None when access is restricted.

    """
    try:
        context = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        return str(context.notebookPath().get())
    except Exception:  # noqa: BLE001
        return None


def resolve_notebook_path(path: str, parent: str | None = None) -> str:
    """Resolve a relative workspace notebook path against its parent's folder.

    Parameters
    ----------
    path : str
        Absolute or relative workspace notebook path.

    parent : str | None, default=None
        Calling notebook's workspace path.

    Returns
    -------
    str
        Normalized workspace path using forward slashes.

    """
    if path.startswith("/") or not parent:
        return posixpath.normpath(path)

    return posixpath.normpath(posixpath.join(posixpath.dirname(parent), path))


def caller_location(session: Any) -> SourceLocation | None:
    """Find the closest registered project or notebook frame.

    Parameters
    ----------
    session : [Session]
        [Session] whose source registry defines the visible code scope.

    Returns
    -------
    [SourceLocation] | None
        Nearest project location; no source location is guessed.

    """
    frame = inspect.currentframe()

    try:
        frame = frame.f_back if frame else None

        while frame:
            filename = frame.f_code.co_filename
            module = str(frame.f_globals.get("__name__", ""))

            if not module.startswith("linescope."):
                unit = session.registry.snapshot(filename)

                if unit is not None:
                    return SourceLocation(unit.id, frame.f_lineno)

            frame = frame.f_back
    finally:
        del frame

    return None


class DatabricksIntegration:
    """Observe `dbutils.notebook.run` and restore its previous implementation.

    Arguments and results are never changed. Returned values and exception
    messages are not embedded in reports. Separate child execution requires
    explicit instrumentation to obtain child line timings.

    Parameters
    ----------
    session : [Session]
        Owning LineScope session.

    dbutils : object
        Databricks utilities object, or an API-compatible test double.

    See Also
    --------
    - linescope.notebooks:NotebookIntegration
    - linescope.notebooks:ChildContext
    - linescope.notebooks:merge_child

    """

    def __init__(self, session: Any, dbutils: Any) -> None:
        self.session = session
        self.dbutils = dbutils
        self.path = notebook_path(dbutils)
        self._original: Any = None
        self._wrapper: Any = None
        self._thread = threading.get_ident()
        self._active = False
        self._had_instance_attribute = False

    def start(self) -> None:
        """Install a reversible observer when the notebook object is writable.

        Capture child invocation metadata around the existing notebook call.

        """
        if self._active:
            return

        try:
            notebook = getattr(self.dbutils, "notebook", None)

            if notebook is None:
                return

            original = getattr(notebook, "run", None)
        except Exception:  # noqa: BLE001
            self.session.result.warnings.append(
                "Databricks notebook.run is unavailable in this environment."
            )
            return

        if not callable(original):
            return

        self._original = original
        self._had_instance_attribute = "run" in getattr(notebook, "__dict__", {})

        @functools.wraps(original)
        def run(*args: Any, **kwargs: Any) -> Any:
            if not self._active or threading.get_ident() != self._thread:
                return original(*args, **kwargs)

            raw_path = args[0] if args else kwargs.get("path", "unknown notebook")
            path = resolve_notebook_path(str(raw_path), self.path)
            arguments = args[2] if len(args) > 2 else kwargs.get("arguments", {})
            correlation = ChildContext.create(self.session.result.root_run.id)

            if isinstance(arguments, dict):
                supplied = arguments.get("linescope_correlation_id")
                parent_id = arguments.get("linescope_parent_id")
                pending = [self.session.result.root_run]
                existing_ids = set()

                while pending:
                    node = pending.pop()
                    existing_ids.add(node.id)
                    pending.extend(node.children)

                if (
                    isinstance(supplied, str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", supplied)
                    and parent_id == correlation.parent_id
                    and supplied not in existing_ids
                ):
                    correlation = ChildContext(supplied, parent_id)

            source = SourceUnit(
                f"notebook://{path}",
                path,
                "# Child notebook source unavailable.\n",
                SourceKind.NOTEBOOK,
            )
            self.session.registry.register(source)
            child = ProfileRun(
                id=correlation.correlation_id,
                parent_id=correlation.parent_id,
                source=source,
                name=path,
                metadata={
                    "correlation_id": correlation.correlation_id,
                    "parameters": safe_parameters(arguments),
                    "collection": "parent wait only",
                },
            )
            location = caller_location(self.session)
            start = perf_counter_ns()

            try:
                result = original(*args, **kwargs)
                child.metadata["result_type"] = type(result).__name__

                if isinstance(result, str):
                    child.metadata["result_length"] = len(result)

                return result
            except BaseException as error:
                child.status = RunStatus.FAILED
                child.metadata["error_type"] = type(error).__name__
                raise
            finally:
                child.elapsed_ns = perf_counter_ns() - start
                self.session.add_child_run(child, location)

        self._wrapper = run

        try:
            notebook.run = run
            self._active = True
        except Exception:  # noqa: BLE001
            self.session.result.warnings.append(
                "Databricks notebook.run could not be instrumented in this environment."
            )

    def stop(self) -> None:
        """Remove this observer without overwriting a subsequent user patch.

        Restore the original callable only while this integration still owns
        it.

        """
        self._active = False
        notebook = getattr(self.dbutils, "notebook", None)

        if notebook is None:
            return

        if self._wrapper is not None and getattr(notebook, "run", None) is self._wrapper:
            if self._had_instance_attribute:
                notebook.run = self._original
            else:
                del notebook.run
