"""LineScope.

Author: Mavs
Description: Collect Python 3.15 Tachyon stacks outside the profiled process.

"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any

from linescope.backends._tachyon_protocol import SamplerMessage
from linescope.backends.base import RawBackendResult, RawLine
from linescope.enums import Backend
from linescope.model import BackendCapabilities


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
    capabilities = BackendCapabilities(hit_counts=False, sampled=True)

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
        if memory or gpu:
            raise ValueError(
                "Tachyon cannot measure memory or GPU metrics; use backend='scalene'."
            )

        self.accepts = accepts
        self.on_source = on_source
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
        self._interval_ns = 1_000_000

    def _observe(self, filename: str) -> bool:
        accepted = self.accepts(filename)

        if accepted and filename not in self._seen:
            self.on_source(filename)
            self._seen.add(filename)

        return accepted

    def _collect(self, frames: Sequence[Sequence[Any]], duration_ns: int = 1_000_000) -> None:
        # Tachyon returns stacks leaf first. Observe every project source, but
        # charge each sample exactly once to the nearest relevant project line.
        nearest = None

        for filename, number in frames:
            if self._observe(filename) and number > 0 and nearest is None:
                nearest = (filename, number)

        if nearest is not None:
            line = self._lines.setdefault(nearest, RawLine(*nearest, wall_time_ns=0))
            line.wall_time_ns = (line.wall_time_ns or 0) + duration_ns

    def _read(self) -> None:
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
                    "linescope.backends._tachyon_worker",
                    str(os.getpid()),
                    str(threading.get_native_id()),
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
