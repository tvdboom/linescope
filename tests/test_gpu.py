"""LineScope.

Author: Mavs
Description: Check GPU metrics remain separate, optional and visible in reports.

"""

from contextlib import nullcontext
import sys
from types import SimpleNamespace
import warnings

import pytest

from linescope import Session
from linescope.backends.scalene import ScaleneBackend, normalize_scalene
from linescope.backends.trace import TraceBackend
from linescope.model import (
    BackendCapabilities,
    GPUStats,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render import render_html
from tests.test_scalene import payload, run_probe


def test_gpu_normalization_keeps_wall_time_separate_and_unknowns():
    """Verify gpu normalization keeps wall time separate and unknowns.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    result = normalize_scalene(
        payload({"gpu_time_ns": 123000, "n_gpu_peak_memory_mb": 2}), gpu=True
    )
    assert result.lines[0].gpu == GPUStats(123000, 2 * 1024**2)
    assert result.lines[0].wall_time_ns is None
    assert normalize_scalene(payload({}), gpu=True).lines[0].gpu is None
    assert normalize_scalene(payload({"gpu_time_ns": 0}), gpu=True).lines[0].gpu.time_ns == 0
    assert normalize_scalene(payload({"gpu_time_ns": float("nan")}), gpu=True).lines[0].gpu is None
    assert normalize_scalene(payload({"gpu_time_ns": 123000})).lines[0].gpu is None


def test_missing_device_is_diagnostic_instead_of_fabricated_gpu(monkeypatch):
    """Verify missing device is diagnostic instead of fabricated gpu.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    collector = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None, gpu=True)
    monkeypatch.setattr("linescope.backends.scalene.sys.platform", "linux")
    monkeypatch.setattr(
        "linescope.backends.scalene._component",
        lambda *_args: lambda: SimpleNamespace(has_gpu=lambda: False),
    )
    collector._start_gpu()
    assert not collector.capabilities.gpu
    assert "unavailable" in collector._gpu_error
    assert collector._accelerator is None


def test_supported_device_passes_measurements_to_processor(monkeypatch):
    """Verify supported device passes measurements to processor.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    collector = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None, gpu=True)
    monkeypatch.setattr("linescope.backends.scalene.sys.platform", "win32")
    device = SimpleNamespace(has_gpu=lambda: True, get_stats=lambda: (0.5, 16))
    monkeypatch.setattr("linescope.backends.scalene._component", lambda *_args: lambda: device)
    collector._start_gpu()
    assert collector.capabilities.gpu
    samples = []
    collector._time = lambda: "current"
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename="project.py"), f_lineno=3)
    collector._frames = lambda _predicate: [(frame, 1, frame)]
    collector._previous = "previous"
    collector._sleeping = {1: False}
    collector._processor = SimpleNamespace(process_cpu_sample=lambda *args: samples.append(args))
    collector._collect_sample()
    assert samples[0][2:4] == (0.5, 16)
    collector._cleanup()
    assert collector._accelerator is None


def test_gpu_report_columns_and_filename_labels(tmp_path):
    """Verify gpu report columns and filename labels.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    path = tmp_path / "jobs" / "worker.py"
    source = SourceUnit(str(path), str(path), "compute()\n")
    line = LineStats(SourceLocation(source.id, 1), wall_time_ns=1000, gpu=GPUStats(500, 4096))
    result = ProfileResult(
        ProfileRun(lines=[line]),
        {source.id: source},
        "scalene",
        BackendCapabilities(sampled=True, gpu=True),
    )
    html = render_html(result, root=tmp_path)
    assert 'class="path-title">worker.py</h1>' in html
    assert "jobs/worker.py" not in html
    assert str(tmp_path) not in html
    assert "GPU time" in html
    assert "GPU peak memory" in html
    assert "4.1 KB" in html
    assert "Offline report" not in html
    assert "snapshotted at collection" not in html
    assert "click underlined symbols" not in html
    assert "data:image/svg+xml;base64," in html


@pytest.mark.parametrize(
    "backend_options", [{}, {"backend": "trace"}], ids=["default", "explicit"]
)
@pytest.mark.parametrize("workload_failed", [False, True])
def test_trace_gpu_request_warns_and_preserves_python_profiling(
    tmp_path,
    backend_options,
    workload_failed,
):
    """Warn once and retain Python measurements without inventing GPU values.

    Keep the diagnostic in session results after repeated snapshots and restore
    interpreter hooks and session ownership after success or a workload error.

    """
    source = "value = sum(range(10))\n"
    path = tmp_path / "workload.py"
    path.write_text(source, encoding="utf-8")
    previous_trace, previous_profile = sys.gettrace(), sys.getprofile()
    session = Session(
        **backend_options,
        gpu=True,
        root=str(tmp_path),
        display="none",
        notebooks=False,
        spark=False,
    )
    expected_error = (
        pytest.raises(RuntimeError, match="workload failed") if workload_failed else nullcontext()
    )
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always", RuntimeWarning)
        with expected_error, session:
            scope = {}
            exec(compile(source, str(path), "exec"), scope)
            session._refresh()
            session._refresh()
            if workload_failed:
                raise RuntimeError("workload failed")

    assert len(emitted) == 1
    assert emitted[0].category is RuntimeWarning
    diagnostic = str(emitted[0].message)
    assert "backend='scalene'" in diagnostic
    assert session.config.gpu
    assert session.result.backend == "trace"
    assert not session.result.capabilities.gpu
    assert session.result.warnings.count(diagnostic) == 1
    assert session.result.root_run.lines
    assert all(line.gpu is None for line in session.result.root_run.lines)
    assert any(line.hits for line in session.result.root_run.lines)
    assert scope["value"] == 45
    assert not session._backend._running
    assert sys.gettrace() is previous_trace
    assert sys.getprofile() is previous_profile
    report = render_html(session.result, root=tmp_path)
    assert "GPU time" not in report
    assert "GPU peak memory" not in report
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass


@pytest.mark.parametrize("gpu", [False, True])
def test_trace_backend_gpu_diagnostic_is_optional_and_detached(gpu):
    """Retain the GPU warning only when device measurements were requested.

    Allow direct collector use and keep caller edits to result diagnostics
    independent of future snapshots.

    """
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always", RuntimeWarning)
        collector = TraceBackend(accepts=lambda _: True, on_source=lambda _: None, gpu=gpu)
    assert len(emitted) == int(gpu)
    result = collector.result()
    diagnostics = [message for message in result.warnings if "GPU" in message]
    assert len(diagnostics) == int(gpu)
    result.warnings.clear()
    assert collector.result().warnings


def test_trace_gpu_warning_as_error_releases_session_lock():
    """Release session ownership when the GPU warning becomes an error.

    Leave interpreter hooks untouched after repeated startup failures and
    allow a subsequent Python-only session to start.

    """
    previous_trace = sys.gettrace()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for _ in range(2):
            with pytest.raises(RuntimeWarning, match=r"trace.*GPU"):
                Session(backend="trace", gpu=True, notebooks=False, spark=False).start()
            assert sys.gettrace() is previous_trace
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass


def test_tachyon_gpu_request_releases_session_lock():
    """Reject unsupported Tachyon device collection without retaining ownership.

    Keep repeated invalid requests from blocking subsequent profiling runs.

    """
    for _ in range(2):
        with pytest.raises(ValueError, match="GPU"):
            Session(backend="tachyon", gpu=True, notebooks=False, spark=False).start()


@pytest.mark.scalene
def test_real_device_sampling_exports_gpu_metrics_and_cleans_up(tmp_path):
    """Verify real device sampling exports gpu metrics and cleans up.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    result = run_probe(
        tmp_path,
        """
import json, time
from pathlib import Path
from linescope import Session
session = Session(
    backend="scalene", gpu=True, display="none", spark=False, notebooks=False,
    root=str(Path(__file__).parent),
)
with session:
    time.sleep(0.2)
measurements = [line.gpu for line in session.result.root_run.lines if line.gpu is not None]
print(json.dumps({
    "supported": session.result.capabilities.gpu,
    "time": [value.time_ns for value in measurements],
    "memory": [value.peak_memory_bytes for value in measurements],
    "cleaned": session._backend._accelerator is None,
    "warnings": session.result.warnings,
}))
""",
    )
    assert result["cleaned"]
    if not result["supported"]:
        pytest.skip("No supported GPU device on this runtime")
    assert result["time"]
    assert all(value is None or value >= 0 for value in result["time"])
    assert all(value is None or value >= 0 for value in result["memory"])
    assert not any("GPU sampling failed" in warning for warning in result["warnings"])
