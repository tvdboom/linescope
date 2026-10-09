"""LineScope.

Author: Mavs
Description: Portable, explicit tracing backend with external-call attribution.

"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import dis
from pathlib import Path
import sys
import threading
from time import perf_counter_ns
import tracemalloc
from types import FrameType
from typing import Any
import warnings

from linescope.backends.base import RawBackendResult, RawLine
from linescope.enums import Backend
from linescope.model import BackendCapabilities, MemoryStats


@dataclass
class _Frame:
    """Track timing ownership for one accepted Python frame.

    Attributes
    ----------
    filename : str
        Runtime filename of the accepted project frame.

    line : int
        Current one-based line, or zero before its first line event.

    started : int
        Monotonic timestamp of the most recent timing boundary.

    parent : int | None
        Nearest accepted caller's frame identity, when present.

    active : bool
        Whether time should accrue to this frame's current line.

    """

    filename: str
    line: int
    started: int
    parent: int | None
    active: bool = True


class TraceBackend:
    """Measure visible Python lines using the current thread's trace events.

    External calls remain charged to the calling project line. Calls into
    another visible project frame pause the caller, avoiding double-counted
    line totals. Optional memory snapshots report net retained Python
    allocations by allocation site, including the nearest project caller
    for library allocations. Per-line peaks and untracked native allocations
    stay unavailable. Tracing adds overhead and does not collect executor
    workers.

    Parameters
    ----------
    accepts : Callable[[str], bool]
        Decide whether a frame's filename belongs to the profiling scope.

    on_source : Callable[[str], Any]
        Snapshot a filename on first observation, owned by the session.

    memory : bool, default=False
        Collect net retained Python allocation changes with `tracemalloc`.
        Preserve an existing memory tracer, including its traceback depth
        and peak. New tracers capture up to 25 frames. Memory snapshots cover
        the process, including allocations from other Python threads.

    gpu : bool, default=False
        Warn when true and continue Python profiling without GPU measurements.
        GPU collection requires Scalene.

    root : str | None, default=None
        Accepted for consistency with collector factories.

    on_interval : Callable[[str, int], None] | None, default=None
        Observe completed project line intervals for optional integrations.

    Attributes
    ----------
    name : str | [Backend]
        Backend identifier used in configuration and reports.

    accepts : Callable[[str], bool]
        Predicate deciding whether a runtime file belongs to the project.

    on_source : Callable[[str], Any]
        Session callback that snapshots accepted source on observation.

    on_interval : Callable[[str, int], None] | None
        Optional callback observing completed project line timing intervals.

    memory : bool
        Whether net retained Python allocations are collected with
        `tracemalloc`.

    capabilities : [BackendCapabilities]
        Exact tracing support and any requested Python allocation tracking.

    _package_root : [Path]
        Profiler package directory excluded from memory attribution.

    _owns_tracemalloc : bool
        Whether this collector started the process memory tracer.

    _memory_baseline : dict[tuple[str, int], int] | None
        Retained allocation bytes at startup, or None when unavailable.

    _memory_deltas : dict[tuple[str, int], int] | None
        Final net allocation changes by project line, when available.

    _memory_warnings : list[str]
        Diagnostics about unavailable Python allocation measurements.

    _gpu_warning : str | None
        Diagnostic retained when GPU collection was requested but unavailable.

    _running : bool
        Whether this collector currently owns active tracing.

    _frames : dict[int, _Frame]
        Accepted frame timing states keyed by frame identity.

    _lines : dict[tuple[str, int], [RawLine]]
        Accumulated line times and exact execution counts.

    _calls : dict[tuple[str, str, int], int]
        Exact function calls keyed by filename, qualified name, and line.

    _old_frames : list[tuple[FrameType, Any]]
        Already active frames and their original local trace callbacks.

    _suspended : dict[int, FrameType]
        Yielded generator or coroutine frames awaiting resumption.

    _previous_trace : Any
        Global trace callback preserved for forwarding and restoration.

    _previous_locals : dict[int, Any]
        Previous local trace callbacks keyed by frame identity.

    _traced_frames : dict[int, FrameType]
        Observed frames whose trace callbacks need restoration.

    _owner : int
        Identifier of the thread that started this collector.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.backends.base:ProfilerBackend
    - linescope.backends.scalene:ScaleneBackend

    """

    name: str | Backend = Backend.TRACE

    def __init__(
        self,
        *,
        accepts: Callable[[str], bool],
        on_source: Callable[[str], Any],
        memory: bool = False,
        root: str | None = None,
        gpu: bool = False,
        on_interval: Callable[[str, int], None] | None = None,
    ):
        """Initialize trace accounting and optional allocation tracking.

        Warn about GPU requests and retain the diagnostic in report snapshots.
        Python profiling continues without device measurements.

        Parameters
        ----------
        accepts : Callable[[str], bool]
            Predicate identifying accepted project runtime filenames.

        on_source : Callable[[str], Any]
            Callback capturing accepted source snapshots.

        memory : bool, default=False
            Whether retained Python allocation collection is requested.

        root : str | None, default=None
            Project root used for source ownership or collector setup.

        gpu : bool, default=False
            Warn when true because tracing cannot collect GPU measurements.

        on_interval : Callable[[str, int], None] | None, default=None
            Observe completed project line intervals for optional
            integrations.

        """
        del root
        self._gpu_warning: str | None = None
        if gpu:
            self._gpu_warning = (
                "The trace backend does not support GPU profiling; GPU metrics are unavailable. "
                "Python profiling will continue. Use backend='scalene' with gpu=True."
            )
            warnings.warn(self._gpu_warning, RuntimeWarning, stacklevel=2)

        self.accepts = accepts
        self.on_source = on_source
        self.on_interval = on_interval
        self.memory = memory
        self.capabilities = BackendCapabilities(hit_counts=True, memory=memory, sampled=False)
        self._package_root = Path(__file__).resolve().parents[1]
        self._owns_tracemalloc = False
        self._memory_baseline: dict[tuple[str, int], int] | None = None
        self._memory_deltas: dict[tuple[str, int], int] | None = None
        self._memory_warnings: list[str] = []
        self._running = False
        self._frames: dict[int, _Frame] = {}
        self._lines: dict[tuple[str, int], RawLine] = {}
        self._calls: dict[tuple[str, str, int], int] = {}
        self._old_frames: list[tuple[FrameType, Any]] = []
        self._suspended: dict[int, FrameType] = {}
        self._previous_trace: Any = None
        self._previous_locals: dict[int, Any] = {}
        self._traced_frames: dict[int, FrameType] = {}
        self._owner = 0

    def _memory_totals(self) -> dict[tuple[str, int], int] | None:
        """Snapshot retained Python allocations by project line.

        Keep profiler-owned work excluded and return None if the memory tracer
        stopped.

        Returns
        -------
        dict[tuple[str, int], int] | None
            Retained allocation bytes by project line, or None if unavailable.

        """
        if not tracemalloc.is_tracing():
            warning = "Python memory tracing stopped during collection; memory is unavailable."
            if warning not in self._memory_warnings:
                self._memory_warnings.append(warning)
            return None

        snapshot = tracemalloc.take_snapshot()
        totals: dict[tuple[str, int], int] = {}
        internal: dict[str, bool] = {}
        accepted: dict[str, bool] = {}

        for statistic in snapshot.statistics("traceback"):
            for frame in reversed(statistic.traceback):
                filename = frame.filename
                if filename not in internal:
                    internal[filename] = Path(filename).is_relative_to(self._package_root)

                # Stop at profiler-owned work, but allow its CLI frames below
                # a workload's own allocation or external-library call.
                if internal[filename]:
                    break

                if filename not in accepted:
                    accepted[filename] = self.accepts(filename)

                if accepted[filename] and frame.lineno > 0:
                    key = (filename, frame.lineno)
                    totals[key] = totals.get(key, 0) + statistic.size
                    break

        return totals

    def _memory_changes(self) -> dict[tuple[str, int], int] | None:
        """Calculate net retained allocation changes from the startup snapshot.

        Keep measurements unavailable when either memory snapshot is absent.

        Returns
        -------
        dict[tuple[str, int], int] | None
            Net retained allocation bytes by project line, or None if
            unavailable.

        """
        if self._memory_baseline is None:
            return None

        totals = self._memory_totals()
        if totals is None:
            return None

        return {
            key: totals.get(key, 0) - self._memory_baseline.get(key, 0)
            for key in totals.keys() | self._memory_baseline.keys()
        }

    def _settle(self, state: _Frame, now: int):
        """Charge the active line since its last timing boundary.

        Reset the boundary even when the frame is paused or has no visible line.

        Parameters
        ----------
        state : _Frame
            Frame timing state whose interval is being settled.

        now : int
            Current monotonic timestamp in nanoseconds.

        """
        if state.active and state.line > 0:
            if self.on_interval is not None:
                self.on_interval(state.filename, state.line)
            key = (state.filename, state.line)
            value = self._lines.get(key)
            if value is None:
                value = self._lines[key] = RawLine(*key, wall_time_ns=0, hits=0)
            value.wall_time_ns = (value.wall_time_ns or 0) + max(0, now - state.started)

        state.started = now

    def _trace(self, frame: FrameType, event: str, arg: Any) -> Any:
        """Process a trace event while forwarding any existing trace callback.

        Pause accepted callers during project child work and preserve generator
        suspension accounting.

        Parameters
        ----------
        frame : FrameType
            Observed Python frame used for source attribution.

        event : str
            Interpreter event identifying the observation being processed.

        arg : Any
            Payload supplied by the interpreter trace or profile callback.

        Returns
        -------
        Any
            Trace callback retained for subsequent events in this frame.

        """
        key = id(frame)
        previous = self._previous_locals.get(key)

        if event == "call":
            previous = self._previous_trace

        if previous is not None:
            self._previous_locals[key] = previous(frame, event, arg)

            # Native trace callbacks (notably coverage.py's C tracer) can reinstall
            # themselves when invoked. Keep this multiplexer as the global hook.
            if self._running:
                sys.settrace(self._trace)

        if not self._running:
            return self._previous_locals.get(key)

        now = perf_counter_ns()

        if key not in self._frames:
            if not self.accepts(frame.f_code.co_filename):
                return self._previous_locals.pop(key, None)

            self.on_source(frame.f_code.co_filename)
            self._traced_frames[key] = frame
            parent_frame = frame.f_back

            while parent_frame is not None and id(parent_frame) not in self._frames:
                parent_frame = parent_frame.f_back

            parent = id(parent_frame) if parent_frame is not None else None

            if parent is not None:
                self._settle(self._frames[parent], now)
                self._frames[parent].active = False

            self._frames[key] = _Frame(frame.f_code.co_filename, 0, now, parent)

            if event == "call" and key not in self._suspended:
                call = (
                    frame.f_code.co_filename,
                    frame.f_code.co_qualname,
                    frame.f_code.co_firstlineno,
                )
                self._calls[call] = self._calls.get(call, 0) + 1

            self._suspended.pop(key, None)

        state = self._frames[key]
        self._settle(state, now)

        if event == "line":
            state.line = frame.f_lineno
            line_key = (state.filename, state.line)
            value = self._lines.get(line_key)
            if value is None:
                value = self._lines[line_key] = RawLine(*line_key, wall_time_ns=0, hits=0)
            value.hits = (value.hits or 0) + 1
        elif event == "return":
            # Generator yields also emit return; suspension must never accrue time.
            opcode = frame.f_code.co_code[frame.f_lasti] if frame.f_lasti >= 0 else 0

            # CPython 3.13+ reports the following RESUME instruction for yields.
            if dis.opname[opcode] in ("YIELD_VALUE", "YIELD_FROM", "RESUME"):
                self._suspended[key] = frame
            else:
                self._suspended.pop(key, None)
                self._traced_frames.pop(key, None)

            self._frames.pop(key)

            if state.parent in self._frames:
                parent_state = self._frames[state.parent]
                parent_state.active = True
                parent_state.started = now

            return self._previous_locals.pop(key, None)

        return self._trace

    def start(self):
        """Install tracing and enroll already active project frames.

        Preserve existing trace callbacks so they can be restored at stop.

        """
        if self._running:
            raise RuntimeError("This collector is already running")

        self._previous_trace = sys.gettrace()
        self._owner = threading.get_ident()
        self._running = True
        try:
            if self.memory:
                self._owns_tracemalloc = not tracemalloc.is_tracing()
                if self._owns_tracemalloc:
                    tracemalloc.start(25)
                self._memory_baseline = self._memory_totals()
                if self._memory_baseline is not None:
                    for filename in {key[0] for key in self._memory_baseline}:
                        self.on_source(filename)

            frame = sys._getframe(1)
            while frame is not None:
                if self.accepts(frame.f_code.co_filename):
                    self.on_source(frame.f_code.co_filename)
                    self._old_frames.append((frame, frame.f_trace))
                    self._previous_locals[id(frame)] = frame.f_trace
                    frame.f_trace = self._trace

                frame = frame.f_back

            sys.settrace(self._trace)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        """Release trace hooks while retaining finalized measurements.

        Restore existing frame callbacks as well as the interpreter trace
        hook.

        """
        if not self._running:
            return

        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        try:
            self._release_trace()
        finally:
            self._finish_memory()

    def _release_trace(self):
        """Restore trace hooks and release frame references before snapshots.

        Allow the shared collector to finalize memory after other collectors
        release their own references to suspended frames.

        """
        if not self._running:
            return

        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        now = perf_counter_ns()
        self._running = False
        sys.settrace(self._previous_trace)

        try:
            for state in self._frames.values():
                self._settle(state, now)
        finally:
            for frame, previous in self._old_frames:
                frame.f_trace = previous
            for key, frame in self._traced_frames.items():
                frame.f_trace = self._previous_locals.get(key)
            self._old_frames.clear()
            self._frames.clear()
            self._suspended.clear()
            self._traced_frames.clear()
            self._previous_locals.clear()

    def _finish_memory(self):
        """Finalize allocations after trace cleanup and release owned tracing.

        Preserve an existing allocation tracer, including on snapshot failure.

        """
        if self._memory_baseline is None and not self._owns_tracemalloc:
            return

        try:
            self._memory_deltas = self._memory_changes()
        finally:
            self._memory_baseline = None
            # Existing tracers belong to the caller; never stop or reset them.
            if self._owns_tracemalloc:
                tracemalloc.stop()
                self._owns_tracemalloc = False

    def result(self) -> RawBackendResult:
        """Return a detached copy of collected line and function measurements.

        Keep the returned snapshot independent of the collector's internal
        state.

        """
        lines = {key: deepcopy(value) for key, value in self._lines.items()}
        warnings = [
            ("Trace instrumentation measures the calling thread; it increases execution overhead.")
        ]
        if self._gpu_warning is not None:
            warnings.append(self._gpu_warning)

        if self.memory:
            deltas = self._memory_changes() if self._running else self._memory_deltas
            if deltas is not None:
                for key, value in lines.items():
                    value.memory = MemoryStats(delta_bytes=deltas.get(key, 0))
                for key, delta in deltas.items():
                    if delta != 0 and key not in lines:
                        self.on_source(key[0])
                        lines[key] = RawLine(*key, memory=MemoryStats(delta_bytes=delta))

            warnings.append(
                "Trace memory measures net retained Python allocations by allocation site across"
                " all Python threads. Library allocations use the nearest project frame in the"
                " captured traceback. Temporary allocations freed between snapshots, untracked"
                " native allocations, and per-line peaks are unavailable. Existing tracemalloc"
                " traceback depth is preserved and can limit library attribution."
            )
            warnings.extend(self._memory_warnings)

        return RawBackendResult(list(lines.values()), warnings, dict(self._calls))
