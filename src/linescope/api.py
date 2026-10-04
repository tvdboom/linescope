"""LineScope.

Author: Mavs
Description: Session lifecycle, public ergonomics, and normalized collection
pipeline.

"""

from __future__ import annotations

import ast
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import threading
from time import perf_counter_ns
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
import warnings
import webbrowser

from linescope.backends.base import ProfilerBackend, RawLine, create_backend
from linescope.config import Config, resolve_config
from linescope.enums import Backend, DisplayMode, RunStatus, SessionState, SourceKind, SymbolKind
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    MemorySample,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
)
from linescope.source import SourceRegistry, build_navigation

if TYPE_CHECKING:
    from linescope.memory import ProcessMemoryCollector

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
    config : [Config]
        Validated options retained for this profiling session.

    registry : [SourceRegistry]
        Project discovery, frozen source snapshots, and notebook aliases.

    result : [ProfileResult]
        Captured source, run tree, and the latest normalized measurements.

    state : [SessionState]
        Lifecycle state; a session starts once and remains stopped.

    _backend : [ProfilerBackend] | None
        Collector owned by this session, once startup begins.

    _memory : ProcessMemoryCollector | None
        Owned RAM and Python allocation collector with a source-linked RAM
        timeline, when enabled.

    _resources : ExitStack | None
        Cleanup stack owning installed integrations and the session lock.

    _started : int
        Monotonic nanosecond timestamp recorded when collection starts.

    _owner : int
        Identifier of the thread that owns collection and cleanup.

    _references : dict[tuple[str, int], [LineStats]]
        Notebook and Spark links retained when measurements are refreshed.

    _notebook_path : str | None
        Original workspace path supplied for generated child notebooks.

    _notebook_sources : list[[SourceUnit]]
        Precaptured child cells reused by notebook source capture.

    launch_root : Path
        Working directory at construction, used for report navigation.

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
        """Initialize validated options, source ownership, and session state.

        Instrumentation is installed only when the session starts.

        Parameters
        ----------
        config : [Config] | None, default=None
            Validated session options, mutually exclusive with keyword options.

        **options : Any
            Configuration or factory keyword options forwarded to the owner.

        """
        if config is not None and options:
            raise TypeError("Pass a Config or keyword options, not both")

        self.config = config if config is not None else resolve_config(**options)
        self.registry = SourceRegistry(self.config.root, self.config.include, self.config.exclude)
        self.result = ProfileResult(
            ProfileRun(), self.registry.sources, self.config.backend, BackendCapabilities()
        )
        self.state: SessionState = SessionState.CREATED

        self._backend: ProfilerBackend | None = None
        self._memory: ProcessMemoryCollector | None = None
        self._resources: ExitStack | None = None
        self._started = 0
        self._owner = 0

        self._references: dict[tuple[str, int], LineStats] = {}
        self._notebook_path: str | None = None
        self._notebook_sources: list[SourceUnit] = []
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
                # The shared collector owns RAM and Python allocation tracking.
                memory=False,
                root=str(self.registry.root),
                **({"gpu": True} if self.config.gpu else {}),
                **(
                    {"sample_rate": self.config.sample_rate}
                    if self.config.sample_rate is not None
                    and self.config.backend in {Backend.SCALENE, Backend.TACHYON}
                    else {}
                ),
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

            if self.config.spark:
                from linescope.spark import SparkIntegration

                spark = SparkIntegration(self)
                resources.callback(spark.stop)
                spark.start()

            self._started = perf_counter_ns()
            if self.config.memory:
                from linescope.memory import ProcessMemoryCollector

                self._memory = ProcessMemoryCollector(
                    self.registry.accepts, self.registry.snapshot
                )
                # Final allocations follow cleanup of both trace collectors.
                resources.callback(self._memory.finish_allocations)
            resources.callback(self._backend.stop)
            self._backend.start()
            self.result.capabilities = self._backend.capabilities
            if self._memory is not None:
                resources.callback(self._memory.stop_tracing)
                self._memory.start()
                self.result.capabilities = replace(self.result.capabilities, memory=True)
            sample_rate = getattr(self._backend, "sample_rate", None)
            if self.result.capabilities.sampled and sample_rate is not None:
                self.result.root_run.metadata["sample_rate"] = sample_rate
            self.state = SessionState.RUNNING
            self._resources = resources
        except BaseException:
            resources.close()
            raise

        return self

    def _refresh(self) -> None:
        """Rebuild normalized measurements from the latest collector snapshot.

        Preserve notebook and Spark references without accumulating measured
        costs twice.

        """
        if self._backend is None:
            return

        raw = self._backend.result()
        if self._memory is not None:
            allocations, allocation_warnings = self._memory.allocations()
            # Custom collectors can reuse their raw result; leave it untouched.
            raw = replace(
                raw,
                lines=[
                    *raw.lines,
                    *(
                        RawLine(filename, number, memory=stats)
                        for (filename, number), stats in allocations.items()
                    ),
                ],
                warnings=[*raw.warnings, *allocation_warnings],
            )
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

            if measurement.samples is not None:
                line.samples = (line.samples or 0) + measurement.samples

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

        if self._memory is not None:
            memory_lines, samples, memory_warnings, compressed = self._memory.snapshot()
            for (filename, number), stats in memory_lines.items():
                source = self.registry.snapshot(filename)
                if source is not None:
                    key = (source.id, number)
                    lines.setdefault(key, LineStats(SourceLocation(*key))).ram = stats

            timeline = []
            for sample in samples:
                source = self.registry.snapshot(sample.filename) if sample.filename else None
                location = SourceLocation(source.id, sample.line) if source is not None else None
                timeline.append(MemorySample(sample.elapsed_ns, sample.rss_bytes, location))
            self.result.root_run.memory_samples = timeline
            self.result.root_run.metadata["memory_timeline_compressed"] = compressed
            for warning in memory_warnings:
                if warning not in self.result.warnings:
                    self.result.warnings.append(warning)

        symbols, navigation = build_navigation(self.registry.sources)

        child_sources = self._child_source_ids()
        own_sources = {source_id for source_id, _line in lines}

        for key, calls in navigation.items():
            if key[0] not in child_sources or key[0] in own_sources:
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
        """Index function costs from visible source lines and exact call counts.

        Exclude nested definitions and keep unsupported call counts unavailable.

        Parameters
        ----------
        lines : dict[tuple[str, int], LineStats]
            Visible source line measurements keyed by snapshot and line.

        calls : dict[tuple[str, str, int], int]
            Exact invocation counts keyed by filename, qualified name, and line.

        Returns
        -------
        list[FunctionStats]
            Function measurements derived from accepted project source.

        """
        functions = []
        child_sources = self._child_source_ids()
        own_sources = {source_id for source_id, _line in lines}

        for unit in self.registry.sources.values():
            if unit.id in child_sources and unit.id not in own_sources:
                continue

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
                own_lines = [
                    value
                    for (source_id, number), value in lines.items()
                    if source_id == unit.id
                    and node.body[0].lineno <= number <= (node.end_lineno or node.lineno)
                    and not any(
                        item.lineno <= number <= (item.end_lineno or item.lineno)
                        for item in nested
                    )
                ]
                measurements = [
                    value.wall_time_ns for value in own_lines if value.wall_time_ns is not None
                ]
                samples = [value.samples for value in own_lines if value.samples is not None]
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
                        sum(samples)
                        if samples or self.result.capabilities.sample_counts
                        else None,
                    )
                )

        return functions

    def _child_source_ids(self) -> set[str]:
        """Collect source identifiers owned by separate child notebook runs.

        Traverse nested children so refreshes preserve each run's measurement
        ownership.

        Returns
        -------
        set[str]
            Snapshot identifiers retained by separate child runs.

        """
        sources: set[str] = set()
        pending = list(self.result.root_run.children)

        while pending:
            run = pending.pop()
            sources.update(run.metadata.get("child_source_ids", []))
            pending.extend(run.children)

        return sources

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

            # srcdoc inherits the notebook URL, so pin fragment links to the report.
            # Set the base before the report's CSP rejects subsequent base changes.
            inline_html = html.replace("<head>", '<head><base href="about:srcdoc">', 1)

            # An iframe isolates report CSS and scripts from the notebook itself.
            display(
                HTML(
                    f'<iframe title="LineScope report" srcdoc="{escape(inline_html, quote=True)}" '
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

    Attributes
    ----------
    _session : [Session] | None
        Most recent explicit session, or None before `start` is called.

    See Also
    --------
    - linescope:configure
    - linescope.model:ProfileResult
    - linescope:Session

    """

    def __init__(self) -> None:
        """Initialize a controller without an explicit profiling session.

        Retain a session only after an explicit `start` succeeds.

        """
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
        """Return the most recent explicit session.

        Raise `RuntimeError` when no explicit session has been started.

        Returns
        -------
        [Session]
            Current owned observer or session, when available.

        """
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
