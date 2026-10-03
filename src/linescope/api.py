"""LineScope.

Author: Mavs
Description: Session lifecycle, public ergonomics, and normalized collection pipeline.

"""

from __future__ import annotations

import ast
import re
import threading
import warnings
import webbrowser
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from time import perf_counter_ns
from types import TracebackType
from typing import Any

from linescope.backends.base import ProfilerBackend, create_backend
from linescope.config import Config, resolve_config
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SparkExecution,
)
from linescope.source import SourceRegistry, build_navigation

_session_lock = threading.Lock()


class Session:
    """Collect one source profiling run and render portable HTML.

    Parameters
    ----------
    config : Config | None, default=None
        Prevalidated configuration. Mutually exclusive with keyword options.

    **options
        Explicit [Config] fields overriding global and project settings.

    Attributes
    ----------
    result : ProfileResult
        Captured source, run relationships, and finalized measurements after stop.

    state : str
        `created`, `running`, or `stopped`. A session can run only once.

    See Also
    --------
    - linescope:configure
    - linescope:profile
    - linescope.model:ProfileResult

    Examples
    --------
    ```pycon
    >>> from linescope import profile
    >>> with profile(backend="trace", display="none", notebooks=False) as session:
    ...     value = sum(range(10))
    >>> value
    45
    >>> session.state
    'stopped'
    ```
    """

    def __init__(self, config: Config | None = None, **options: Any) -> None:
        if config is not None and options:
            raise TypeError("Pass a Config or keyword options, not both")
        self.config = config if config is not None else resolve_config(**options)
        self.registry = SourceRegistry(self.config.root, self.config.include, self.config.exclude)
        self.result = ProfileResult(
            ProfileRun(), self.registry.sources, self.config.backend, BackendCapabilities()
        )
        self.state = "created"
        self._backend: ProfilerBackend | None = None
        self._resources: ExitStack | None = None
        self._started = 0
        self._owner = 0
        self._references: dict[tuple[str, int], LineStats] = {}

    def __enter__(self) -> Session:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        successful_exit = isinstance(exception, SystemExit) and exception.code in (None, 0)
        if exception_type is not None and not successful_exit:
            self.result.root_run.status = "failed"
        try:
            self.stop()
        except Exception as error:
            if exception is None:
                raise
            # Profiling cleanup must never replace the workload's original error.
            warnings.warn(f"LineScope cleanup failed: {error}", RuntimeWarning, stacklevel=2)
        return False

    def start(self) -> Session:
        """Begin collection and install available notebook and Spark observers.

        Returns
        -------
        Session
            This running session.

        Raises
        ------
        RuntimeError
            If another session is running or this session has already started.
        """
        if self.state != "created":
            raise RuntimeError("A session can only start once; create a new profile()")
        if not _session_lock.acquire(blocking=False):
            raise RuntimeError("Another profiling session is already running in this process")
        resources = ExitStack()
        resources.callback(_session_lock.release)
        self._owner = threading.get_ident()
        try:
            self._backend = create_backend(
                self.config.backend,
                accepts=self.registry.accepts,
                on_source=self.registry.snapshot,
                memory=self.config.memory,
                root=str(self.registry.root),
            )
            self.result.capabilities = self._backend.capabilities
            self.result.backend = self._backend.name
            if self.config.notebooks:
                from linescope.notebooks.ipython import NotebookIntegration

                integration = NotebookIntegration(self)
                resources.callback(integration.stop)
                integration.start()
            if self.config.spark is not False:
                from linescope.spark import SparkIntegration

                spark = SparkIntegration(self)
                resources.callback(spark.stop)
                spark.start()
            self._started = perf_counter_ns()
            resources.callback(self._backend.stop)
            self._backend.start()
            self.state = "running"
            self._resources = resources
        except BaseException:
            resources.close()
            raise
        return self

    def _refresh(self) -> None:
        if self._backend is None:
            return
        raw = self._backend.result()
        lines = deepcopy(self._references)
        for measurement in raw.lines:
            source = self.registry.snapshot(measurement.filename)
            if source is None or measurement.line < 1:
                continue
            key = (source.id, measurement.line)
            line = lines.setdefault(key, LineStats(SourceLocation(*key)))
            if measurement.wall_time_ns is not None:
                line.wall_time_ns = (line.wall_time_ns or 0) + measurement.wall_time_ns
            if measurement.hits is not None:
                line.hits = (line.hits or 0) + measurement.hits
            if measurement.memory is not None:
                if line.memory is None:
                    line.memory = deepcopy(measurement.memory)
                else:
                    if measurement.memory.delta_bytes is not None:
                        line.memory.delta_bytes = (
                            line.memory.delta_bytes or 0
                        ) + measurement.memory.delta_bytes
                    if measurement.memory.peak_bytes is not None:
                        line.memory.peak_bytes = max(
                            line.memory.peak_bytes or 0, measurement.memory.peak_bytes
                        )
        symbols, navigation = build_navigation(self.registry.sources)
        for key, calls in navigation.items():
            lines.setdefault(key, LineStats(SourceLocation(*key))).calls = calls
        self.result.symbols = symbols
        self.result.root_run.lines = sorted(
            lines.values(), key=lambda item: (item.location.source_id, item.location.line)
        )
        self.result.root_run.functions = self._function_stats(lines, raw.function_calls)
        for warning in raw.warnings:
            if warning not in self.result.warnings:
                self.result.warnings.append(warning)
        if self.state == "running":
            self.result.root_run.elapsed_ns = perf_counter_ns() - self._started

    def _function_stats(
        self,
        lines: dict[tuple[str, int], LineStats],
        calls: dict[tuple[str, str, int], int],
    ) -> list[FunctionStats]:
        functions = []
        for unit in self.registry.sources.values():
            source = unit.source
            if unit.kind == "notebook":
                source = "\n".join(
                    re.sub(r"^(\s*)[!%]", r"\1#", line) for line in source.split("\n")
                )
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            nodes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for definition in self.result.symbols:
                if definition.source_id != unit.id or definition.kind not in (
                    "function",
                    "method",
                ):
                    continue
                node = next((node for node in nodes if node.lineno == definition.line), None)
                if node is None:
                    continue
                nested = [
                    item
                    for item in ast.walk(node)
                    if item is not node
                    and isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                measurements = [
                    value.wall_time_ns
                    for (source_id, number), value in lines.items()
                    if source_id == unit.id
                    and node.body[0].lineno <= number <= (node.end_lineno or node.lineno)
                    and not any(
                        item.lineno <= number <= (item.end_lineno or item.lineno)
                        for item in nested
                    )
                    and value.wall_time_ns is not None
                ]
                count = None
                if self.result.capabilities.hit_counts:
                    count = 0
                    first = min([node.lineno, *[d.lineno for d in node.decorator_list]])
                    for (filename, name, first_line), value in calls.items():
                        source = self.registry.snapshot(filename)
                        if (
                            source is not None
                            and source.id == unit.id
                            and first_line == first
                            and name.rsplit(".", 1)[-1] == node.name
                        ):
                            count += value
                functions.append(
                    FunctionStats(
                        unit.id,
                        definition.qualified_name,
                        definition.line,
                        sum(measurements) if measurements else None,
                        count,
                    )
                )
        return functions

    def stop(self) -> ProfileResult:
        """Finalize measurements, restore instrumentation, and display when configured.

        Returns
        -------
        ProfileResult
            Completed run. Repeated calls return the same result without redisplay.
        """
        if self.state == "stopped":
            return self.result
        if self.state != "running":
            raise RuntimeError("Start the session before stopping it")
        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")
        self.result.root_run.elapsed_ns = perf_counter_ns() - self._started
        try:
            if self._resources is not None:
                self._resources.close()
        finally:
            self.state = "stopped"
        self._refresh()
        if self.config.display != "none":
            self.show()
        return self.result

    def add_spark_execution(
        self, execution: SparkExecution, locations: list[SourceLocation]
    ) -> None:
        """Associate one observed Spark execution with any number of source lines."""
        self.result.root_run.spark_executions.append(execution)
        for location in locations:
            key = (location.source_id, location.line)
            line = self._references.setdefault(key, LineStats(location))
            if execution.id not in line.spark_executions:
                line.spark_executions.append(execution.id)

    def add_child_run(self, run: ProfileRun, location: SourceLocation | None) -> None:
        """Attach a separate notebook invocation without fabricating its internal data."""
        run.parent_id = self.result.root_run.id
        self.result.root_run.children.append(run)
        if run.source is not None:
            self.registry.register(run.source)
        if location is not None:
            key = (location.source_id, location.line)
            line = self._references.setdefault(key, LineStats(location))
            line.notebook_runs.append(run.id)

    def html(self) -> str:
        """Return the complete self-contained report as HTML."""
        if self.state == "created":
            raise RuntimeError("Start the session before creating a report")
        if self.state == "running":
            self._refresh()
        from linescope.render import render_html

        return render_html(self.result)

    def save(self, path: str | Path) -> Path:
        """Write an HTML snapshot without opening a browser.

        Parameters
        ----------
        path : str | Path
            Destination filename. Missing parent directories are created.

        Returns
        -------
        Path
            Absolute path to the saved report.
        """
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.html(), encoding="utf-8")
        return destination

    def show(self) -> str:
        """Display HTML inline in notebooks, or save and open it in a browser.

        Returns
        -------
        str
            The self-contained HTML used for display.
        """
        html = self.html()
        try:
            from IPython import get_ipython
            from IPython.display import HTML, display
        except ImportError:
            shell = None
        else:
            shell = get_ipython()
        if shell is not None:
            from html import escape

            # An iframe isolates report CSS and scripts from the notebook itself.
            display(
                HTML(
                    f'<iframe title="LineScope report" srcdoc="{escape(html, quote=True)}" style="width:100%;height:760px;border:0" sandbox="allow-scripts allow-same-origin"></iframe>'
                )
            )
        else:
            destination = Path(self.config.output).expanduser().resolve()
            destination.write_text(html, encoding="utf-8")
            webbrowser.open(destination.as_uri())
        return html


class ProfileController:
    """Offer callable contexts and explicit notebook-wide start/stop ergonomics.

    See Also
    --------
    - linescope:configure
    - linescope.model:ProfileResult
    - linescope:Session
    """

    def __init__(self) -> None:
        self._session: Session | None = None

    def __call__(self, **options: Any) -> Session:
        """Create a context-managed session using explicit [Config] options.

        Parameters
        ----------
        **options
            Configuration fields overriding process and project defaults.
            Collection begins when entering the context or calling `start`.

        Returns
        -------
        Session
            A new session in the `created` state.

        Raises
        ------
        TypeError
            If an option name or value type is invalid.
        ValueError
            If an option value is outside its supported range.

        Examples
        --------
        ```pycon
        from linescope import profile
        session = profile(backend="trace", display="none")
        session.state
        ```
        """
        return Session(**options)

    def start(self, **options: Any) -> Session:
        """Start a new session using explicit [Config] options.

        Parameters
        ----------
        **options
            Configuration fields for the new session. Use `display="none"`
            to collect without displaying a report automatically at stop.

        Returns
        -------
        Session
            The running session retained by this controller.

        Raises
        ------
        RuntimeError
            If a session is already running or the collector cannot start.
        ImportError
            If the selected collector's optional dependency is missing.
        TypeError
            If an option name or value type is invalid.
        ValueError
            If an option value or requested capability is unsupported.

        Examples
        --------
        ```pycon
        from linescope import ProfileController
        controller = ProfileController()
        session = controller.start(
            backend="trace", display="none", notebooks=False, spark=False
        )
        total = sum(range(10))
        result = controller.stop()
        (total, session.state, result.backend)
        ```
        """
        if self._session is not None and self._session.state == "running":
            raise RuntimeError("The current session is already running")
        session = Session(**options)
        session.start()
        self._session = session
        return session

    def _current(self) -> Session:
        if self._session is None:
            raise RuntimeError("Call profile.start() first")
        return self._session

    def stop(self) -> ProfileResult:
        """Stop and return the current explicit session's result.

        Instrumentation is restored before any configured report display.
        Repeated calls return the completed result without displaying again.

        Returns
        -------
        ProfileResult
            Captured source snapshots, normalized measurements, and run tree.

        Raises
        ------
        RuntimeError
            If no explicit session has started, or a running session is
            stopped from a different thread than the one that started it.
        """
        return self._current().stop()

    def save(self, path: str | Path) -> Path:
        """Save the current session without displaying it.

        Parameters
        ----------
        path : str | Path
            Destination HTML filename. Missing parent directories are created.
            Stop collection first when saving a finalized sampling result.

        Returns
        -------
        Path
            Absolute path of the self-contained report.

        Raises
        ------
        RuntimeError
            If no explicit session exists or the collector cannot return
            measurements while it is still running.
        OSError
            If the destination cannot be created or written.
        """
        return self._current().save(path)

    def show(self) -> str:
        """Display the current session.

        Notebook environments receive an inline report. Other environments
        save the configured output file and open it in a browser. Stop
        collection first when displaying a finalized sampling result.

        Returns
        -------
        str
            The complete HTML used for display.

        Raises
        ------
        RuntimeError
            If no explicit session exists or the collector cannot return
            measurements while it is still running.
        OSError
            If the output file cannot be written outside a notebook.
        """
        return self._current().show()


profile = ProfileController()
profiler = profile
