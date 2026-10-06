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
from linescope.enums import Backend

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


def test_gpu_demo_profiles_transfers_and_finishes_device_work(
    gpu_demo,
    monkeypatch,
    tmp_path,
    capsys,
):
    """Verify gpu demo profiles transfers and finishes device work.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    demo, torch, generator, signals, weights = gpu_demo
    sessions = []
    previous_trace = sys.gettrace()

    def collect(**options):
        """Provide the controlled behavior used by this test.

        Create a controlled session while checking forwarded profiling options.

        """
        assert "backend" not in options
        assert "gpu" not in options
        assert options["memory"] is False
        assert options["spark"] is False
        assert options["display"] == "none"
        assert torch.cuda.init.called
        session = Session(backend="trace", **options)
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
    assert not sessions[0]._backend._running
    assert sys.gettrace() is previous_trace
    result = sessions[0].result
    assert result.backend is Backend.TRACE
    assert result.capabilities.hit_counts
    assert not result.capabilities.gpu
    assert all(line.gpu is None for line in result.root_run.lines)
    assert any(line.hits for line in result.root_run.lines)
    report = (tmp_path / "gpu.html").read_text(encoding="utf-8")
    assert "gpu_example.py" in report
    assert "project_signals" in report
    assert "</html>" in report
    output = capsys.readouterr().out
    assert "Test GPU" in output


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


def test_gpu_demo_restores_collection_after_device_error(gpu_demo, monkeypatch, tmp_path):
    """Verify gpu demo restores collection after device error.

    Use controlled device operations and collector results to inspect transfers,
    completion, and error cleanup.

    """
    demo, torch, *_ = gpu_demo
    sessions = []
    previous_trace = sys.gettrace()

    def collect(**options):
        """Provide the controlled behavior used by this test.

        Create a controlled session while checking forwarded profiling options.

        """
        assert "backend" not in options
        assert "gpu" not in options
        session = Session(backend="trace", **options)
        sessions.append(session)
        return session

    demo["main"].__globals__["profile"] = collect
    monkeypatch.chdir(tmp_path)
    torch.cuda.synchronize.side_effect = RuntimeError("Device execution failed")
    with pytest.raises(RuntimeError, match="Device execution failed"):
        demo["main"]()
    assert not sessions[0]._backend._running
    assert sys.gettrace() is previous_trace
    assert not (tmp_path / "gpu.html").exists()
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass
