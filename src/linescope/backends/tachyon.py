"""LineScope.

Author: Mavs
Description: Collect Python 3.15 Tachyon stacks outside the profiled process.

"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from enum import StrEnum
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import perf_counter_ns
from typing import Any

from linescope.backends.base import RawBackendResult, RawLine
from linescope.config import _validate_sample_rate
from linescope.enums import Backend
from linescope.model import BackendCapabilities


class SamplerMessage(StrEnum):
    """Identify sampler readiness, measurements, errors, and stop commands.

    String values form the parent and worker's process protocol.

    Attributes
    ----------
    READY : [SamplerMessage]
        Signal that the external sampler is ready to collect.

    SAMPLE : [SamplerMessage]
        Deliver one sampled stack and its observation interval.

    ERROR : [SamplerMessage]
        Report a sampler initialization or collection failure.

    STOP : [SamplerMessage]
        Request clean termination of the external sampler.

    """

    READY = "ready"
    SAMPLE = "sample"
    ERROR = "error"
    STOP = "stop"


class TachyonBackend:
    """Estimate project line time using Python 3.15's Tachyon sampler.

    A separate Python process samples the thread that starts this collector.
    External library work is charged to its nearest accepted project caller.
    Samples estimate wall time and never become line execution counts.

    Raise `RuntimeError` if Python is older than 3.15, attachment is denied,
    or collection cannot initialize. Process-memory access must be allowed by
    the OS.

    Raise `ValueError` if memory or GPU collection is requested.

    Parameters
    ----------
    accepts : Callable[[str], bool]
        Predicate defining project-owned runtime filenames.

    on_source : Callable[[str], Any]
        Snapshot a filename when its source is first observed.

    memory : bool, default=False
        Must be false; Tachyon does not collect memory measurements.

    root : str | None, default=None
        Accepted for consistency with collector factories.

    gpu : bool, default=False
        Must be false; GPU collection requires Scalene.

    sample_rate : int, default=1000
        Target samples per second. Actual collection may be slower due to
        stack capture overhead and scheduler delays.

    Attributes
    ----------
    name : [Backend] | str
        Backend identifier used in configuration and reports.

    capabilities : [BackendCapabilities]
        Sampling support without fabricated hits, memory, or GPU metrics.

    accepts : Callable[[str], bool]
        Predicate deciding whether a sampled filename belongs to the project.

    on_source : Callable[[str], Any]
        Session callback that snapshots accepted source on observation.

    sample_rate : int
        Requested sampling frequency in samples per second.

    _seen : set[str]
        Accepted filenames already passed to the snapshot callback.

    _lines : dict[tuple[str, int], [RawLine]]
        Estimated line time keyed by runtime filename and line.

    _raw : [RawBackendResult]
        Collected measurements and sampling diagnostics.

    _lock : threading.RLock
        Lock guarding samples and diagnostics shared with the reader thread.

    _ready : threading.Event
        Event signaling sampler readiness or an initialization error.

    _running : bool
        Whether the external sampler is actively collecting.

    _process : subprocess.Popen[str] | None
        Owned sampling process, when started.

    _reader : threading.Thread | None
        Thread draining sampler messages into normalized measurements.

    _error : str | None
        Most recent sampler diagnostic, or None before an error.

    See Also
    --------
    - linescope.backends.base:ProfilerBackend
    - linescope.backends.trace:TraceBackend
    - linescope.backends.scalene:ScaleneBackend

    Examples
    --------
    Inspect capabilities without starting a sampler:

    ```pycon
    from linescope.backends.tachyon import TachyonBackend
    backend = TachyonBackend(
        accepts=lambda filename: True, on_source=lambda filename: None
    )
    (backend.capabilities.sampled, backend.capabilities.hit_counts)
    ```

    """

    name: Backend | str = Backend.TACHYON
    capabilities = BackendCapabilities(hit_counts=False, sampled=True, sample_counts=True)

    def __init__(
        self,
        *,
        accepts: Callable[[str], bool],
        on_source: Callable[[str], Any],
        memory: bool = False,
        root: str | None = None,
        gpu: bool = False,
        sample_rate: int = 1000,
    ) -> None:
        """Initialize external sampling and reader synchronization.

        Reject unsupported memory and GPU requests before starting a child
        process.

        Parameters
        ----------
        accepts : Callable[[str], bool]
            Predicate identifying accepted project runtime filenames.

        on_source : Callable[[str], Any]
            Callback capturing accepted source snapshots.

        memory : bool, default=False
            Whether supported driver memory collection is requested.

        root : str | None, default=None
            Project root used for source ownership or collector setup.

        gpu : bool, default=False
            Whether supported GPU collection is requested.

        sample_rate : int, default=1000
            Requested sampling frequency in samples per second.

        """
        del root
        _validate_sample_rate(sample_rate)
        if memory or gpu:
            raise ValueError(
                "Tachyon cannot measure memory or GPU metrics; use backend='scalene'."
            )

        self.accepts = accepts
        self.on_source = on_source
        self.sample_rate = sample_rate
        self._seen: set[str] = set()
        self._lines: dict[tuple[str, int], RawLine] = {}
        self._raw = RawBackendResult(
            warnings=[
                (
                    "Tachyon line time is a sampled wall-time estimate. Hit counts, memory and "
                    "GPU metrics are unavailable; short lines may receive no samples."
                )
            ]
        )
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._running = False
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._error: str | None = None

    def _observe(self, filename: str) -> bool:
        """Snapshot each accepted sampled filename once.

        Return the project ownership decision used for sample attribution.

        Parameters
        ----------
        filename : str
            Runtime filename being observed or resolved.

        Returns
        -------
        bool
            Whether the runtime filename belongs to accepted project source.

        """
        accepted = self.accepts(filename)

        if accepted and filename not in self._seen:
            self.on_source(filename)
            self._seen.add(filename)

        return accepted

    def _collect(self, frames: Sequence[Sequence[Any]], duration_ns: int = 1_000_000) -> None:
        # Tachyon returns stacks leaf first. Observe every project source, but
        # charge each sample exactly once to the nearest relevant project line.
        """Charge a sample interval to its nearest project line.

        Observe every project filename and keep sampling counts separate from
        exact hits.

        Parameters
        ----------
        frames : Sequence[Sequence[Any]]
            Sampled stack entries used for project attribution.

        duration_ns : int, default=1000000
            Observed sampling interval in nanoseconds.

        """
        nearest = None

        for filename, number in frames:
            if self._observe(filename) and number > 0 and nearest is None:
                nearest = (filename, number)

        if nearest is not None:
            line = self._lines.setdefault(nearest, RawLine(*nearest, wall_time_ns=0, samples=0))
            line.wall_time_ns = (line.wall_time_ns or 0) + duration_ns
            line.samples = (line.samples or 0) + 1

    def _read(self) -> None:
        """Drain sampler messages into measurements and diagnostics.

        Signal readiness on either successful initialization or a reported
        startup failure.

        """
        process = self._process
        assert process is not None
        assert process.stdout is not None

        try:
            for record in process.stdout:
                message = json.loads(record)

                with self._lock:
                    if message["type"] == SamplerMessage.READY:
                        self._ready.set()
                    elif message["type"] == SamplerMessage.SAMPLE:
                        self._collect(message["frames"], message["duration_ns"])
                    elif message["type"] == SamplerMessage.ERROR:
                        self._error = message["message"]
                        self._raw.warnings.append(self._error)
                        self._ready.set()
        except Exception as error:  # noqa: BLE001
            # The optional process integration must not interrupt user code.
            with self._lock:
                self._error = f"Tachyon collection failed: {type(error).__name__}: {error}"
                self._raw.warnings.append(self._error)
        finally:
            if not self._ready.is_set():
                self._error = self._error or "Tachyon sampler exited before attachment completed"
                self._ready.set()

    def start(self) -> None:
        """Attach a collector process without replacing Python trace hooks.

        Wait for the sampler to signal readiness before accepting
        measurements.

        """
        if sys.version_info < (3, 15):
            raise RuntimeError(
                "The Tachyon backend requires Python 3.15 or newer; use backend='trace'."
            )

        if self._running:
            raise RuntimeError("This Tachyon collector is already running")

        self._ready.clear()
        self._error = None
        self._seen.clear()
        self._lines.clear()
        frame = sys._getframe(1)

        while frame is not None:
            self._observe(frame.f_code.co_filename)
            frame = frame.f_back

        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[2]), environment.get("PYTHONPATH", "")]
        )

        try:
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "linescope.backends.tachyon",
                    str(os.getpid()),
                    str(threading.get_native_id()),
                    str(self.sample_rate),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=environment,
                **(
                    {"creationflags": subprocess.CREATE_NO_WINDOW}
                    if sys.platform == "win32"
                    else {}
                ),
            )
            self._reader = threading.Thread(
                target=self._read, name="linescope-tachyon", daemon=True
            )
            self._reader.start()

            if not self._ready.wait(10):
                raise RuntimeError("Tachyon attachment timed out")

            if self._error:
                raise RuntimeError(self._error)

            self._running = True
        except BaseException:
            self._close()
            raise

    def _close(self) -> None:
        """Stop the owned sampler and release pipes and reader state.

        Record forced cleanup as a diagnostic instead of inventing successful
        collection.

        """
        process = self._process

        if process is None:
            return

        try:
            if process.poll() is None:
                if process.stdin is not None:
                    try:
                        process.stdin.write(f"{SamplerMessage.STOP}\n")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass

                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self._raw.warnings.append(
                        "Tachyon sampler required termination during cleanup"
                    )

            if self._reader is not None:
                self._reader.join(timeout=5)

            if process.returncode and self._error is None:
                self._raw.warnings.append(
                    f"Tachyon sampler exited with status {process.returncode}"
                )
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()

            self._process = None
            self._reader = None
            self._running = False

    def stop(self) -> None:
        """Stop the sampler, drain collected stacks, and release its pipes.

        Retain collected measurements for reporting after releasing the
        sampler.

        """
        self._close()

    def result(self) -> RawBackendResult:
        """Return a detached, consistent snapshot, including while running.

        Copy measurements while holding the lock shared with the sampler
        reader.

        """
        with self._lock:
            result = deepcopy(self._raw)
            result.lines = deepcopy(list(self._lines.values()))
            return result


def _send(message: dict[str, object]) -> None:
    """Write one JSON protocol record to the parent process.

    Flush immediately so readiness and sample messages are observable without
    buffering delays.

    Parameters
    ----------
    message : dict[str, object]
        JSON protocol record sent to the sampler parent.

    """
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _run(pid: int, thread_id: int, sample_rate: int = 1000) -> None:
    """Sample the requested process and thread from an external interpreter.

    Report initialization errors through the protocol and stop when the control
    event is set.

    Parameters
    ----------
    pid : int
        Process identifier whose Python stacks are sampled.

    thread_id : int
        Target thread identifier used to select sampled stacks.

    sample_rate : int, default=1000
        Requested sampling frequency in samples per second.

    """
    _validate_sample_rate(sample_rate)
    interval = 1 / sample_rate
    # Import inside the helper so Python 3.11-3.14 can import LineScope normally.
    sampling = importlib.import_module("profiling.sampling.sample")
    profiler = sampling.SampleProfiler(pid, max(1, round(interval * 1_000_000)), all_threads=True)
    # Verify attachment before allowing the workload to proceed.
    profiler.dump_stack()
    _send({"type": SamplerMessage.READY})
    stopped = threading.Event()

    def control() -> None:
        """Read parent control messages until sampling is asked to stop.

        Set the shared event when the parent pipe closes or a stop record
        arrives.

        """
        sys.stdin.readline()
        stopped.set()

    threading.Thread(target=control, daemon=True).start()
    previous = perf_counter_ns()

    while not stopped.wait(interval):
        try:
            stack = profiler.dump_stack()
        except (RuntimeError, UnicodeDecodeError):
            # Inconsistent stacks are a documented non-blocking sampling limitation.
            continue

        for interpreter in stack:
            for thread in interpreter.threads:
                if thread.thread_id != thread_id:
                    continue

                frames = [
                    [frame.filename, frame.location.lineno if frame.location is not None else 0]
                    for frame in thread.frame_info
                ]
                now = perf_counter_ns()
                _send(
                    {
                        "type": SamplerMessage.SAMPLE,
                        "frames": frames,
                        "duration_ns": now - previous,
                    }
                )
                previous = now


# The backend launches this module in a separate interpreter to sample its caller.
if __name__ == "__main__":
    try:
        _run(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 1000)
    except BaseException as error:  # noqa: BLE001
        _send(
            {
                "type": SamplerMessage.ERROR,
                "message": (
                    f"Tachyon attachment/collection failed: {type(error).__name__}: {error}. "
                    "Verify that the OS permits reading this Python process's memory, "
                    "or explicitly select backend='trace'."
                ),
            }
        )
        sys.exit(1)
