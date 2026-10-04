"""LineScope.

Author: Mavs
Description: Portable, explicit tracing backend with external-call attribution.

"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import dis
import sys
import threading
from time import perf_counter_ns
from types import FrameType
from typing import Any

from linescope.backends.base import RawBackendResult, RawLine
from linescope.enums import Backend
from linescope.model import BackendCapabilities


@dataclass
class _Frame:
    filename: str
    line: int
    started: int
    parent: int | None
    active: bool = True


class TraceBackend:
    """Measure visible Python lines using the current thread's trace events.

    External calls remain charged to the calling project line. Calls into
    another visible project frame pause the caller, avoiding double-counted
    line totals. Tracing adds overhead and does not collect executor workers
    or memory.

    Parameters
    ----------
    accepts : Callable[[str], bool]
        Decide whether a frame's filename belongs to the profiling scope.

    on_source : Callable[[str], Any]
        Snapshot a filename on first observation, owned by the session.

    memory : bool, default=False
        Must be false; this backend does not fabricate allocation
        measurements.

    gpu : bool, default=False
        Must be false; GPU collection requires Scalene.

    root : str | None, default=None
        Accepted for consistency with collector factories.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.backends.base:ProfilerBackend
    - linescope.backends.scalene:ScaleneBackend

    """

    name: Backend | str = Backend.TRACE
    capabilities = BackendCapabilities(hit_counts=True, memory=False, sampled=False)

    def __init__(
        self,
        *,
        accepts: Callable[[str], bool],
        on_source: Callable[[str], Any],
        memory: bool = False,
        root: str | None = None,
        gpu: bool = False,
    ) -> None:
        del root
        if memory:
            raise ValueError("The trace backend cannot measure memory; use backend='scalene'.")

        if gpu:
            raise ValueError(
                "The trace backend cannot measure GPU metrics; use backend='scalene'."
            )

        self.accepts = accepts
        self.on_source = on_source
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

    def _settle(self, state: _Frame, now: int) -> None:
        if state.active and state.line > 0:
            key = (state.filename, state.line)
            value = self._lines.setdefault(key, RawLine(*key, wall_time_ns=0, hits=0))
            value.wall_time_ns = (value.wall_time_ns or 0) + max(0, now - state.started)

        state.started = now

    def _trace(self, frame: FrameType, event: str, arg: Any) -> Any:
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
            value = self._lines.setdefault(line_key, RawLine(*line_key, wall_time_ns=0, hits=0))
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

    def start(self) -> None:
        """Install tracing and enroll already active project frames.

        Preserve existing trace callbacks so they can be restored at stop.

        """
        if self._running:
            raise RuntimeError("This collector is already running")

        self._previous_trace = sys.gettrace()
        self._owner = threading.get_ident()
        self._running = True
        frame = sys._getframe(1)

        while frame is not None:
            if self.accepts(frame.f_code.co_filename):
                self.on_source(frame.f_code.co_filename)
                self._old_frames.append((frame, frame.f_trace))
                self._previous_locals[id(frame)] = frame.f_trace
                frame.f_trace = self._trace

            frame = frame.f_back

        sys.settrace(self._trace)

    def stop(self) -> None:
        """Release trace hooks while retaining finalized measurements.

        Restore existing frame callbacks as well as the interpreter trace
        hook.

        """
        if not self._running:
            return

        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        now = perf_counter_ns()
        self._running = False
        sys.settrace(self._previous_trace)

        for state in self._frames.values():
            self._settle(state, now)

        for frame, previous in self._old_frames:
            frame.f_trace = previous

        for key, frame in self._traced_frames.items():
            frame.f_trace = self._previous_locals.get(key)

        self._old_frames.clear()
        self._frames.clear()
        self._suspended.clear()
        self._traced_frames.clear()
        self._previous_locals.clear()

    def result(self) -> RawBackendResult:
        """Return a detached copy of collected line and function measurements.

        Keep the returned snapshot independent of the collector's internal
        state.

        """
        return RawBackendResult(
            deepcopy(list(self._lines.values())),
            [
                (
                    "Trace instrumentation measures the calling thread; it increases execution"
                    " overhead."
                )
            ],
            dict(self._calls),
        )
