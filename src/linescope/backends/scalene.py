"""LineScope.

Author: Mavs
Description: Scoped sampling with Scalene's collection and normalization engine.

"""

from __future__ import annotations

import importlib
import inspect
import math
import os
import random
import signal
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from types import FrameType
from typing import Any

from linescope.backends.base import RawBackendResult, RawLine
from linescope.model import BackendCapabilities, MemoryStats

_ACTIVE: ScaleneBackend | None = None
_NATIVE_MAPS: tuple[Any, Any] | None = None
_MISSING = object()
_SAMPLE_WARNING = (
    "Scalene time is a sampled wall-time estimate, including external work attributed "
    "to the nearest project line. Hit counts are unavailable; short runs may receive no samples."
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalize_scalene(
    payload: Mapping[str, Any],
    *,
    accepts: Callable[[str], bool] | None = None,
    memory: bool = False,
) -> RawBackendResult:
    """Normalize Scalene JSON metrics without exposing backend details.

    Parameters
    ----------
    payload : Mapping[str, Any]
        Scalene JSON profile containing ``files`` and ``elapsed_time_sec``.

    accepts : callable | None, default=None
        Optional filename predicate. None retains every reported file.

    memory : bool, default=False
        Include available allocation delta and peak metrics in bytes.

    Returns
    -------
    RawBackendResult
        Sampled line estimates. Unsupported counts and memory values remain
        None; Scalene's ``n_growth_mb`` is not interpreted as allocation delta.

    """
    elapsed = _number(payload.get("elapsed_time_sec"))
    rows = []
    for filename, file_data in payload.get("files", {}).items():
        if accepts is not None and not accepts(filename):
            continue
        for line in file_data.get("lines", []):
            number = _number(line.get("lineno"))
            if number is None or number < 1 or int(number) != number:
                continue
            percentages = [
                _number(line.get(key))
                for key in ("n_cpu_percent_python", "n_cpu_percent_c", "n_sys_percent")
            ]
            wall_time = None
            if (
                elapsed is not None
                and elapsed >= 0
                and any(value is not None for value in percentages)
            ):
                percent = min(100, sum(max(0, value or 0) for value in percentages))
                wall_time = round(elapsed * percent * 10_000_000)
            memory_stats = None
            if memory:
                allocated = _number(line.get("n_malloc_mb"))
                freed = _number(line.get("n_free_mb"))
                peak = _number(line.get("n_peak_mb"))
                delta = (
                    round((allocated - freed) * 1024**2)
                    if allocated is not None and freed is not None
                    else None
                )
                if delta is not None or peak is not None:
                    memory_stats = MemoryStats(
                        delta, round(max(0, peak) * 1024**2) if peak is not None else None
                    )
            rows.append(RawLine(filename, int(number), wall_time, None, memory_stats))
    return RawBackendResult(rows, [_SAMPLE_WARNING])


def _load_scalene() -> Any:
    """Load the compatible engine while containing package import side effects."""
    try:
        installed = version("scalene")
    except PackageNotFoundError:
        raise ImportError(
            "The Scalene backend requires its optional dependency. "
            "Install it with `pip install 'linescope[scalene]'`, "
            "or explicitly select backend='trace'."
        ) from None
    if installed.split(".")[:2] != ["2", "3"]:
        raise RuntimeError(
            f"Scalene {installed} is not supported by this adapter; "
            "install scalene>=2.3,<2.4 or select backend='trace'."
        )
    locale = os.environ.get("LC_ALL")
    try:
        return importlib.import_module("scalene")
    finally:
        # Scalene's package initializer sets this globally even in CPU-only mode.
        if locale is None:
            os.environ.pop("LC_ALL", None)
        else:
            os.environ["LC_ALL"] = locale


def _component(module: str, name: str) -> Any:
    """Import an optional, version-checked Scalene component lazily."""
    return getattr(importlib.import_module(f"scalene.{module}"), name)


def memory_preload_environment() -> dict[str, str]:
    """Return startup environment variables for native memory collection.

    Returns
    -------
    dict[str, str]
        Variables to merge into a newly launched Python process. Linux and
        macOS require the interposer before Python starts. Windows loads its
        native collector at runtime and does not need a new process.

    Notes
    -----
    This function never restarts Python or changes the caller's environment.
    The CLI uses it before executing the target script for ``--memory``.

    """
    _load_scalene()
    arguments = _component("scalene_arguments", "ScaleneArguments")
    preload = _component("scalene_preload", "ScalenePreload")
    return preload.get_preload_environ(arguments(memory=True, gpu=False))


class ScaleneBackend:
    """Collect scoped measurements using the Scalene sampling engine.

    Parameters
    ----------
    accepts : callable
        Predicate defining project-owned runtime filenames.

    on_source : callable
        Snapshot callback invoked on first observation of an accepted file.

    memory : bool, default=False
        Collect native Scalene allocation samples. Linux and macOS require
        starting Python with the native interposer; the CLI handles this.

    root : str | None, default=None
        Accepted for consistency with the backend factory protocol.

    See Also
    --------
    - linescope.backends.base:ProfilerBackend
    - linescope.backends.trace:TraceBackend
    - linescope.backends.scalene:normalize_scalene

    Notes
    -----
    The adapter uses Scalene 2.3's collection components directly, avoiding
    the command-line controller's process-wide OS, thread, and subprocess
    replacements. POSIX systems sample using SIGALRM; Windows uses a sampler
    thread, matching Scalene's platform strategy. All installed hooks are
    released at stop. Starting requires the main Python thread.

    Examples
    --------
    Inspect capabilities without starting the optional collector:

    ```pycon
    from linescope.backends.scalene import ScaleneBackend
    backend = ScaleneBackend(accepts=lambda filename: True, on_source=lambda filename: None)
    (backend.capabilities.sampled, backend.capabilities.hit_counts)
    ```

    """

    name = "scalene"

    def __init__(
        self,
        *,
        accepts: Callable[[str], bool],
        on_source: Callable[[str], Any],
        memory: bool = False,
        root: str | None = None,
    ) -> None:
        self.accepts = accepts
        self.on_source = on_source
        self.memory = memory
        self.root = str(root or os.getcwd())
        self.capabilities = BackendCapabilities(hit_counts=False, memory=memory, sampled=True)
        self._running = False
        self._seen: set[str] = set()
        self._raw = RawBackendResult()
        self._signals: dict[Any, Any] = {}
        self._native_state: dict[str, Any] = {}
        self._native: Any = None
        self._native_started = False
        self._native_queues: list[Any] = []
        self._sampler: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sampling = False
        self._sample_lock = threading.RLock()
        self._sampling_error: str | None = None
        self._profile_installed = False
        self._interval = 0.01
        self._random = random.Random()

    def _observe(self, filename: str) -> bool:
        accepted = self.accepts(filename)
        if accepted and filename not in self._seen:
            self._seen.add(filename)
            self.on_source(filename)
            if self._native_started:
                self._register_native_files()
        return accepted

    def _should_trace(self, filename: str, function: str = "") -> bool:
        return self._observe(filename)

    def _source_event(self, frame: FrameType, event: str, arg: Any) -> None:
        if self._running and event == "call":
            self._observe(frame.f_code.co_filename)

    def _time(self) -> Any:
        current = self._time_info()
        current.sys, current.user = self._get_times()
        current.virtual = time.process_time()
        current.wallclock = time.perf_counter()
        return current

    def _set_signal(self, signum: Any, handler: Any) -> None:
        self._signals.setdefault(signum, signal.getsignal(signum))
        signal.signal(signum, handler)

    def start(self) -> None:
        """Start the real Scalene collector and source observation hooks."""
        global _ACTIVE
        if self._running or _ACTIVE is not None:
            raise RuntimeError("A Scalene collector is already running in this process")
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Start Scalene profiling from the main Python thread")
        if sys.getprofile() is not None:
            raise RuntimeError(
                "A Python profile hook is already installed; stop it before starting Scalene"
            )
        if sys.platform != "win32" and any(signal.getitimer(signal.ITIMER_REAL)):
            raise RuntimeError("SIGALRM is already in use by an active timer")
        _load_scalene()
        self._stats = _component("scalene_statistics", "ScaleneStatistics")()
        self._processor = _component("scalene_cpu_profiler", "ScaleneCPUProfiler")(
            self._stats, os.cpu_count() or 1, False
        )
        self._json = _component("scalene_json", "ScaleneJSON")()
        self._frames = _component("scalene_utility", "compute_frames_to_record")
        self._time_info = _component("time_info", "TimeInfo")
        self._get_times = _component("scalene_profiler", "get_times")
        self._sleeping: dict[int, bool] = defaultdict(bool)
        self._seen.clear()
        self._raw = RawBackendResult()
        self._stop_event.clear()
        self._sampling_error = None
        self._clear_caches = _component("scalene_utility", "clear_intern_caches")
        self._clear_caches()
        _ACTIVE = self
        try:
            if self.memory:
                self._start_memory()
            frame = sys._getframe(1)
            while frame is not None:
                self._observe(frame.f_code.co_filename)
                frame = frame.f_back
            self._started = time.perf_counter()
            self._stats.start_clock()
            self._previous = self._time()
            self._running = True
            if self._native_started:
                importlib.import_module("scalene.pywhere").set_scalene_done_false()
            sys.setprofile(self._source_event)
            self._profile_installed = True
            if sys.platform == "win32":
                self._sampler = threading.Thread(
                    target=self._sample_loop, name="linescope-scalene", daemon=True
                )
                self._sampler.start()
            else:
                self._set_signal(signal.SIGALRM, self._sample)
                self._interval = self._random.expovariate(100)
                signal.setitimer(signal.ITIMER_REAL, self._interval)
        except BaseException:
            self._cleanup()
            raise

    def _sample_loop(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._sample()

    def _sample(self, signum: Any = None, frame: FrameType | None = None) -> None:
        if not self._running:
            return
        if self._sampling:
            if sys.platform != "win32":
                signal.setitimer(signal.ITIMER_REAL, self._interval)
            return
        with self._sample_lock:
            self._collect_sample()

    def _collect_sample(self) -> None:
        self._sampling = True
        try:
            current = self._time()
            frames = self._frames(self._should_trace)
            self._processor.process_cpu_sample(
                frames,
                current,
                0.0,
                0.0,
                self._previous,
                self._sleeping,
                self._should_trace,
                self._interval,
                False,
            )
            self._previous = current
            if self.memory and sys.platform == "win32":
                self._native._alloc_sigqueue_processor([0])
                self._native._memcpy_sigqueue_processor(None, None)
        except Exception as error:
            # Signals must never replace an exception in the profiled program.
            # Keep collection failures visible in the normalized report.
            self._sampling_error = (
                f"Scalene sample collection failed: {type(error).__name__}: {error}"
            )
        finally:
            self._sampling = False
            if self._running and sys.platform != "win32":
                self._interval = max(0.0001, self._random.expovariate(100))
                signal.setitimer(signal.ITIMER_REAL, self._interval)

    def _replace_native(self, name: str, value: Any) -> None:
        self._native_state[name] = inspect.getattr_static(self._native, name, _MISSING)
        setattr(self._native, name, value)

    def _start_memory(self) -> None:
        global _NATIVE_MAPS
        pywhere = importlib.import_module("scalene.pywhere")
        arguments_type = _component("scalene_arguments", "ScaleneArguments")
        mapfile = _component("scalene_mapfile", "ScaleneMapFile")
        memory_profiler = _component("scalene_memory_profiler", "ScaleneMemoryProfiler")
        scalene = _component("scalene_profiler", "Scalene")
        signal_queue = _component("scalene_sigqueue", "ScaleneSigQueue")
        initialize_tracer = _component("scalene_tracer", "initialize_tracer")

        if sys.gettrace() is not None:
            raise RuntimeError(
                "Native memory profiling cannot share an active debugger or trace hook"
            )
        if sys.platform == "win32":
            windows = _component("scalene_windows", "get_windows_profiler")()
            if not windows.load_dll() or not windows.initialize():
                raise RuntimeError(
                    "Scalene's native Windows memory collector could not initialize"
                )
        try:
            if _NATIVE_MAPS is None:
                _NATIVE_MAPS = (mapfile("malloc"), mapfile("memcpy"))
        except (OSError, ValueError) as error:
            raise RuntimeError(
                "Scalene memory profiling requires its native allocator at Python startup. "
                "Use `linescope --memory script.py`, or start the notebook kernel with "
                "the environment from linescope.backends.scalene.memory_preload_environment()."
            ) from error
        self._native = scalene
        if getattr(scalene, "_Scalene__initialized", False):
            self._native = None
            raise RuntimeError(
                "Native memory profiling cannot share another initialized Scalene session"
            )
        processor = memory_profiler(self._stats)
        processor.set_mapfiles(*_NATIVE_MAPS)
        arguments = arguments_type(memory=True, gpu=False, stacks=False, async_profile=False)
        state = {
            "_Scalene__args": arguments,
            "_Scalene__stats": self._stats,
            "_Scalene__memory_profiler": processor,
            "_Scalene__last_profiled": ["", 0, 0],
            "_Scalene__invalidate_queue": [],
            "_Scalene__invalidate_mutex": threading.Lock(),
            "_Scalene__start_time": time.monotonic_ns(),
            "_should_trace": staticmethod(self._should_trace),
        }
        for name, value in state.items():
            self._replace_native(name, value)
        alloc_queue = signal_queue(scalene._alloc_sigqueue_processor)
        memcpy_queue = signal_queue(scalene._memcpy_sigqueue_processor)
        self._replace_native("_Scalene__alloc_sigq", alloc_queue)
        self._replace_native("_Scalene__memcpy_sigq", memcpy_queue)
        self._native_queues = [alloc_queue, memcpy_queue]
        pywhere.populate_struct()
        self._native_started = True
        self._register_native_files()
        initialize_tracer(
            state["_Scalene__last_profiled"],
            state["_Scalene__invalidate_queue"],
            self._should_trace,
        )
        if sys.platform != "win32":
            self._set_signal(signal.SIGXCPU, scalene.malloc_signal_handler)
            self._set_signal(signal.SIGXFSZ, scalene.free_signal_handler)
            self._set_signal(signal.SIGPROF, scalene.memcpy_signal_handler)
            for queue in self._native_queues:
                queue.start()

    def _register_native_files(self) -> None:
        package = importlib.import_module("scalene")
        importlib.import_module("scalene.pywhere").register_files_to_profile(
            list(self._seen), self.root, False, os.path.dirname(package.__file__ or "")
        )

    def _stop_memory(self) -> None:
        try:
            if self._native_started:
                pywhere = importlib.import_module("scalene.pywhere")
                tracer = importlib.import_module("scalene.scalene_tracer")
                pywhere.set_scalene_done_true()
                try:
                    tracer.disable_tracing()
                    for queue in self._native_queues:
                        queue.stop()
                    self._native._alloc_sigqueue_processor([0])
                    self._native._memcpy_sigqueue_processor(None, None)
                finally:
                    tracer.cleanup_tracer()
                    pywhere.depopulate_struct()
                    self._native_started = False
        finally:
            if self._native is not None:
                for name, previous in self._native_state.items():
                    if previous is _MISSING:
                        delattr(self._native, name)
                    else:
                        setattr(self._native, name, previous)
            self._native_state.clear()
            self._native_queues.clear()
            self._native = None

    def _cleanup(self) -> None:
        global _ACTIVE
        self._running = False
        try:
            if self._profile_installed:
                if sys.getprofile() == self._source_event:
                    sys.setprofile(None)
                self._profile_installed = False
            self._stop_event.set()
            if self._sampler is not None:
                self._sampler.join()
                self._sampler = None
            if sys.platform != "win32" and signal.SIGALRM in self._signals:
                signal.setitimer(signal.ITIMER_REAL, 0)
            self._stop_memory()
        finally:
            for signum, handler in self._signals.items():
                signal.signal(signum, handler)
            self._signals.clear()
            if _ACTIVE is self:
                _ACTIVE = None

    def stop(self) -> None:
        """Stop collection, detach hooks, and normalize sampled measurements."""
        if not self._running:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Stop Scalene profiling from the main Python thread")
        elapsed = time.perf_counter() - self._started
        self._cleanup()
        self._stats.stop_clock()
        self._stats.elapsed_time = elapsed
        self._raw = self._export(elapsed)
        self._clear_caches()

    def _export(self, elapsed: float) -> RawBackendResult:
        files: dict[str, dict[str, Any]] = {}
        cpu = self._stats.cpu_stats
        memory = self._stats.memory_stats
        mappings = [cpu.cpu_samples_python, cpu.cpu_samples_c]
        memory_locations: set[tuple[str, int]] = set()
        if self.memory:
            memory_mappings = [
                memory.memory_malloc_samples,
                memory.memory_free_samples,
                memory.memory_max_footprint,
            ]
            memory_locations = {
                (filename, number)
                for mapping in memory_mappings
                for filename, lines in mapping.items()
                for number, amount in lines.items()
                if amount
            }
            mappings += memory_mappings
        locations = {
            (filename, number)
            for mapping in mappings
            for filename, lines in mapping.items()
            for number in lines
            if number > 0 and self.accepts(filename)
        }
        for filename, number in sorted(locations):
            row = self._json.output_profile_line(
                fname=filename,
                fname_print=filename,
                line_no=number,
                line="",
                stats=self._stats,
                profile_this_code=lambda filename, line: True,
                profile_memory=self.memory,
                force_print=True,
            )
            if self.memory and (filename, number) in memory_locations:
                # Upstream's JSON has malloc and peak, but omits free volume.
                row["n_free_mb"] = memory.memory_free_samples[filename][number]
            else:
                for key in ("n_malloc_mb", "n_peak_mb"):
                    row.pop(key, None)
            files.setdefault(filename, {"lines": []})["lines"].append(row)
        result = normalize_scalene(
            {"elapsed_time_sec": elapsed, "files": files}, memory=self.memory
        )
        if self._sampling_error:
            result.warnings.append(self._sampling_error)
        if self.memory and not memory_locations:
            result.warnings.append(
                "No native allocation samples were recorded; Python driver memory values are unavailable for this run."
            )
        return result

    def result(self) -> RawBackendResult:
        """Return a detached final result or a consistent live sample snapshot."""
        if self._running:
            with self._sample_lock, ExitStack() as resources:
                self._sampling = True
                try:
                    for queue in self._native_queues:
                        resources.enter_context(queue.lock)
                    return self._export(time.perf_counter() - self._started)
                finally:
                    self._sampling = False
        return deepcopy(self._raw)
