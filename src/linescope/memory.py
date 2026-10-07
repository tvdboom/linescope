"""LineScope.

Author: Mavs
Description: Observe resident process RAM and retained Python allocations.

"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import sys
import threading
from time import perf_counter_ns
from typing import Any

import psutil

from linescope.backends.trace import TraceBackend
from linescope.model import MemoryStats, ProcessMemoryStats

_MAX_SAMPLES = 4096
_INTERVAL_SECONDS = 0.01


@dataclass(frozen=True)
class _Sample:
    """Retain a RAM observation before source identities are normalized.

    Attributes
    ----------
    elapsed_ns : int
        Time since collection began, in nanoseconds.

    rss_bytes : int | None
        Resident process RAM in bytes; None marks an unavailable reading.

    filename : str | None
        Runtime source filename; None marks an unlinked observation.

    line : int
        One-based active source line, or zero for an unlinked observation.

    """

    elapsed_ns: int
    rss_bytes: int | None
    filename: str | None = None
    line: int = 0


class ProcessMemoryCollector:
    """Collect process RAM and retained Python allocations separately.

    Trace the starting thread's project line boundaries and sample during
    long calls every 10 ms when the scheduler and GIL permit. Changes describe
    the whole process rather than proving allocation ownership. Compression
    retains chronological bucket extrema and the first and final readings.
    Allocation snapshots use the owned boundary tracer and are finalized
    after timing collectors release their trace-frame references.

    Parameters
    ----------
    accepts : Callable[[str], bool]
        Decide which runtime filenames belong to the project.

    on_source : Callable[[str], Any]
        Snapshot accepted source files on the profiling thread.

    Attributes
    ----------
    _accepts : Callable[[str], bool]
        Project source predicate used for sampled stack attribution.

    _process : [psutil.Process]
        Handle for the profiled process's resident memory readings.

    _tracer : [TraceBackend]
        Own line-boundary tracing, retained allocations, and restoration of
        previous callbacks.

    _lock : threading.RLock
        Serialize observations and detached live snapshots.

    _stop : threading.Event
        Wake the background sampler during cleanup.

    _thread : threading.Thread | None
        Owned periodic sampler, joined before restoring tracing.

    _lines : dict[tuple[str, int], [ProcessMemoryStats]]
        Latest RAM, accumulated change, and peak per runtime source line.

    _samples : list[_Sample]
        Bounded chronological history, retaining bucket extrema.

    _unknown_deltas : set[tuple[str, int]]
        Lines whose intervals cross a missing reading.

    _warnings : list[str]
        Diagnostics explaining unavailable process measurements.

    _previous : int | None
        Previous process RAM reading in bytes, including unlinked readings.

    _started : int
        Monotonic timestamp of collection startup, in nanoseconds.

    _owner : int
        Starting Python thread identifier used to inspect active frames.

    _running : bool
        Whether the collector owns instrumentation requiring cleanup.

    _compressed : bool
        Whether timeline compression has omitted intermediate readings.

    """

    def __init__(
        self,
        accepts: Callable[[str], bool],
        on_source: Callable[[str], Any],
    ):
        """Initialize observations without installing trace hooks.

        Parameters
        ----------
        accepts : Callable[[str], bool]
            Project source predicate.

        on_source : Callable[[str], Any]
            Source snapshot callback owned by the session.

        """
        self._accepts = accepts
        self._process = psutil.Process()
        self._tracer = TraceBackend(
            accepts=accepts, on_source=on_source, memory=True, on_interval=self._boundary
        )
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lines: dict[tuple[str, int], ProcessMemoryStats] = {}
        self._samples: list[_Sample] = []
        self._unknown_deltas: set[tuple[str, int]] = set()
        self._warnings: list[str] = []
        self._previous: int | None = None
        self._started = 0
        self._owner = 0
        self._running = False
        self._compressed = False

    def _read(self) -> int | None:
        """Read current resident RAM, preserving unavailable values.

        Returns
        -------
        int | None
            Resident process bytes, or None with a diagnostic on failure.

        """
        try:
            return self._process.memory_info().rss
        except (psutil.Error, OSError) as error:
            warning = f"Process RAM reading unavailable: {type(error).__name__}: {error}"
            if warning not in self._warnings:
                self._warnings.append(warning)
            return None

    def _append(self, sample: _Sample):
        """Append a reading and compress history while preserving extrema.

        Parameters
        ----------
        sample : _Sample
            Observation to append in chronological order.

        """
        self._samples.append(sample)
        if len(self._samples) <= _MAX_SAMPLES:
            return

        # Keep spike and trough observations with their exact source links.
        retained = [self._samples[0]]
        for offset in range(1, len(self._samples) - 1, 4):
            bucket = self._samples[offset : min(offset + 4, len(self._samples) - 1)]
            known = [index for index, item in enumerate(bucket) if item.rss_bytes is not None]
            indices = set()
            if known:
                indices.add(min(known, key=lambda index: bucket[index].rss_bytes or 0))
                indices.add(max(known, key=lambda index: bucket[index].rss_bytes or 0))
            missing = next(
                (index for index, item in enumerate(bucket) if item.rss_bytes is None), None
            )
            if missing is not None:
                indices.add(missing)
            retained.extend(bucket[index] for index in sorted(indices))
        retained.append(self._samples[-1])
        self._samples = retained
        self._compressed = True

    def _record(self, filename: str | None, number: int, *, boundary: bool):
        """Update process changes and peaks for one observed interval.

        Parameters
        ----------
        filename : str | None
            Active project filename, or None for an unlinked reading.

        number : int
            One-based source line, or zero when unlinked.

        boundary : bool
            Whether the reading completes a project line interval.

        """
        with self._lock:
            rss = self._read()
            previous = self._previous
            if filename is not None and number > 0:
                key = (filename, number)
                stats = self._lines.setdefault(key, ProcessMemoryStats())
                if rss is None or previous is None:
                    self._unknown_deltas.add(key)
                if key not in self._unknown_deltas and rss is not None and previous is not None:
                    stats.delta_bytes = (stats.delta_bytes or 0) + rss - previous
                else:
                    stats.delta_bytes = None
                if boundary:
                    stats.rss_bytes = rss
                peaks = [value for value in (stats.peak_bytes, previous, rss) if value is not None]
                stats.peak_bytes = max(peaks) if peaks else None
            self._append(_Sample(perf_counter_ns() - self._started, rss, filename, number))
            self._previous = rss

    def _boundary(self, filename: str, number: int):
        """Observe RAM after a completed project line interval.

        Parameters
        ----------
        filename : str
            Runtime project filename.

        number : int
            One-based completed source line.

        """
        self._record(filename, number, boundary=True)

    def _sample(self):
        """Observe RAM during a long call at the nearest project frame.

        Keep source discovery on the owner thread; snapshots normalize the
        recorded filename after collection.

        """
        with self._lock:
            frame = sys._current_frames().get(self._owner)
            while frame is not None:
                if self._accepts(frame.f_code.co_filename) and frame.f_lineno > 0:
                    self._record(frame.f_code.co_filename, frame.f_lineno, boundary=False)
                    return
                frame = frame.f_back
            self._record(None, 0, boundary=False)

    def _loop(self):
        """Sample until cleanup signals the owned stop event.

        Wake promptly when cleanup requests sampler shutdown.

        """
        while not self._stop.wait(_INTERVAL_SECONDS):
            self._sample()

    def start(self):
        """Start observations while preserving existing trace callbacks.

        Unwind partially installed instrumentation if startup fails.

        """
        self._owner = threading.get_ident()
        self._started = perf_counter_ns()
        self._running = True
        try:
            self._record(None, 0, boundary=False)
            self._tracer.start()
            self._thread = threading.Thread(target=self._loop, name="linescope-ram", daemon=True)
            self._thread.start()
        except BaseException:
            self.stop()
            raise

    def stop(self):
        """Stop sampling and restore trace callbacks, including after errors.

        Finalize pending project intervals before releasing the hook.

        """
        if not self._running:
            return
        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        try:
            self.stop_tracing()
        finally:
            self.finish_allocations()

    def stop_tracing(self):
        """Stop RAM sampling and restore hooks before timing collector cleanup.

        Keep allocation tracing alive until all collectors release their frame
        references. Sessions own the subsequent `finish_allocations` call.

        """
        if not self._running:
            return
        if threading.get_ident() != self._owner:
            raise RuntimeError("Stop profiling from the thread that started it")

        self._stop.set()
        try:
            if self._thread is not None and self._thread.ident is not None:
                self._thread.join()
        finally:
            try:
                self._tracer._release_trace()
                self._record(None, 0, boundary=False)
            finally:
                self._running = False

    def finish_allocations(self):
        """Capture final allocations after all trace-frame references are freed.

        Release owned allocation instrumentation even when a snapshot fails.

        """
        self._tracer._finish_memory()

    def snapshot(
        self,
    ) -> tuple[dict[tuple[str, int], ProcessMemoryStats], list[_Sample], list[str], bool]:
        """Return detached line readings, timeline, and diagnostics.

        Returns
        -------
        tuple
            Line RAM statistics, chronological observations, warnings, and
            whether the bounded timeline was compressed.

        """
        with self._lock:
            return (
                deepcopy(self._lines),
                list(self._samples),
                list(self._warnings),
                self._compressed,
            )

    def allocations(self) -> tuple[dict[tuple[str, int], MemoryStats], list[str]]:
        """Return retained Python allocations separately from resident RAM.

        Keep allocation snapshots detached from the collector's state and
        leave per-line allocation peaks unavailable.

        Returns
        -------
        tuple[dict[tuple[str, int], [MemoryStats]], list[str]]
            Retained allocation changes by source line and their diagnostics.

        """
        result = self._tracer.result()
        return {
            (line.filename, line.line): line.memory
            for line in result.lines
            if line.memory is not None
        }, result.warnings
