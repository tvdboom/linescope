"""LineScope.

Author: Mavs
Description: Conservative Databricks invocation observation and workspace
metadata.

"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import functools
import inspect
import posixpath
import re
import threading
from time import perf_counter_ns
from typing import TYPE_CHECKING

from linescope.enums import NotebookCollection, RunStatus, SourceKind
from linescope.model import ProfileRun, SourceLocation, SourceUnit
from linescope.notebooks.correlation import ChildContext, merge_child
from linescope.notebooks.remote import UNAVAILABLE, PreparedChild

if TYPE_CHECKING:
    from databricks.sdk.runtime.dbutils_stub import dbutils as DBUtils

    from linescope.api import Session

_SECRET = re.compile(
    r"secret|password|passwd|token|credential|private|api.?key|authorization", re.I
)


def safe_parameters(arguments: object) -> dict[str, str]:
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


def notebook_path(dbutils: DBUtils) -> str | None:
    """Read the workspace notebook path when the context API permits access.

    Parameters
    ----------
    dbutils : [DBUtils]
        Databricks utilities object exposing the notebook context API.

    Returns
    -------
    str | None
        Workspace notebook path, or None when access is restricted.

    """
    try:
        # The runtime's JVM context bridge is absent from the SDK's public stub.
        entry_point = getattr(dbutils.notebook, "entry_point", None)
        if entry_point is None:
            return None

        context = entry_point.getDbutils().notebook().getContext()
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


def caller_location(session: Session) -> SourceLocation | None:
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
    messages are not embedded in reports. Separate Python child execution is
    instrumented automatically through a temporary workspace notebook when
    SDK access permits it.

    Parameters
    ----------
    session : [Session]
        Owning LineScope session.

    dbutils : [DBUtils]
        Databricks utilities object, or an API-compatible test double.

    Attributes
    ----------
    session : [Session]
        Owning session receiving child run references and diagnostics.

    dbutils : object
        Databricks utilities object used to observe notebook invocations.

    path : str | None
        Current workspace notebook path, when available.

    _original : Any
        Original notebook `run` callable restored during cleanup.

    _wrapper : Any
        Installed invocation observer, retained to check patch ownership.

    _thread : int
        Identifier of the thread whose notebook calls are observed.

    _active : bool
        Whether this observer is installed and accepting invocations.

    _had_instance_attribute : bool
        Whether `run` was already an instance attribute before patching.

    See Also
    --------
    - linescope.notebooks:NotebookIntegration
    - linescope.notebooks:ChildContext
    - linescope.notebooks:merge_child

    """

    def __init__(self, session: Session, dbutils: DBUtils) -> None:
        """Initialize notebook invocation observation and patch ownership state.

        Resolve available notebook context without changing the workload's
        utilities.

        Parameters
        ----------
        session : [Session]
            Owning profiling session receiving measurements and diagnostics.

        dbutils : [DBUtils]
            Databricks notebook utilities, when available.

        """
        self.session = session
        self.dbutils = dbutils
        self.path = notebook_path(dbutils)
        self._original: Callable[..., object] | None = None
        self._wrapper: Callable[..., object] | None = None
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
        def run(*args: object, **kwargs: object) -> object:
            """Observe a child invocation and preserve its outcome.

            Scrub parameter values and merge separately collected child
            measurements when available.

            Parameters
            ----------
            *args : object
                Positional arguments or syntax parameters forwarded to the
                operation.

            **kwargs : object
                Keyword arguments forwarded without changing their values.

            Returns
            -------
            object
                Original child notebook return value.

            """
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
            child = ProfileRun(
                id=correlation.correlation_id,
                parent_id=correlation.parent_id,
                source=source,
                name=path,
                metadata={
                    "correlation_id": correlation.correlation_id,
                    "parameters": safe_parameters(arguments),
                    "collection": NotebookCollection.WAIT_ONLY,
                    "child_capabilities": asdict(UNAVAILABLE),
                    "child_source_ids": [source.id],
                },
            )
            location = caller_location(self.session)
            prepared = None

            if (self.path or path.startswith("/")) and getattr(
                self.session.config, "child_notebooks", True
            ):
                try:
                    prepared = PreparedChild(
                        self.session, path, correlation.correlation_id, correlation.parent_id
                    )
                    prepared.prepare()
                except Exception as error:  # noqa: BLE001
                    self.session.result.warnings.append(
                        f"Automatic child profiling unavailable for {path}"
                        f" ({type(error).__name__}); the original notebook will run."
                    )

                    if prepared is not None:
                        prepared.cleanup()

                if prepared is not None and prepared.sources:
                    child.source = prepared.sources[0]
                    child.metadata["collection"] = NotebookCollection.SOURCE_ONLY
                    child.metadata["child_source_ids"] = [unit.id for unit in prepared.sources]

                    for unit in prepared.sources:
                        self.session.registry.register(unit)

            call_args, call_kwargs = args, kwargs

            if prepared is not None and prepared.instrumented:
                if args:
                    call_args = (prepared.path, *args[1:])
                else:
                    call_kwargs = {**kwargs, "path": prepared.path}

            start = perf_counter_ns()

            try:
                result = original(*call_args, **call_kwargs)
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

                if prepared is not None:
                    try:
                        if prepared.instrumented:
                            collected = prepared.collect()
                            merged = merge_child(
                                self.session.result, collected, correlation.correlation_id
                            )
                            # Preserve the live session's registry/result ownership.
                            self.session.result.root_run.children[:] = merged.root_run.children
                            self.session.registry.sources.update(merged.sources)
                            self.session.result.symbols[:] = merged.symbols
                            self.session.result.warnings[:] = merged.warnings
                    except Exception as error:  # noqa: BLE001
                        self.session.result.warnings.append(
                            f"Child profile unavailable for {path} ({type(error).__name__});"
                            " captured source and parent wait are retained."
                        )
                    finally:
                        prepared.cleanup()

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
