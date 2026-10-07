"""LineScope.

Author: Mavs
Description: Preserve workload errors and unknown metrics at API boundaries.

"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import linescope
from linescope import ProfileController, Session
from linescope.backends.base import RawBackendResult, RawLine
from linescope.model import BackendCapabilities, GPUStats, MemoryStats


def backend(raw=None):
    """Provide a detached collector without installing interpreter hooks.

    Allow deterministic measurements and cleanup errors through the public
    session lifecycle.

    """
    return SimpleNamespace(
        name="custom",
        capabilities=BackendCapabilities(),
        start=Mock(),
        stop=Mock(),
        result=Mock(return_value=raw or RawBackendResult()),
    )


@pytest.mark.parametrize("workload_failed", [False, True])
def test_cleanup_failure_preserves_workload_error_and_releases_lock(monkeypatch, workload_failed):
    """Keep the original workload exception when collector cleanup also fails.

    Expose cleanup failure directly after a successful workload and ensure
    another session can start after either error path.

    """
    collector = backend()
    collector.stop.side_effect = RuntimeError("cleanup failed")
    monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: collector)
    session = Session(display="none", spark=False, notebooks=False)
    if workload_failed:
        with pytest.raises(ValueError, match="workload failed"), pytest.warns(RuntimeWarning):
            with session:
                raise ValueError("workload failed")
    else:
        with pytest.raises(RuntimeError, match="cleanup failed"), session:
            pass
    assert session.state == "stopped"
    collector.stop.side_effect = None
    with Session(display="none", spark=False, notebooks=False):
        pass


def test_custom_backend_without_gpu_rejects_requested_collection(monkeypatch):
    """Reject unsupported GPU requests before starting a custom collector.

    Release session ownership so repeated invalid requests cannot lock out
    future profiling runs.

    """
    collector = backend()
    monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: collector)
    for _ in range(2):
        with pytest.raises(ValueError, match="cannot collect GPU"):
            Session(gpu=True, display="none", notebooks=False, spark=False).start()
    collector.start.assert_not_called()


def test_duplicate_optional_metrics_accumulate_without_fabricating_unknowns(tmp_path, monkeypatch):
    """Merge available allocation and device costs without mutating raw rows.

    Preserve unknown measurements, take peaks independently of additive costs,
    and ignore invalid or unrelated source locations.

    """
    path = tmp_path / "worker.py"
    path.write_text("value = 1\n", encoding="utf-8")
    raw = RawBackendResult(
        [
            RawLine(str(path), 1, memory=MemoryStats(), gpu=GPUStats()),
            RawLine(str(path), 1, memory=MemoryStats(-2, 4), gpu=GPUStats(2, 4)),
            RawLine(str(path), 1, memory=MemoryStats(3, 2), gpu=GPUStats(3, 2)),
            RawLine(str(path), 1, memory=MemoryStats(), gpu=GPUStats()),
            RawLine(str(path), 0, gpu=GPUStats(999)),
            RawLine(str(tmp_path / "missing.py"), 1, gpu=GPUStats(999)),
        ]
    )
    collector = backend(raw)
    monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: collector)
    session = Session(root=tmp_path, display="none", notebooks=False, spark=False)
    session._refresh()
    with session:
        session._refresh()
    row = session.result.root_run.lines[0]
    assert row.memory == MemoryStats(1, 4)
    assert row.gpu == GPUStats(5, 4)
    assert row.wall_time_ns is None
    assert row.hits is None
    assert raw.lines[0].gpu == GPUStats()
    assert raw.lines[0].memory == MemoryStats()


def test_extension_entry_points_and_controller_show_delegate(monkeypatch):
    """Forward extension loading and report display through their owners.

    Keep top-level extension entry points lazy and preserve explicit display
    preferences when the controller forwards to its active session.

    """
    load, unload = Mock(), Mock()
    monkeypatch.setattr("linescope.notebooks.ipython.load_ipython_extension", load)
    monkeypatch.setattr("linescope.notebooks.ipython.unload_ipython_extension", unload)
    shell = object()
    linescope.load_ipython_extension(shell)
    linescope.unload_ipython_extension(shell)
    load.assert_called_once_with(shell)
    unload.assert_called_once_with(shell)
    controller = ProfileController()
    controller._session = SimpleNamespace(show=Mock(return_value="report"))
    assert controller.show(inline=True) is None
    controller._session.show.assert_called_once_with(inline=True)


def test_inline_show_requires_a_live_shell(monkeypatch):
    """Reject inline display when no IPython shell owns the output.

    Keep explicit inline requests from silently writing or opening a report.

    """
    monkeypatch.setattr("IPython.get_ipython", lambda: None)
    monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: backend())
    session = Session(display="none", spark=False, notebooks=False)
    with session:
        pass
    with pytest.raises(RuntimeError, match="active IPython"):
        session.show(inline=True)


def test_controller_retains_result_and_releases_hooks_after_display_failure(monkeypatch):
    """Keep finalized measurements accessible when final display fails.

    Release collector ownership and make a repeated stop harmless so callers
    can inspect the result and start another session after the error.

    """
    collector = backend()
    monkeypatch.setattr("linescope.api.create_backend", lambda *_args, **_kwargs: collector)
    controller = ProfileController()
    session = controller.start(display="end", spark=False, notebooks=False)
    show = Mock(side_effect=RuntimeError("display unavailable"))
    monkeypatch.setattr(session, "show", show)

    with pytest.raises(RuntimeError, match="display unavailable"):
        controller.stop()

    assert session.state == "stopped"
    assert controller.result is session.result
    assert controller.result.root_run.elapsed_ns is not None
    assert controller.stop() is None
    assert session.stop() is None
    show.assert_called_once_with()
    collector.stop.assert_called_once_with()
    with Session(display="none", spark=False, notebooks=False):
        pass
