"""LineScope.

Author: Mavs
Description: Check Tachyon sampling attribution and collector resource cleanup.

"""

from io import StringIO
import json
import subprocess
import sys

import pytest

from linescope import Session
from linescope.backends.base import create_backend, register_backend
from linescope.backends.tachyon import TachyonBackend


def backend(observed=None):
    return TachyonBackend(
        accepts=lambda name: name in ("caller.py", "callee.py"),
        on_source=(observed if observed is not None else []).append,
    )


def test_external_attribution_snapshots_and_unknown_counts():
    observed = []
    collector = backend(observed)
    collector._collect([["stdlib.py", 3], ["callee.py", 7], ["caller.py", 8]])
    collector._collect([["callee.py", 7], ["caller.py", 8]])
    result = collector.result()
    assert observed == ["callee.py", "caller.py"]
    assert [(line.filename, line.line) for line in result.lines] == [("callee.py", 7)]
    assert result.lines[0].wall_time_ns > 0
    assert result.lines[0].hits is None
    assert result.lines[0].memory is None
    result.lines.clear()
    assert collector.result().lines


def test_no_project_frame_produces_no_fake_measurements():
    collector = backend()
    collector._collect([["stdlib.py", 7]])
    collector._collect([["caller.py", 0]])
    assert collector.result().lines == []


@pytest.mark.parametrize("option", ["memory", "gpu"])
def test_unsupported_metrics_rejected(option):
    with pytest.raises(ValueError, match="cannot measure"):
        TachyonBackend(accepts=lambda _: True, on_source=lambda _: None, **{option: True})


def test_factory_reserved_name_and_old_runtime(monkeypatch):
    collector = create_backend("tachyon", accepts=lambda _: True, on_source=lambda _: None)
    assert isinstance(collector, TachyonBackend)
    with pytest.raises(ValueError, match="already registered"):
        register_backend("tachyon", TachyonBackend)
    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 14))
    with pytest.raises(RuntimeError, match=r"requires Python 3.15"):
        collector.start()
    collector.stop()


class FakeProcess:
    def __init__(self, messages):
        self.stdout = StringIO("\n".join(json.dumps(item) for item in messages))
        self.stdin = StringIO()
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True


def test_start_live_result_and_stop_drain_pipes(monkeypatch):
    process = FakeProcess(
        [
            {"type": "ready"},
            {"type": "sample", "frames": [["caller.py", 9]], "duration_ns": 12345},
        ]
    )
    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 15))
    monkeypatch.setattr(
        "linescope.backends.tachyon.subprocess.Popen", lambda *_args, **_kwargs: process
    )
    collector = backend()
    previous = sys.gettrace(), sys.getprofile()
    collector.start()
    collector._reader.join(timeout=1)
    assert collector.result().lines[0].line == 9
    with pytest.raises(RuntimeError, match="already running"):
        collector.start()
    collector.stop()
    collector.stop()
    assert process.stdout.closed
    assert process.stdin.closed
    assert collector._process is None
    assert (sys.gettrace(), sys.getprofile()) == previous


def test_attachment_failure_restores_session_resources(monkeypatch):
    processes = []

    def start_process(*args, **kwargs):
        del args, kwargs
        process = FakeProcess([{"type": "error", "message": "permission denied"}])
        processes.append(process)
        return process

    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 15))
    monkeypatch.setattr("linescope.backends.tachyon.subprocess.Popen", start_process)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="permission denied"):
            Session(backend="tachyon", display="none", notebooks=False, spark=False).start()
    assert all(process.stdout.closed and process.stdin.closed for process in processes)


def test_forced_cleanup_keeps_diagnostic(monkeypatch):
    del monkeypatch
    process = FakeProcess([])

    def wait(timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired("sampler", timeout)
        process.returncode = 1

    process.wait = wait
    collector = backend()
    collector._process = process
    collector.stop()
    assert process.killed
    assert any("termination" in message for message in collector.result().warnings)


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Requires the Python 3.15 sampler")
def test_real_sampling_and_error_cleanup(tmp_path):
    source = tmp_path / "sampled.py"
    source.write_text(
        "from time import sleep\nsleep(0.15)\nraise ValueError('workload failed')\n",
        encoding="utf-8",
    )
    session = Session(
        backend="tachyon", root=str(tmp_path), notebooks=False, spark=False, display="none"
    )
    try:
        with pytest.raises(ValueError, match="workload failed"), session:
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), {})
    except RuntimeError as error:
        if "permission" in str(error).lower() or "not permitted" in str(error).lower():
            pytest.skip(str(error))
        raise
    assert session.state == "stopped"
    assert session._backend._process is None
    lines = session.result.root_run.lines
    assert any(line.wall_time_ns is not None and line.wall_time_ns > 0 for line in lines)
    assert all(line.hits is None for line in lines)
    assert session.result.root_run.status == "failed"
