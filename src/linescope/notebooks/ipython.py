"""LineScope.

Author: Mavs
Description: IPython cell snapshots, multi-cell sessions, and profiling magics.

"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import linecache
import re
import shlex
from typing import Any
from uuid import uuid4

from linescope.enums import DisplayMode, RunStatus, SourceKind
from linescope.model import ProfileRun, SourceLocation, SourceUnit
from linescope.notebooks.databricks import DatabricksIntegration, resolve_notebook_path


class NotebookIntegration:
    """Capture executed notebook cells during a profiling session.

    Parameters
    ----------
    session : [Session]
        Owning session. Cell hooks never display full reports unless its
        display mode is explicitly `"cell"`.

    shell : object | None, default=None
        IPython shell. Detected automatically when omitted.

    dbutils : object | None, default=None
        Databricks utilities; defaults to the shell's user namespace.

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

    def __init__(self, session: Any, shell: Any = None, dbutils: Any = None) -> None:
        self.session = session

        if shell is None:
            try:
                from IPython import get_ipython

                shell = get_ipython()
            except ImportError:
                pass

        self.shell = shell
        self.dbutils = dbutils or getattr(shell, "user_ns", {}).get("dbutils")
        self.databricks = DatabricksIntegration(session, self.dbutils) if self.dbutils else None
        self.path = (self.databricks.path if self.databricks else None) or "interactive"
        self._active = False
        self._registered: list[tuple[str, Any]] = []
        self._compiler_cache: Any = None
        self._compiler_wrapper: Any = None
        self._compiler_owned = False
        self._cell_info: Any = None
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
        digest = hashlib.sha256(source.encode()).hexdigest()[:12]
        identity = cell_id or str(getattr(self.shell, "execution_count", 1))
        source_id = f"notebook://{self.path}#cell-{identity}-{digest}"
        unit = SourceUnit(source_id, f"{self.path} · cell {identity}", source, SourceKind.NOTEBOOK)
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

    def _pre_run_cell(self, info: Any) -> None:
        raw = getattr(info, "raw_cell", "")

        if not isinstance(raw, str):
            return

        self._cell_info = info

        if self._compiler_wrapper is None:
            self.capture(raw, cell_id=getattr(info, "cell_id", None))

    def _observe_compiler(self) -> None:
        compiler = self.shell.compile
        original = compiler.cache
        self._compiler_cache = original
        self._compiler_owned = "cache" in getattr(compiler, "__dict__", {})

        def cache(transformed_code: str, number: int = 0, raw_code: str | None = None) -> str:
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
                    self.capture(raw, cell_id=cell_id or str(number), filename=filename)
                except Exception as error:  # noqa: BLE001
                    self.session.result.warnings.append(
                        f"Notebook source metadata unavailable ({type(error).__name__})."
                    )

            return filename

        try:
            compiler.cache = cache
            self._compiler_wrapper = cache
        except (AttributeError, TypeError):
            self.session.result.warnings.append(
                "Notebook compiler aliases unavailable in this shell."
            )

    def _capture_current_cell(self) -> None:
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

                    if source:
                        self.capture(source, cell_id=identity, filename=filename)
                        return

                frame = frame.f_back
        finally:
            del frame

    @staticmethod
    def _cell_identity(filename: str) -> str | None:
        ipython = re.match(r"<ipython-input-(\d+)-", filename)

        if ipython:
            return ipython.group(1)

        databricks = re.search(r"(?:^|[/\\])<?command-([A-Za-z0-9_.-]+)>?$", filename)
        return databricks.group(1) if databricks else None

    def _capture_existing_definitions(self) -> None:
        # Functions defined before start() retain their compiler filename. Read
        # only cached notebook sources; never invoke user properties or imports.
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

    def _post_run_cell(self, result: Any) -> None:
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

        for event, callback in self._registered:
            try:
                self.shell.events.unregister(event, callback)
            except (ValueError, KeyError):
                pass

        self._registered.clear()

        if (
            self._compiler_wrapper is not None
            and self.shell.compile.cache is self._compiler_wrapper
        ):
            if self._compiler_owned:
                self.shell.compile.cache = self._compiler_cache
            else:
                del self.shell.compile.cache

        if self.databricks:
            self.databricks.stop()


def load_ipython_extension(shell: Any) -> None:
    """Register `%%profile` and `%%linescope` cell magics.

    Parameters
    ----------
    shell : [InteractiveShell]
        Shell supplied by `%load_ext linescope`.

    """
    previous = getattr(shell, "_linescope_magic_previous", None)

    def profile_magic(line: str, cell: str) -> Any:
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
        shell._linescope_magic_previous = {
            name: magics.get(name) for name in ("profile", "linescope")
        }

    shell.register_magic_function(profile_magic, "cell", "profile")
    shell.register_magic_function(profile_magic, "cell", "linescope")
    shell._linescope_magic_installed = profile_magic


def unload_ipython_extension(shell: Any) -> None:
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
