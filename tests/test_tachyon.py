"""LineScope.

Author: Mavs
Description: Check Tachyon sampling attribution and collector resource cleanup.

"""

from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope import Session
from linescope.backends.base import create_backend, register_backend
import linescope.backends.tachyon as tachyon
from linescope.backends.tachyon import TachyonBackend


def backend(observed=None):
    """Provide an external sampling collector with controlled source callbacks.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    return TachyonBackend(
        accepts=lambda name: name in ("caller.py", "callee.py"),
        on_source=(observed if observed is not None else []).append,
    )


def test_external_attribution_snapshots_and_unknown_counts():
    """Verify external attribution snapshots and unknown counts.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
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
    """Verify no project frame produces no fake measurements.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    collector = backend()
    collector._collect([["stdlib.py", 7]])
    collector._collect([["caller.py", 0]])
    assert collector.result().lines == []


@pytest.mark.parametrize("option", ["memory", "gpu"])
def test_unsupported_metrics_rejected(option):
    """Verify unsupported metrics rejected.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    with pytest.raises(ValueError, match="cannot measure"):
        TachyonBackend(accepts=lambda _: True, on_source=lambda _: None, **{option: True})


def test_factory_reserved_name_and_old_runtime(monkeypatch):
    """Verify factory reserved name and old runtime.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    collector = create_backend("tachyon", accepts=lambda _: True, on_source=lambda _: None)
    assert isinstance(collector, TachyonBackend)
    with pytest.raises(ValueError, match="already registered"):
        register_backend("tachyon", TachyonBackend)
    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 14))
    with pytest.raises(RuntimeError, match=r"requires Python 3.15"):
        collector.start()
    collector.stop()


class FakeProcess:
    """Emulate sampler pipes, process status, and cleanup failures.

    Attributes
    ----------
    stdout : StringIO
        Controlled JSON message stream drained by the sampler reader.

    stdin : StringIO
        Captured stop commands written during sampler cleanup.

    returncode : int | None
        Simulated process exit status, or None while still running.

    killed : bool
        Whether forced process termination was requested.

    """

    def __init__(self, messages):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        self.stdout = StringIO("\n".join(json.dumps(item) for item in messages))
        self.stdin = StringIO()
        self.returncode = None
        self.killed = False

    def poll(self):
        """Return the simulated process status without waiting.

        Keep None distinguishable from a completed exit status.

        """
        return self.returncode

    def wait(self, timeout=None):
        """Simulate process completion or a configured termination timeout.

        Record completion or the configured wait behavior for shutdown
        assertions.

        """
        del timeout
        self.returncode = 0
        return 0

    def kill(self):
        """Record forced termination of the simulated sampler process.

        Retain the termination request for cleanup diagnostics and ownership
        checks.

        """
        self.killed = True


def test_start_live_result_and_stop_drain_pipes(monkeypatch):
    """Verify start live result and stop drain pipes.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    process = FakeProcess(
        [
            {"type": "ready"},
            {"type": "sample", "frames": [["caller.py", 9]], "duration_ns": 12345},
        ]
    )
    commands = []

    def start_process(command, **kwargs):
        """Return a controlled sampler process for startup failure assertions.

        Use controlled stacks and sampler processes unless the case explicitly
        selects real Python 3.15 sampling.

        """
        del kwargs
        commands.append(command)
        return process

    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 15))
    monkeypatch.setattr("linescope.backends.tachyon.subprocess.Popen", start_process)
    collector = backend()
    previous = sys.gettrace(), sys.getprofile()
    collector.start()
    assert commands[0][:4] == [sys.executable, "-u", "-m", "linescope.backends.tachyon"]
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


@pytest.mark.parametrize("attachment_denied", [False, True])
def test_module_worker_entry_point(tmp_path, monkeypatch, attachment_denied):
    """Verify module worker entry point.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    sampling = tmp_path / "profiling" / "sampling"
    sampling.mkdir(parents=True)
    (sampling.parent / "__init__.py").write_text("", encoding="utf-8")
    (sampling / "__init__.py").write_text("", encoding="utf-8")
    (sampling / "sample.py").write_text(
        "class SampleProfiler:\n"
        "    def __init__(self, pid, interval, *, all_threads):\n"
        "        assert (pid, interval, all_threads) == (123, 1000, True)\n"
        "    def dump_stack(self):\n"
        + (
            "        raise PermissionError('attachment denied')\n"
            if attachment_denied
            else "        return []\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([str(tmp_path), str(Path(tachyon.__file__).resolve().parents[2])]),
    )

    result = subprocess.run(
        [sys.executable, "-u", "-m", "linescope.backends.tachyon", "123", "456"],
        input="stop\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        timeout=10,
    )
    messages = [json.loads(record) for record in result.stdout.splitlines()]
    assert result.stderr == ""
    if attachment_denied:
        assert result.returncode == 1
        assert len(messages) == 1
        assert messages[0]["type"] == "error"
        assert "PermissionError: attachment denied" in messages[0]["message"]
    else:
        assert result.returncode == 0
        assert messages == [{"type": "ready"}]


def test_attachment_failure_restores_session_resources(monkeypatch):
    """Verify attachment failure restores session resources.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    processes = []

    def start_process(*args, **kwargs):
        """Return a controlled sampler process for startup failure assertions.

        Use controlled stacks and sampler processes unless the case explicitly
        selects real Python 3.15 sampling.

        """
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
    """Verify forced cleanup keeps diagnostic.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
    del monkeypatch
    process = FakeProcess([])

    def wait(timeout=None):
        """Simulate process completion or a configured termination timeout.

        Record completion or the configured wait behavior for shutdown
        assertions.

        """
        if timeout is not None:
            raise subprocess.TimeoutExpired("sampler", timeout)
        process.returncode = 1

    process.wait = wait
    collector = backend()
    collector._process = process
    collector.stop()
    assert process.killed
    assert any("termination" in message for message in collector.result().warnings)


@pytest.mark.parametrize("record", ["", "not json", '{"type": "unknown"}'])
def test_reader_reports_incomplete_or_corrupt_attachment(record):
    """Report a missing readiness message without blocking sampler startup.

    Keep malformed protocol records diagnostic and wake the waiting parent
    even when the worker exits before attachment completes.

    """
    collector = backend()
    collector._process = FakeProcess([])
    collector._process.stdout = StringIO(record)
    collector._read()
    assert collector._ready.is_set()
    assert collector._error
    if record == "not json":
        assert "JSONDecodeError" in collector.result().warnings[-1]
    else:
        assert "before attachment" in collector._error
    collector.stop()


@pytest.mark.parametrize("failure", ["timeout", "spawn"])
def test_startup_timeout_and_spawn_error_release_resources(monkeypatch, failure):
    """Release sampler ownership after attachment timeout or process failure.

    Use a controlled reader that creates no real thread and never waits for
    the ten-second attachment deadline.

    """
    process = FakeProcess([])
    reader = SimpleNamespace(start=Mock(), join=Mock())
    collector = backend()
    collector._ready = SimpleNamespace(clear=Mock(), wait=Mock(return_value=False))
    monkeypatch.setattr(
        tachyon,
        "sys",
        SimpleNamespace(
            version_info=(3, 15),
            platform="linux",
            executable=sys.executable,
            _getframe=lambda _: None,
        ),
    )
    monkeypatch.setattr(
        tachyon,
        "threading",
        SimpleNamespace(
            Thread=Mock(return_value=reader),
            get_native_id=lambda: 123,
        ),
    )
    monkeypatch.setattr(
        tachyon.subprocess,
        "Popen",
        Mock(
            side_effect=OSError("spawn failed") if failure == "spawn" else None,
            return_value=process,
        ),
    )
    with pytest.raises((OSError, RuntimeError), match=r"spawn failed|timed out"):
        collector.start()
    assert collector._process is None
    assert collector._reader is None
    assert not collector._running
    if failure == "timeout":
        assert process.stdout.closed
        assert process.stdin.closed
        reader.join.assert_called_once()


@pytest.mark.parametrize("stdin", ["absent", "broken", "closed"])
def test_shutdown_handles_missing_or_broken_control_pipe(stdin):
    """Release worker pipes after missing stdin or a failed stop write.

    Preserve a process exit diagnostic while tolerating an already closed
    control pipe.

    """
    process = FakeProcess([])
    if stdin == "absent":
        process.stdin = None
        process.stdout = None
    elif stdin == "broken":
        process.stdin = Mock()
        process.stdin.write.side_effect = BrokenPipeError("worker exited")
    else:
        process.returncode = 2
    collector = backend()
    collector._process = process
    collector.stop()
    assert collector._process is None
    if stdin == "closed":
        assert "status 2" in collector.result().warnings[-1]


def test_worker_samples_target_thread_and_recovers_inconsistent_stacks(monkeypatch):
    """Keep sampling after transient stack errors and select the target thread.

    Retain unknown source locations as zero protocol lines, omit unrelated
    threads, and verify that parent control wakes the stop event.

    """
    target = SimpleNamespace(
        thread_id=456,
        frame_info=[
            SimpleNamespace(filename="caller.py", location=SimpleNamespace(lineno=7)),
            SimpleNamespace(filename="native.py", location=None),
        ],
    )
    stack = [SimpleNamespace(threads=[SimpleNamespace(thread_id=999), target])]
    profiler = SimpleNamespace(
        dump_stack=Mock(
            side_effect=[
                [],
                RuntimeError("inconsistent"),
                UnicodeDecodeError("utf8", b"x", 0, 1, "stack"),
                stack,
            ]
        )
    )
    factory = Mock(return_value=profiler)
    stopped = SimpleNamespace(wait=Mock(side_effect=[False, False, False, True]), set=Mock())
    thread = Mock(return_value=SimpleNamespace(start=Mock()))
    monkeypatch.setattr(
        tachyon,
        "threading",
        SimpleNamespace(
            Event=lambda: stopped,
            Thread=thread,
        ),
    )
    monkeypatch.setattr(
        tachyon,
        "importlib",
        SimpleNamespace(
            import_module=lambda _: SimpleNamespace(SampleProfiler=factory),
        ),
    )
    monkeypatch.setattr(tachyon, "sys", SimpleNamespace(stdin=StringIO("stop\n")))
    messages = []
    monkeypatch.setattr(tachyon, "_send", messages.append)
    tachyon._run(123, 456, 2000)
    factory.assert_called_once_with(123, 500, all_threads=True)
    assert messages[0] == {"type": "ready"}
    assert messages[1]["frames"] == [["caller.py", 7], ["native.py", 0]]
    assert messages[1]["duration_ns"] >= 0
    thread.call_args.kwargs["target"]()
    stopped.set.assert_called_once()


def test_protocol_writer_escapes_unicode_and_flushes(monkeypatch):
    """Emit a complete ASCII JSON record and flush it for the parent reader.

    Preserve Unicode filename data when decoding the process protocol.

    """
    stream = Mock(wraps=StringIO())
    monkeypatch.setattr(tachyon, "sys", SimpleNamespace(stdout=stream))
    tachyon._send({"filename": "café.py"})
    assert json.loads(stream.write.call_args.args[0]) == {"filename": "café.py"}
    assert stream.write.call_args.args[0].isascii()
    assert stream.write.call_args.args[0].endswith("\n")
    stream.flush.assert_called_once()


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Requires the Python 3.15 sampler")
def test_real_sampling_and_error_cleanup(tmp_path):
    """Verify real sampling and error cleanup.

    Use controlled stacks and sampler processes unless the case explicitly
    selects real Python 3.15 sampling.

    """
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
