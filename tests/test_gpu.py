"""LineScope.

Author: Mavs
Description: Check GPU metrics remain separate, optional and visible in reports.

"""

from types import SimpleNamespace

import pytest

from linescope import Session
from linescope.backends.scalene import ScaleneBackend, normalize_scalene
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
    assert "Estimated GPU time" in html
    assert "GPU peak memory" in html
    assert "4.0 KiB" in html
    assert "Offline report" not in html
    assert "snapshotted at collection" not in html
    assert "click underlined symbols" not in html
    assert "data:image/svg+xml;base64," in html


@pytest.mark.parametrize("name", ["trace", "tachyon"])
def test_unsupported_gpu_request_releases_session_lock(name):
    """Verify unsupported gpu request releases session lock.

    Use controlled accelerator data unless the case explicitly checks real
    supported device sampling.

    """
    for _ in range(2):
        with pytest.raises(ValueError, match="GPU"):
            Session(backend=name, gpu=True, notebooks=False, spark=False).start()


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
