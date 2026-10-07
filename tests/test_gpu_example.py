"""LineScope.

Author: Mavs
Description: Exercise the CUDA example without device libraries or GPU hardware.

"""

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from linescope import Session
from linescope.backends.base import RawBackendResult, RawLine, create_backend
from linescope.enums import Backend
from linescope.model import BackendCapabilities, GPUStats

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/gpu_example.py"


@pytest.fixture
def gpu_demo(monkeypatch, mocker):
    """Provide the GPU example and controlled device workload inputs.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    torch = ModuleType("torch")
    torch.Tensor = object
    torch.cuda = SimpleNamespace(
        is_available=mocker.Mock(return_value=True),
        init=mocker.Mock(),
        synchronize=mocker.Mock(),
        get_device_name=mocker.Mock(return_value="Test GPU"),
    )
    torch.Generator = mocker.Mock()
    generator = torch.Generator.return_value.manual_seed.return_value
    signals, weights = mocker.MagicMock(), mocker.MagicMock()
    torch.randn = mocker.Mock(side_effect=[signals, weights])
    projected = mocker.MagicMock()
    projected.mean.return_value.item.return_value = 0.25
    torch.relu = mocker.Mock(return_value=projected)
    monkeypatch.setitem(sys.modules, "torch", torch)
    demo = runpy.run_path(str(EXAMPLE))
    # Advance the loop clock without waiting three seconds in a unit test.
    demo["main"].__globals__["perf_counter"] = mocker.Mock(side_effect=[0, 1, 3])
    return demo, torch, generator, signals, weights


@pytest.fixture
def gpu_collector(monkeypatch, mocker):
    """Supply sampled device measurements without loading Scalene or CUDA.

    Attribute controlled driver and GPU costs to the example's projection line
    and expose lifecycle calls for success and failure checks.

    """
    line_number = next(
        number
        for number, line in enumerate(EXAMPLE.read_text(encoding="utf-8").splitlines(), 1)
        if "projected = torch.relu" in line
    )
    collector = SimpleNamespace(
        name=Backend.SCALENE,
        capabilities=BackendCapabilities(sampled=True, sample_counts=True, gpu=True),
        start=mocker.Mock(),
        stop=mocker.Mock(),
        result=mocker.Mock(
            return_value=RawBackendResult(
                [
                    RawLine(
                        str(EXAMPLE),
                        line_number,
                        wall_time_ns=1_000_000,
                        gpu=GPUStats(time_ns=500_000, peak_memory_bytes=2 * 1024**2),
                        samples=2,
                    )
                ]
            )
        ),
    )

    def create(name, **options):
        """Use controlled GPU sampling and real Trace for subsequent sessions.

        Keep the optional engine out of unit tests while exercising session
        ownership after the example finishes or raises.

        """
        return collector if name == Backend.SCALENE else create_backend(name, **options)

    factory = mocker.Mock(side_effect=create)
    monkeypatch.setattr("linescope.api.create_backend", factory)
    return collector, factory


def test_gpu_demo_profiles_transfers_and_finishes_device_work(
    gpu_demo,
    gpu_collector,
    monkeypatch,
    tmp_path,
    capsys,
):
    """Verify gpu demo profiles transfers and finishes device work.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    demo, torch, generator, signals, weights = gpu_demo
    collector, factory = gpu_collector
    sessions = []
    previous_trace = sys.gettrace()

    def collect(**options):
        """Provide the controlled behavior used by this test.

        Create a controlled session while checking forwarded profiling options.

        """
        assert options["backend"] == "scalene"
        assert options["gpu"] is True
        assert options["memory"] is False
        assert options["spark"] is False
        assert options["display"] == "none"
        assert torch.cuda.init.called
        session = Session(**options)
        sessions.append(session)
        return session

    demo["main"].__globals__["profile"] = collect
    monkeypatch.chdir(tmp_path)
    demo["main"]()

    torch.randn.assert_any_call(2048, 1024, generator=generator)
    torch.randn.assert_any_call(1024, 1024, generator=generator)
    signals.to.assert_called_once_with("cuda")
    (weights / 32).to.assert_called_once_with("cuda")
    assert torch.relu.call_count == torch.cuda.synchronize.call_count == 2
    collector.start.assert_called_once_with()
    collector.stop.assert_called_once_with()
    assert factory.call_args.args == (Backend.SCALENE,)
    assert factory.call_args.kwargs["gpu"] is True
    assert sys.gettrace() is previous_trace
    result = sessions[0].result
    assert result.backend is Backend.SCALENE
    assert result.capabilities.sample_counts
    assert not result.capabilities.hit_counts
    assert result.capabilities.gpu
    assert result.root_run.lines[0].gpu == GPUStats(500_000, 2 * 1024**2)
    assert all(line.hits is None for line in result.root_run.lines)
    report = (tmp_path / "gpu.html").read_text(encoding="utf-8")
    assert "gpu_example.py" in report
    assert "project_signals" in report
    assert "GPU time" in report
    assert "GPU peak memory" in report
    assert "</html>" in report
    output = capsys.readouterr().out
    assert "Test GPU" in output
    assert "Attributed GPU time:" in output
    assert "GPU peak memory: 2.0 MiB" in output
    assert "Select GPU in the report" in output


def test_gpu_demo_discloses_unavailable_device_memory(
    gpu_demo,
    gpu_collector,
    monkeypatch,
    tmp_path,
    capsys,
):
    """Show missing device memory without replacing it with zero.

    Keep the available GPU-time estimate visible in both printed output and
    the dedicated report view.

    """
    demo, _torch, *_ = gpu_demo
    collector, _factory = gpu_collector
    collector.result.return_value.lines[0].gpu.peak_memory_bytes = None
    monkeypatch.chdir(tmp_path)
    demo["main"]()
    output = capsys.readouterr().out
    assert "Attributed GPU time:" in output
    assert "GPU peak memory: unavailable" in output
    report = (tmp_path / "gpu.html").read_text(encoding="utf-8")
    assert "Sampled GPU memory is unavailable" not in report
    assert "<span>GPU peak memory</span><strong>—</strong>" in report


def test_gpu_demo_rejects_missing_cuda_before_profiling(gpu_demo, mocker):
    """Verify gpu demo rejects missing cuda before profiling.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    demo, torch, *_ = gpu_demo
    torch.cuda.is_available.return_value = False
    profile = mocker.Mock()
    demo["main"].__globals__["profile"] = profile
    with pytest.raises(SystemExit, match=r"NVIDIA GPU.*CUDA-enabled PyTorch"):
        demo["main"]()
    profile.assert_not_called()
    torch.cuda.init.assert_not_called()
    torch.randn.assert_not_called()


def test_gpu_demo_restores_collection_after_device_error(
    gpu_demo,
    gpu_collector,
    monkeypatch,
    tmp_path,
):
    """Verify gpu demo restores collection after device error.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    demo, torch, *_ = gpu_demo
    collector, _factory = gpu_collector
    sessions = []
    previous_trace = sys.gettrace()

    def collect(**options):
        """Provide the controlled behavior used by this test.

        Create a controlled session while checking forwarded profiling options.

        """
        assert options["backend"] == "scalene"
        assert options["gpu"] is True
        session = Session(**options)
        sessions.append(session)
        return session

    demo["main"].__globals__["profile"] = collect
    monkeypatch.chdir(tmp_path)
    torch.cuda.synchronize.side_effect = RuntimeError("Device execution failed")
    with pytest.raises(RuntimeError, match="Device execution failed"):
        demo["main"]()
    collector.stop.assert_called_once_with()
    assert sessions[0].state == "stopped"
    assert sys.gettrace() is previous_trace
    assert not (tmp_path / "gpu.html").exists()
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass
