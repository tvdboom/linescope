"""LineScope.

Author: Mavs
Description: IPython cell snapshots, multi-cell sessions, and profiling magics.

"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import inspect
import linecache
import re
import shlex
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from linescope.enums import DisplayMode, RunStatus, SourceKind
from linescope.model import ProfileRun, SourceLocation, SourceUnit
from linescope.notebooks.databricks import DatabricksIntegration, resolve_notebook_path
from linescope.notebooks.remote import INTERNAL_CELL

if TYPE_CHECKING:
    from databricks.sdk.runtime.dbutils_stub import dbutils as DBUtils
    from IPython.core.interactiveshell import ExecutionInfo, ExecutionResult, InteractiveShell

    from linescope.api import Session


class NotebookIntegration:
    """Capture executed notebook cells during a profiling session.

    Parameters
    ----------
    session : [Session]
        Owning session. Cell hooks never display full reports unless its
        display mode is explicitly `"cell"`.

    shell : [InteractiveShell] | None, default=None
        IPython shell. Detected automatically when omitted.

    dbutils : [DBUtils] | None, default=None
        Databricks utilities; defaults to the shell's user namespace.

    Attributes
    ----------
    session : [Session]
        Owning profiling session receiving captured cells and references.

    shell : [InteractiveShell] | None
        Detected or supplied IPython shell, when available.

    dbutils : object | None
        Databricks utilities from the shell or constructor, when available.

    databricks : [DatabricksIntegration] | None
        Owned observer for separate Databricks notebook calls.

    path : str
        Workspace notebook path or the `interactive` fallback label.

    _snapshots : list[[SourceUnit]]
        Precaptured notebook cells available for runtime alias reuse.

    _used_snapshots : set[str]
        Precaptured snapshot identifiers already matched to runtime cells.

    _active : bool
        Whether source capture hooks are currently installed.

    _registered : list[tuple[str, Any]]
        Owned shell event registrations to remove during cleanup.

    _compiler_cache : Any
        Original compiler cache callable preserved for restoration.

    _compiler_wrapper : Any
        Installed cache observer retained to check patch ownership.

    _compiler_owned : bool
        Whether the compiler already had an instance `cache` attribute.

    _cell_info : Any
        Most recent pre-execution cell metadata from the shell.

    _inline_captured : set[str]
        Snapshot identifiers already inspected for inline `%run` references.

    See Also
    --------
    - linescope:Session
    - linescope.notebooks:ChildContext
    - linescope.notebooks:merge_child

    Examples
    --------
    ```pycon
    >>> from linescope import Session
    >>> from linescope.notebooks import NotebookIntegration
    >>> session = Session(backend="trace", display="none")
    >>> notebook = NotebookIntegration(session)
    >>> source = notebook.capture("answer = 42", cell_id="example")
    >>> source.kind
    'notebook'
    ```

    """

    def __init__(
        self,
        session: Session,
        shell: InteractiveShell | None = None,
        dbutils: DBUtils | None = None,
    ) -> None:
        """Initialize cell capture, compiler alias, and notebook observer state.

        Use supplied shell context or detect an available IPython shell.

        Parameters
        ----------
        session : [Session]
            Owning profiling session receiving measurements and diagnostics.

        shell : [InteractiveShell] | None, default=None
            Existing notebook shell, when available.

        dbutils : [DBUtils] | None, default=None
            Databricks notebook utilities, when available.

        """
        self.session = session

        if shell is None:
            try:
                from IPython import get_ipython

                shell = get_ipython()
            except ImportError:
                pass

        self.shell: InteractiveShell | None = shell
        self.dbutils: DBUtils | None = dbutils or getattr(shell, "user_ns", {}).get("dbutils")
        self.databricks = DatabricksIntegration(session, self.dbutils) if self.dbutils else None
        self.path = (
            getattr(session, "_notebook_path", None)
            or (self.databricks.path if self.databricks else None)
            or "interactive"
        )
        if self.databricks and getattr(session, "_notebook_path", None):
            self.databricks.path = self.path

        self._snapshots: list[SourceUnit] = getattr(session, "_notebook_sources", [])
        self._used_snapshots: set[str] = set()
        self._active = False
        self._registered: list[
            tuple[str, Callable[[ExecutionInfo], None] | Callable[[ExecutionResult], None]]
        ] = []
        self._compiler_cache: Callable[..., str] | None = None
        self._compiler_wrapper: Callable[..., str] | None = None
        self._compiler_owned = False
        self._cell_info: ExecutionInfo | None = None
        self._inline_captured: set[str] = set()

    def capture(
        self,
        source: str,
        *,
        cell_id: str | None = None,
        filename: str | None = None,
    ) -> SourceUnit:
        """Snapshot a cell and register its compiler filename as an alias.

        Parameters
        ----------
        source : str
            Complete source text as executed.

        cell_id : str | None, default=None
            Frontend cell ID or execution count.

        filename : str | None, default=None
            Python compiler filename used for line attribution.

        Returns
        -------
        [SourceUnit]
            Immutable notebook source snapshot.

        Examples
        --------
        ```pycon
        >>> from linescope import Session
        >>> from linescope.notebooks import NotebookIntegration
        >>> notebook = NotebookIntegration(Session(backend="trace"))
        >>> source = notebook.capture("answer = 42", cell_id="demo")
        >>> source.source
        'answer = 42'
        ```

        """
        matching = [unit for unit in self._snapshots if unit.source == source.rstrip("\n")]
        unit = next((unit for unit in matching if unit.id not in self._used_snapshots), None)

        if unit is None and matching:
            unit = matching[0]

        if unit is None:
            digest = hashlib.sha256(source.encode()).hexdigest()[:12]
            identity = cell_id or str(getattr(self.shell, "execution_count", 1))
            source_id = f"notebook://{self.path}#cell-{identity}-{digest}"
            unit = SourceUnit(
                source_id, f"{self.path} · cell {identity}", source, SourceKind.NOTEBOOK
            )

        self._used_snapshots.add(unit.id)
        self.session.registry.register(unit, filename=filename)

        if filename is not None and filename not in linecache.cache:
            linecache.cache[filename] = (
                len(source),
                None,
                source.splitlines(keepends=True),
                filename,
            )

        self._inline_references(unit)
        return unit

    def _inline_references(self, unit: SourceUnit) -> None:
        """Attach inline `%run` references found in a captured cell.

        Keep their execution owned by the parent rather than fabricating child
        measurements.

        Parameters
        ----------
        unit : [SourceUnit]
            Captured source snapshot being inspected.

        """
        if unit.id in self._inline_captured:
            return

        self._inline_captured.add(unit.id)

        for line_number, line in enumerate(unit.source.splitlines(), 1):
            match = re.match(r"\s*%run\s+(.+)", line)

            if not match:
                continue

            try:
                args = shlex.split(match.group(1))
            except ValueError:
                continue

            if not args or args[0].startswith("-"):
                continue

            path = resolve_notebook_path(args[0], self.path)
            source = SourceUnit(
                f"notebook://{path}",
                path,
                "# Inline notebook source unavailable.\n",
                SourceKind.NOTEBOOK,
            )
            self.session.registry.register(source)
            child = ProfileRun(
                source=source,
                name=path,
                parent_id=self.session.result.root_run.id,
                status=RunStatus.REFERENCE,
                metadata={
                    "inline": True,
                    "collection": "inline reference; execution belongs to parent",
                },
            )
            self.session.add_child_run(child, SourceLocation(unit.id, line_number))

    def _pre_run_cell(self, info: ExecutionInfo) -> None:
        """Remember raw cell metadata before execution.

        Capture source directly when compiler alias observation is unavailable.

        Parameters
        ----------
        info : ExecutionInfo
            Shell metadata for the cell about to execute.

        """
        raw = getattr(info, "raw_cell", "")

        if not isinstance(raw, str):
            return

        self._cell_info = info

        if self._compiler_wrapper is None and not raw.startswith(INTERNAL_CELL):
            self.capture(raw, cell_id=getattr(info, "cell_id", None))

    def _observe_compiler(self) -> None:
        """Install a reversible observer around the shell's compiler cache.

        Preserve the original cache behavior and correlate runtime filenames
        with cell snapshots.

        """
        if self.shell is None:
            return

        compiler = self.shell.compile
        original = compiler.cache
        self._compiler_cache = original
        self._compiler_owned = "cache" in getattr(compiler, "__dict__", {})

        def cache(transformed_code: str, number: int = 0, raw_code: str | None = None) -> str:
            """Cache compiled cell source and register its runtime filename.

            Preserve the compiler's result when optional source metadata capture
            fails.

            Parameters
            ----------
            transformed_code : str
                Cell source after the shell applies input transformations.

            number : int, default=0
                Cell execution number used by the shell compiler.

            raw_code : str | None, default=None
                Original cell source before input transformations, when
                available.

            Returns
            -------
            str
                Original compiler filename, retained for runtime source
                attribution.

            """
            filename = original(transformed_code, number, raw_code=raw_code)

            if self._active:
                raw = raw_code if raw_code is not None else transformed_code
                info = self._cell_info
                cell_id = (
                    getattr(info, "cell_id", None)
                    if getattr(info, "raw_cell", None) == raw
                    else None
                )

                try:
                    if not raw.startswith(INTERNAL_CELL):
                        self.capture(raw, cell_id=cell_id or str(number), filename=filename)
                except Exception as error:  # noqa: BLE001
                    self.session.result.warnings.append(
                        f"Notebook source metadata unavailable ({type(error).__name__})."
                    )

            return filename

        try:
            setattr(compiler, "cache", cache)  # noqa: B010
            self._compiler_wrapper = cache
        except (AttributeError, TypeError):
            self.session.result.warnings.append(
                "Notebook compiler aliases unavailable in this shell."
            )

    def _capture_current_cell(self) -> None:
        """Capture the currently executing notebook cell from cached source.

        Release inspected frame references after walking the active stack.

        """
        history = getattr(getattr(self.shell, "history_manager", None), "input_hist_raw", [])
        frame = inspect.currentframe()

        try:
            while frame is not None:
                filename = frame.f_code.co_filename
                identity = self._cell_identity(filename)

                if identity is not None:
                    cached = "".join(linecache.getlines(filename))
                    raw = history[-1] if history else ""
                    source = (
                        (raw or cached)
                        if filename.startswith("<ipython-input-")
                        else (cached or raw)
                    )

                    if source and not source.startswith(INTERNAL_CELL):
                        self.capture(source, cell_id=identity, filename=filename)
                        return

                frame = frame.f_back
        finally:
            del frame

    @staticmethod
    def _cell_identity(filename: str) -> str | None:
        """Extract a stable cell label from a recognized compiler filename.

        Return None for filenames outside supported IPython and Databricks
        formats.

        Parameters
        ----------
        filename : str
            Runtime filename being observed or resolved.

        Returns
        -------
        str | None
            Recognized cell label, or None for other runtime filenames.

        """
        ipython = re.match(r"<ipython-input-(\d+)-", filename)

        if ipython:
            return ipython.group(1)

        databricks = re.search(r"(?:^|[/\\])<?command-([A-Za-z0-9_.-]+)>?$", filename)
        return databricks.group(1) if databricks else None

    def _capture_existing_definitions(self) -> None:
        # Functions defined before start() retain their compiler filename. Read
        # only cached notebook sources; never invoke user properties or imports.
        """Capture cached notebook source for existing definitions.

        Inspect raw class dictionaries without invoking user properties or
        importing code.

        """
        seen: set[str] = set()
        namespace = getattr(self.shell, "user_ns", {})
        candidates = list(namespace.values())

        for value in list(candidates):
            if inspect.isclass(value):
                candidates.extend(type.__getattribute__(value, "__dict__").values())

        for value in candidates:
            if isinstance(value, (staticmethod, classmethod)):
                value = value.__func__
            elif inspect.ismethod(value):
                value = value.__func__

            if not inspect.isfunction(value):
                continue

            filename = value.__code__.co_filename
            identity = self._cell_identity(filename)

            if identity is None or filename in seen:
                continue

            seen.add(filename)
            source = "".join(linecache.getlines(filename))

            if source:
                self.capture(source, cell_id=identity, filename=filename)

    def _post_run_cell(self, result: ExecutionResult) -> None:
        """Display the current report when live cell display is enabled.

        Leave ordinary notebook-wide collection to display once at stop.

        Parameters
        ----------
        result : ExecutionResult
            Cell execution result or model being processed.

        """
        del result
        if self._active and self.session.config.display == DisplayMode.CELL:
            self.session.show()

    def start(self) -> None:
        """Register cell hooks and optional Databricks invocation observation.

        Capture source at execution time so reports remain usable after cells
        change.

        """
        if self._active:
            return

        self._active = True

        for unit in self._snapshots:
            self.session.registry.register(unit)
            self._inline_references(unit)

        if self.shell is not None:
            self._observe_compiler()

            for event, callback in (
                ("pre_run_cell", self._pre_run_cell),
                ("post_run_cell", self._post_run_cell),
            ):
                self.shell.events.register(event, callback)
                self._registered.append((event, callback))

            # start() commonly executes inside a cell, after its pre-run event.
            self._capture_current_cell()
            self._capture_existing_definitions()

        if self.databricks:
            self.databricks.start()

    def stop(self) -> None:
        """Unregister only hooks owned by this session.

        Release compiler and cell callbacks without removing unrelated
        extensions.

        """
        self._active = False

        if self.shell is not None:
            for event, callback in self._registered:
                try:
                    self.shell.events.unregister(event, callback)
                except (ValueError, KeyError):
                    pass

        self._registered.clear()

        if (
            self.shell is not None
            and self._compiler_wrapper is not None
            and self.shell.compile.cache is self._compiler_wrapper
        ):
            if self._compiler_owned:
                setattr(self.shell.compile, "cache", self._compiler_cache)  # noqa: B010
            else:
                del self.shell.compile.cache

        if self.databricks:
            self.databricks.stop()


def load_ipython_extension(shell: InteractiveShell) -> None:
    """Register `%%profile` and `%%linescope` cell magics.

    Parameters
    ----------
    shell : [InteractiveShell]
        Shell supplied by `%load_ext linescope`.

    """
    previous = getattr(shell, "_linescope_magic_previous", None)

    def profile_magic(line: str, cell: str) -> None:
        """Run a cell inside a profiling scope using parsed magic options.

        Preserve the shell namespace and release session instrumentation after
        errors.

        Parameters
        ----------
        line : str
            One-based source line used by the report link.

        cell : str
            Original cell source executed by the profiling magic.

        """
        from linescope import profile

        parser = argparse.ArgumentParser(prog="%%profile")
        parser.add_argument("--backend")
        parser.add_argument("--memory", action="store_true", default=None)
        parser.add_argument("--gpu", action="store_true", default=None)
        parser.add_argument("--inline", action="store_true", default=None)
        parser.add_argument("--spark", action="store_true", default=None)
        parser.add_argument("--include", action="append")
        parser.add_argument("--exclude", action="append")
        parser.add_argument("--display", choices=tuple(DisplayMode))
        options = {
            key: value
            for key, value in vars(parser.parse_args(shlex.split(line))).items()
            if value is not None
        }
        session = profile(**options)

        # Executing through IPython preserves magics, shell escapes, display,
        # and its ordinary exception behavior.
        with session:
            integration = NotebookIntegration(session, shell)

            try:
                transformed = shell.transform_cell(cell)
                filename = shell.compile.cache(transformed, shell.execution_count, raw_code=cell)
            except Exception:  # noqa: BLE001
                filename = f"<linescope-cell-{uuid4().hex}>"

            integration.capture(cell, filename=filename)
            result = shell.run_cell(cell, store_history=False)

            if getattr(result, "error_before_exec", None) or getattr(
                result, "error_in_exec", None
            ):
                session.result.root_run.status = RunStatus.FAILED

        return None

    if previous is None:
        magics = shell.magics_manager.magics["cell"]
        setattr(  # noqa: B010
            shell,
            "_linescope_magic_previous",
            {name: magics.get(name) for name in ("profile", "linescope")},
        )

    # IPython's decorator retains the unbound method signature in its types.
    register_magic = cast(
        Callable[[Callable[..., object], str, str], None], shell.register_magic_function
    )
    register_magic(profile_magic, "cell", "profile")
    register_magic(profile_magic, "cell", "linescope")
    setattr(shell, "_linescope_magic_installed", profile_magic)  # noqa: B010


def unload_ipython_extension(shell: InteractiveShell) -> None:
    """Restore cell magics that existed before loading the extension.

    Release the registrations saved by the matching load operation.

    """
    previous = getattr(shell, "_linescope_magic_previous", {})
    installed = getattr(shell, "_linescope_magic_installed", None)

    for name, magic in previous.items():
        if shell.magics_manager.magics["cell"].get(name) is not installed:
            continue

        if magic is None:
            shell.magics_manager.magics["cell"].pop(name, None)
        else:
            shell.register_magic_function(magic, "cell", name)

    if hasattr(shell, "_linescope_magic_previous"):
        del shell._linescope_magic_previous

    if hasattr(shell, "_linescope_magic_installed"):
        del shell._linescope_magic_installed
