"""LineScope.

Author: Mavs
Description: Run the standard-library sampler in an isolated helper interpreter.

"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from time import perf_counter_ns

from linescope.backends._tachyon_protocol import SamplerMessage


def _send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _run(pid: int, thread_id: int) -> None:
    # Import inside the helper so Python 3.11-3.14 can import LineScope normally.
    sampling = importlib.import_module("profiling.sampling.sample")
    profiler = sampling.SampleProfiler(pid, 1000, all_threads=True)
    # Verify attachment before allowing the workload to proceed.
    profiler.dump_stack()
    _send({"type": SamplerMessage.READY})
    stopped = threading.Event()

    def control() -> None:
        sys.stdin.readline()
        stopped.set()

    threading.Thread(target=control, daemon=True).start()
    previous = perf_counter_ns()

    while not stopped.wait(0.001):
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


if __name__ == "__main__":
    try:
        _run(int(sys.argv[1]), int(sys.argv[2]))
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
