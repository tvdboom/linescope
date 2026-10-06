"""LineScope.

Author: Mavs
Description: Automatically collect Databricks child profiles through temporary
workspace notebooks and JSON artifacts.

"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import asdict
import functools
import hashlib
from importlib import import_module
import json
import posixpath
import re
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from linescope.enums import RunStatus, SessionState, SourceKind
from linescope.model import BackendCapabilities, ProfileResult, ProfileRun, SourceUnit
from linescope.notebooks.serialization import dumps_result, loads_result

if TYPE_CHECKING:
    from databricks.sdk.core import ApiClient
    from databricks.sdk.runtime.dbutils_stub import dbutils as DBUtils
    from IPython.core.interactiveshell import ExecutionResult, InteractiveShell

    from linescope.api import Session

INTERNAL_CELL = "# LineScope automatic child instrumentation\n"
PENDING_PROFILE = b'{"version":1,"pending":true}'
UNAVAILABLE = BackendCapabilities(
    line_time=False, function_time=False, hit_counts=False, spark_driver=False
)


class WorkspaceStore:
    """Access notebook snapshots and owned artifacts through the SDK client.

    Use the runtime's normal authentication. Never embed credentials in the
    generated notebook or report.

    Attributes
    ----------
    client : Any
        Databricks SDK API client using the runtime's normal authentication.

    """

    def __init__(self) -> None:
        """Initialize the Databricks SDK client using runtime authentication.

        Keep credentials outside generated notebook source and profile
        artifacts.

        """
        self.client: ApiClient = import_module("databricks.sdk").WorkspaceClient().api_client

    def status(self, path: str) -> dict[str, Any]:
        """Read the workspace object's language and kind.

        Use the original notebook's default language for instrumentation.

        """
        return cast(
            dict[str, Any],
            self.client.do("GET", "/api/2.0/workspace/get-status", query={"path": path}),
        )

    def read(self, path: str, export_format: str = "AUTO") -> bytes:
        """Export a notebook or file as decoded bytes.

        Request SOURCE for notebook snapshots and AUTO for profile files.

        """
        response = cast(
            dict[str, Any],
            self.client.do(
                "GET", "/api/2.0/workspace/export", query={"path": path, "format": export_format}
            ),
        )
        return base64.b64decode(response["content"], validate=True)

    def write(
        self,
        path: str,
        content: bytes,
        *,
        notebook: bool = False,
        overwrite: bool = False,
    ) -> None:
        """Import an owned Python notebook or profile file.

        Refuse to overwrite objects unless updating the reserved result file.

        """
        body = {
            "path": path,
            "content": base64.b64encode(content).decode("ascii"),
            "format": "SOURCE" if notebook else "AUTO",
            "overwrite": overwrite,
        }

        if notebook:
            body["language"] = "PYTHON"

        self.client.do("POST", "/api/2.0/workspace/import", body=body)

    def delete(self, path: str) -> None:
        """Delete exactly one owned temporary object.

        Never delete directories recursively.

        """
        self.client.do(
            "POST", "/api/2.0/workspace/delete", body={"path": path, "recursive": False}
        )


def notebook_sources(path: str, source: str) -> list[SourceUnit]:
    """Snapshot all exported Python notebook cells, including unexecuted cells.

    Decode Databricks magic comments for readable source. Stable cell IDs
    allow runtime compiler aliases to reuse these snapshots.

    """
    source = source.removeprefix("# Databricks notebook source\n")
    cells = re.split(r"(?m)^# COMMAND ----------\s*$", source)
    units = []

    for index, cell in enumerate(cells, 1):
        text = re.sub(r"(?m)^# MAGIC ?", "", cell.strip("\n"))
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        units.append(
            SourceUnit(
                f"notebook://{path}#cell-{index}-{digest}",
                path if index == 1 else f"{path} · cell {index}",
                text,
                SourceKind.NOTEBOOK,
            )
        )

    return units


class PreparedChild:
    """Own an instrumented sibling notebook and its result file.

    Keep relative notebook paths in the original directory and preserve the
    workload's arguments and return value. Cleanup belongs to the parent.

    Attributes
    ----------
    session : [Session]
        Parent session receiving snapshots, child measurements, and warnings.

    path : str
        Original workspace notebook path whose source is captured.

    correlation_id : str
        Unique token linking the child result to its parent invocation.

    parent_id : str
        Identifier of the parent profiling run.

    store : [WorkspaceStore]
        SDK-backed store owning temporary workspace operations.

    sources : list[[SourceUnit]]
        Complete exported notebook source snapshots.

    instrumented : bool
        Whether a generated Python sibling notebook is ready to execute.

    profile_path : str
        Reserved workspace file where the child exports its profile.

    _owned : list[str]
        Temporary objects created by this instance and eligible for cleanup.

    """

    def __init__(self, session: Session, path: str, correlation_id: str, parent_id: str) -> None:
        """Initialize child invocation and temporary artifact ownership.

        Create workspace objects only when preparation is explicitly performed.

        Parameters
        ----------
        session : [Session]
            Owning profiling session receiving measurements and diagnostics.

        path : str
            File, workspace, or import search path used by this operation.

        correlation_id : str
            Token associating the child profile with its parent invocation.

        parent_id : str
            Identifier of the owning parent profiling run.

        """
        self.session = session
        self.path = path
        self.correlation_id = correlation_id
        self.parent_id = parent_id
        self.store = WorkspaceStore()
        self.sources: list[SourceUnit] = []
        self.instrumented = False
        self.profile_path = ""
        self._owned: list[str] = []

    def prepare(self) -> None:
        """Prepare source and automatic collection for Python notebooks.

        Leave unsupported languages as source-only invocations.

        """
        status = self.store.status(self.path)
        source = self.store.read(self.path, "SOURCE").decode("utf-8")
        python = status.get("language") == "PYTHON" and status.get("object_type") == "NOTEBOOK"
        self.sources = (
            notebook_sources(self.path, source)
            if python
            else [
                SourceUnit(
                    f"notebook://{self.path}#snapshot-{hashlib.sha256(source.encode()).hexdigest()[:12]}",
                    self.path,
                    source,
                    SourceKind.NOTEBOOK,
                )
            ]
        )

        if not python:
            self.session.result.warnings.append(
                f"Child notebook {self.path}: source included; automatic line profiling requires"
                " a Python notebook."
            )
            return

        token = uuid4().hex
        notebook_path = posixpath.join(posixpath.dirname(self.path), f"_linescope_{token}")
        self.profile_path = notebook_path + ".json"
        options = asdict(self.session.config)
        options.update(display="none", output=None)
        context = {
            "path": self.path,
            "correlation_id": self.correlation_id,
            "parent_id": self.parent_id,
            "profile_path": self.profile_path,
            "source": source,
            "options": options,
        }
        # Enum reprs are not standalone Python expressions. JSON also safely
        # quotes source text and paths before they enter the generated code.
        encoded = repr(json.dumps(context, ensure_ascii=True))
        variable = f"_linescope_child_{token}"
        bootstrap = (
            INTERNAL_CELL
            + "try:\n"
            + f"    from linescope.notebooks.remote import start_child as _start_{token}\n"
            + "    import json as _linescope_json\n"
            + f"    {variable} = _start_{token}(_linescope_json.loads({encoded}))\n"
            + "except Exception:\n"
            + f"    {variable} = None\n"
        )
        finalizer = (
            INTERNAL_CELL
            + f"if globals().get({variable!r}) is not None:\n"
            + f"    {variable}.finish()\n"
        )
        body = source.removeprefix("# Databricks notebook source\n")
        instrumented = (
            "# Databricks notebook source\n"
            + bootstrap
            + "\n# COMMAND ----------\n\n"
            + body
            + "\n\n# COMMAND ----------\n\n"
            + finalizer
        )
        self.store.write(self.profile_path, PENDING_PROFILE)
        self._owned.append(self.profile_path)
        self.store.write(notebook_path, instrumented.encode("utf-8"), notebook=True)
        self._owned.append(notebook_path)
        self.path = notebook_path
        self.instrumented = True

    def collect(self) -> ProfileResult:
        """Read and validate the independently collected child profile.

        A pending reserved file means child startup or finalization failed.

        """
        payload = self.store.read(self.profile_path)

        if not payload or payload == PENDING_PROFILE:
            raise ValueError("Child profiling produced no result.")

        result = loads_result(payload)

        if (
            result.root_run.parent_id != self.parent_id
            or result.root_run.metadata.get("correlation_id") != self.correlation_id
        ):
            raise ValueError("Child profile ownership does not match this invocation.")

        return result

    def cleanup(self) -> None:
        """Remove owned workspace objects after success or failure.

        Record cleanup failures without hiding the workload's outcome.

        """
        for path in reversed(self._owned):
            try:
                self.store.delete(path)
            except Exception as error:  # noqa: BLE001
                self.session.result.warnings.append(
                    f"Temporary child artifact cleanup failed for {path} ({type(error).__name__})."
                )

        self._owned.clear()


class ChildCollector:
    """Finalize a child profile at normal completion, notebook exit, or error.

    Preserve notebook exit values and restore owned event hooks and patches.

    Attributes
    ----------
    context : dict[str, Any]
        Parent-supplied source, correlation, output path, and session options.

    session : [Session] | None
        Child profiling session, once startup begins.

    shell : [InteractiveShell] | None
        Child IPython shell whose completion hooks are observed.

    notebook : Any
        Databricks notebook API object whose `exit` is observed.

    _exit : Any
        Original notebook exit callable preserved for restoration.

    _wrapper : Any
        Installed exit observer retained to check patch ownership.

    _had_exit : bool
        Whether `exit` was an instance attribute before patching.

    _finished : bool
        Whether finalization has already claimed cleanup and export.

    _startup_failed : bool
        Whether collection failed to start and only source is available.

    """

    def __init__(self, context: dict[str, Any]) -> None:
        """Initialize child correlation and finalization ownership.

        Defer instrumentation, event hooks, and notebook exit patches until
        startup.

        Parameters
        ----------
        context : dict[str, Any]
            Context or display option used by the operation.

        """
        self.context = context
        self.session: Session | None = None
        self.shell: InteractiveShell | None = None
        self.notebook: type[DBUtils.notebook] | None = None
        self._exit: Callable[..., object] | None = None
        self._wrapper: Callable[..., object] | None = None
        self._had_exit = False
        self._finished = False
        self._startup_failed = False

    def start(self) -> None:
        """Start the configured collector in the child notebook environment.

        Keep the original workspace path in captured source and nested calls.

        """
        from IPython import get_ipython

        from linescope.api import Session

        self.shell = get_ipython()
        if self.shell is None:
            raise RuntimeError("Notebook cell hooks are unavailable.")

        self.session = Session(**self.context["options"])
        self.session._notebook_path = self.context["path"]
        self.session._notebook_sources = notebook_sources(
            self.context["path"], self.context["source"]
        )
        run = self.session.result.root_run
        run.parent_id = self.context["parent_id"]
        run.metadata["correlation_id"] = self.context["correlation_id"]
        run.name = self.context["path"]
        run.source = self.session._notebook_sources[0]
        self.session.start()

        if self.shell is not None:
            self.shell.events.register("post_run_cell", self._post_cell)
            dbutils: DBUtils | None = self.shell.user_ns.get("dbutils")
            self.notebook = getattr(dbutils, "notebook", None)
            original = getattr(self.notebook, "exit", None)

            if self.notebook is not None and callable(original):
                self._exit = original
                self._had_exit = "exit" in getattr(self.notebook, "__dict__", {})

                @functools.wraps(original)
                def exit_notebook(*args: object, **kwargs: object) -> object:
                    """Finalize collection before forwarding a notebook exit.

                    Preserve the original exit arguments and return behavior.

                    Parameters
                    ----------
                    *args : object
                        Positional arguments or syntax parameters forwarded to
                        the operation.

                    **kwargs : object
                        Keyword arguments forwarded without changing their
                        values.

                    Returns
                    -------
                    object
                        Original exit result, when the notebook API returns
                        normally.

                    """
                    self.finish()
                    return original(*args, **kwargs)

                self._wrapper = exit_notebook
                setattr(self.notebook, "exit", exit_notebook)  # noqa: B010

    def _post_cell(self, result: ExecutionResult) -> None:
        """Finalize child collection after a notebook cell execution error.

        Retain source snapshots and restore owned instrumentation before
        exporting failure metadata.

        Parameters
        ----------
        result : ExecutionResult
            Cell execution result or model being processed.

        """
        if getattr(result, "error_before_exec", None) or getattr(result, "error_in_exec", None):
            self.finish(failed=True)

    def _restore_hooks(self) -> None:
        """Restore owned child completion and notebook exit hooks.

        Retain cleanup diagnostics without replacing the workload's original
        outcome.

        """
        try:
            if self.shell is not None:
                self.shell.events.unregister("post_run_cell", self._post_cell)
        except (KeyError, ValueError):
            pass
        except Exception as error:  # noqa: BLE001
            if self.session is not None:
                self.session.result.warnings.append(
                    f"Child completion hook cleanup failed ({type(error).__name__})."
                )

        try:
            if (
                self.notebook is not None
                and self._wrapper is not None
                and self.notebook.exit is self._wrapper
            ):
                if self._had_exit:
                    setattr(self.notebook, "exit", self._exit)  # noqa: B010
                else:
                    del self.notebook.exit
        except Exception as error:  # noqa: BLE001
            if self.session is not None:
                self.session.result.warnings.append(
                    f"Child exit hook cleanup failed ({type(error).__name__})."
                )

    def finish(self, *, failed: bool = False) -> None:
        """Stop and export once while preserving the workload outcome.

        Failed or unavailable collection still carries the captured source.

        """
        if self._finished:
            return

        self._finished = True
        self._restore_hooks()

        try:
            if self.session is not None and self.session.state == SessionState.RUNNING:
                if failed:
                    self.session.result.root_run.status = RunStatus.FAILED

                result = self.session.stop()
                if self._startup_failed:
                    result.capabilities = UNAVAILABLE
                    result.root_run.lines.clear()
                    result.root_run.functions.clear()
                    result.root_run.spark_executions.clear()
                    result.warnings.append(
                        "Child profiler startup failed; only source was captured."
                    )
            else:
                sources = notebook_sources(self.context["path"], self.context["source"])
                result = ProfileResult(
                    ProfileRun(
                        parent_id=self.context["parent_id"],
                        name=self.context["path"],
                        source=sources[0],
                        metadata={"correlation_id": self.context["correlation_id"]},
                    ),
                    {source.id: source for source in sources},
                    self.context["options"]["backend"],
                    UNAVAILABLE,
                    warnings=["Child profiler startup was unavailable; only source was captured."],
                )

            WorkspaceStore().write(
                self.context["profile_path"], dumps_result(result), overwrite=True
            )
        except Exception:  # noqa: BLE001
            # The parent reports a missing/invalid artifact. Never replace the
            # original notebook result with an instrumentation failure.
            pass


def start_child(context: dict[str, Any]) -> ChildCollector:
    """Bootstrap a generated notebook and retain cleanup ownership.

    Collection failures leave the workload runnable and are reported through
    the reserved profile artifact.

    """
    collector = ChildCollector(context)

    try:
        collector.start()
    except Exception:  # noqa: BLE001
        collector._startup_failed = True
        collector.finish()

    return collector
