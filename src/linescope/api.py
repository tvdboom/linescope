"""LineScope.

Author: Mavs
Description: Session lifecycle, public ergonomics, and normalized collection
pipeline.

"""

from __future__ import annotations

import ast
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import threading
from time import perf_counter_ns
from types import TracebackType
from typing import Any, Self
import warnings
import webbrowser

from linescope.backends.base import ProfilerBackend, create_backend
from linescope.config import Config, resolve_config
from linescope.enums import Backend, DisplayMode, RunStatus, SessionState, SourceKind, SymbolKind
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
    config : [Config] | None, default=None
        Prevalidated configuration. Mutually exclusive with keyword options.

    **options
        Explicit [Config] fields overriding global and project settings.

    Attributes
    ----------
    result : [ProfileResult]
        Captured source, run relationships, and finalized measurements after
        stop.

    state : SessionState
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
    >>> with profile(
    ...     backend="trace", display="none", notebooks=False, spark=False
    ... ) as session:
    ...     value = sum(range(10))
    >>> value
    45
    >>> str(session.state)
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
        self.state: SessionState = SessionState.CREATED

        self._backend: ProfilerBackend | None = None
        self._resources: ExitStack | None = None
        self._started = 0
        self._owner = 0

        self._references: dict[tuple[str, int], LineStats] = {}
        self.launch_root = Path.cwd().resolve()

    def __enter__(self) -> Self:
        """Start collection when entering a context.

        Return this session after instrumentation is ready.

        """
        self.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Finalize the run and preserve workload exceptions.

        Restore instrumentation before preparing any report.

        """
        successful_exit = isinstance(exception, SystemExit) and exception.code in (None, 0)

        if exception_type is not None and not successful_exit:
            self.result.root_run.status = RunStatus.FAILED

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

        Raise `RuntimeError` if another session is running or this session
        has already started.

        Returns
        -------
        [Session]
            This running session.

        """
        if self.state != SessionState.CREATED:
            raise RuntimeError("A session can only start once; create a new profile()")

        if not _session_lock.acquire(blocking=False):
            raise RuntimeError("Another profiling session is already running in this process")

        # Register cleanup before installing hooks so partial startup unwinds
        # in the same reverse order as a successful session.
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
                **({"gpu": True} if self.config.gpu else {}),
            )

            if (
                self.config.gpu
                and self._backend.name != Backend.SCALENE
                and not self._backend.capabilities.gpu
            ):
                raise ValueError(f"The {self._backend.name} backend cannot collect GPU metrics")

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
            self.result.capabilities = self._backend.capabilities
            self.state = SessionState.RUNNING
            self._resources = resources
        except BaseException:
            resources.close()
            raise

        return self

    def _refresh(self) -> None:
        if self._backend is None:
            return

        raw = self._backend.result()
        # Notebook and Spark links survive refreshes; measured costs are
        # rebuilt from the backend snapshot rather than added a second time.
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

            if measurement.gpu is not None:
                if line.gpu is None:
                    line.gpu = deepcopy(measurement.gpu)
                else:
                    if measurement.gpu.time_ns is not None:
                        line.gpu.time_ns = (line.gpu.time_ns or 0) + measurement.gpu.time_ns

                    if measurement.gpu.peak_memory_bytes is not None:
                        line.gpu.peak_memory_bytes = max(
                            line.gpu.peak_memory_bytes or 0, measurement.gpu.peak_memory_bytes
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

        if self.state == SessionState.RUNNING:
            self.result.root_run.elapsed_ns = perf_counter_ns() - self._started

    def _function_stats(
        self,
        lines: dict[tuple[str, int], LineStats],
        calls: dict[tuple[str, str, int], int],
    ) -> list[FunctionStats]:
        functions = []

        for unit in self.registry.sources.values():
            source = unit.source

            if unit.kind == SourceKind.NOTEBOOK:
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
                    SymbolKind.FUNCTION,
                    SymbolKind.METHOD,
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
        """Finalize measurements and restore instrumentation.

        Display the completed report when configured.

        Returns
        -------
        [ProfileResult]
            Completed run. Repeated calls return the same result without
            redisplay.

        """
        if self.state == SessionState.STOPPED:
            return self.result

        if self.state != SessionState.RUNNING:
            raise RuntimeError("Start the session before stopping it")

        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        self.result.root_run.elapsed_ns = perf_counter_ns() - self._started

        try:
            if self._resources is not None:
                self._resources.close()
        finally:
            self.state = SessionState.STOPPED

        self._refresh()

        if self.config.display != DisplayMode.NONE:
            self.show()

        return self.result

    def add_spark_execution(
        self,
        execution: SparkExecution,
        locations: list[SourceLocation],
    ) -> None:
        """Associate a Spark execution with its project source lines.

        Parameters
        ----------
        execution : [SparkExecution]
            Observed action and its available plan and metrics.

        locations : list[[SourceLocation]]
            Source lines that contributed to the action.

        """
        self.result.root_run.spark_executions.append(execution)

        for location in locations:
            key = (location.source_id, location.line)
            line = self._references.setdefault(key, LineStats(location))

            if execution.id not in line.spark_executions:
                line.spark_executions.append(execution.id)

    def add_child_run(self, run: ProfileRun, location: SourceLocation | None) -> None:
        """Attach a separate notebook invocation.

        Keep unavailable child measurements unknown.

        Parameters
        ----------
        run : [ProfileRun]
            Child invocation to attach to the root run.

        location : [SourceLocation] | None
            Calling project line, when available.

        """
        run.parent_id = self.result.root_run.id
        self.result.root_run.children.append(run)

        if run.source is not None:
            self.registry.register(run.source)

        if location is not None:
            key = (location.source_id, location.line)
            line = self._references.setdefault(key, LineStats(location))
            line.notebook_runs.append(run.id)

    def html(self) -> str:
        """Return the complete self-contained report as HTML.

        Returns
        -------
        str
            Report containing the captured source and current measurements.

        """
        if self.state == SessionState.CREATED:
            raise RuntimeError("Start the session before creating a report")

        if self.state == SessionState.RUNNING:
            self._refresh()

        from linescope.render import render_html

        return render_html(self.result, root=self.launch_root)

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

    def show(self, *, inline: bool | None = None) -> str:
        """Open a browser report or display it inside a notebook cell.

        Parameters
        ----------
        inline : bool | None, default=None
            Override the session's `inline` setting. False opens a browser
            tab; True displays an isolated iframe in the current notebook
            cell.

        Returns
        -------
        str
            The self-contained HTML used for display.

        """
        html = self.html()

        display_inline = self.config.inline if inline is None else inline

        if display_inline:
            from html import escape

            from IPython import get_ipython
            from IPython.display import HTML, display

            if get_ipython() is None:
                raise RuntimeError("Inline display requires an active IPython notebook")

            # An iframe isolates report CSS and scripts from the notebook itself.
            display(
                HTML(
                    f'<iframe title="LineScope report" srcdoc="{escape(html, quote=True)}" '
                    'style="width:100%;height:760px;border:0" '
                    'sandbox="allow-scripts allow-same-origin"></iframe>'
                )
            )
        else:
            if self.config.output is not None:
                destination = self.save(self.config.output)
            else:
                # A browser needs a URL; keep its backing file outside the project.
                with NamedTemporaryFile(
                    mode="w", suffix=".html", prefix="linescope-", encoding="utf-8", delete=False
                ) as report:
                    report.write(html)
                    destination = Path(report.name)

            webbrowser.open(destination.as_uri(), new=2)

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

        Raise `TypeError` if an option name or value type is invalid.

        Raise `ValueError` if an option value is outside its supported range.

        Parameters
        ----------
        **options
            Configuration fields overriding process and project defaults.
            Collection begins when entering the context or calling `start`.

        Returns
        -------
        [Session]
            A new session in the `created` state.

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

        Raise `RuntimeError` if a session is already running or the collector
        cannot start.

        Raise `ImportError` if the selected collector's optional dependency
        is missing.

        Raise `TypeError` if an option name or value type is invalid.

        Raise `ValueError` if an option value or requested capability is
        unsupported.

        Parameters
        ----------
        **options
            Configuration fields for the new session. Use `display="none"` to
            collect without displaying a report automatically at stop.

        Returns
        -------
        [Session]
            The running session retained by this controller.

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
        (total, str(session.state), str(result.backend))
        ```

        """
        if self._session is not None and self._session.state == SessionState.RUNNING:
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

        Raise `RuntimeError` if no explicit session has started, or a running
        session is stopped from a different thread than the one that started
        it.

        Returns
        -------
        [ProfileResult]
            Captured source snapshots, normalized measurements, and run tree.

        """
        return self._current().stop()

    def save(self, path: str | Path) -> Path:
        """Save the current session without displaying it.

        Raise `RuntimeError` if no explicit session exists or the collector
        cannot return measurements while it is still running.

        Raise `OSError` if the destination cannot be created or written.

        Parameters
        ----------
        path : str | Path
            Destination HTML filename. Missing parent directories are
            created. Stop collection first when saving a finalized sampling
            result.

        Returns
        -------
        Path
            Absolute path of the self-contained report.

        """
        return self._current().save(path)

    def show(self, *, inline: bool | None = None) -> str:
        """Display the current session.

        Reports open in a new browser tab by default. Use `inline=True` to
        display inside a notebook cell. Stop collection first when displaying
        a finalized sampling result.

        Raise `RuntimeError` if no explicit session exists or the collector
        cannot return measurements while it is still running.

        Raise `OSError` if the output file cannot be written outside a
        notebook.

        Parameters
        ----------
        inline : bool | None, default=None
            Override the session's notebook display preference.

        Returns
        -------
        str
            The complete HTML used for display.

        """
        return self._current().show(inline=inline)


profile = ProfileController()
profiler = profile
