"""LineScope.

Author: Mavs
Description: Profile CUDA signal projections and device metrics with Scalene.

"""

from pathlib import Path
from time import perf_counter

import torch

from linescope import profile


def load_signals() -> tuple[torch.Tensor, torch.Tensor]:
    """Prepare reproducible signals and transfer them to the GPU.

    Generate input on the CPU so host preparation and device transfers appear
    separately from the repeated GPU calculations.

    Returns
    -------
    tuple[torch.Tensor, torch.Tensor]
        Signal and projection-weight tensors stored on the CUDA device.

    """
    generator = torch.Generator().manual_seed(42)
    signals = torch.randn(2048, 1024, generator=generator)
    weights = torch.randn(1024, 1024, generator=generator) / 32
    return signals.to("cuda"), weights.to("cuda")


def project_signals(signals: torch.Tensor, weights: torch.Tensor) -> tuple[torch.Tensor, int]:
    """Project signals repeatedly to sample driver and device work.

    Synchronize each iteration to finish queued device work inside the
    profiling session. Scalene collects driver time and separate estimated
    GPU time and peak device memory. The iteration count depends on the device.

    Parameters
    ----------
    signals : torch.Tensor
        Input signals already transferred to the CUDA device.

    weights : torch.Tensor
        Projection weights stored on the same device as the signals.

    Returns
    -------
    tuple[torch.Tensor, int]
        Final projected signals and the number of completed projections.

    """
    deadline = perf_counter() + 3
    iterations = 0
    while True:
        projected = torch.relu(signals @ weights)
        torch.cuda.synchronize()
        iterations += 1
        if perf_counter() >= deadline:
            return projected, iterations


def main():
    """Run the CUDA workload and save its source profile.

    Require a CUDA-enabled PyTorch build and an available NVIDIA GPU before
    starting collection. Select Scalene explicitly and enable GPU collection
    to show device metrics separately from driver timing. Driver memory
    collection remains disabled.

    """
    if not torch.cuda.is_available():
        raise SystemExit(
            "The GPU example requires an NVIDIA GPU, a compatible driver, and CUDA-enabled "
            "PyTorch. See docs_sources/examples/notebooks/gpu_example.ipynb for setup."
        )

    # Initialize CUDA before profiling so one-time device startup is excluded.
    torch.cuda.init()
    with profile(
        backend="scalene",
        gpu=True,
        memory=False,
        root=str(Path(__file__).parent),
        spark=False,
        display="none",
    ) as session:
        signals, weights = load_signals()
        projected, iterations = project_signals(signals, weights)
        mean = projected.mean().item()

    report = session.save("gpu.html")
    print(f"GPU: {torch.cuda.get_device_name()}; projections: {iterations}; mean: {mean:.4f}")
    print(f"Report: {report}")
    gpu_times = [
        line.gpu.time_ns
        for line in session.result.root_run.lines
        if line.gpu is not None and line.gpu.time_ns is not None
    ]
    gpu_peaks = [
        line.gpu.peak_memory_bytes
        for line in session.result.root_run.lines
        if line.gpu is not None and line.gpu.peak_memory_bytes is not None
    ]
    gpu_time = f"{sum(gpu_times) / 1_000_000_000:.3f} s" if gpu_times else "unavailable"
    gpu_memory = f"{max(gpu_peaks) / 1024**2:.1f} MiB" if gpu_peaks else "unavailable"
    print(f"Attributed GPU time: {gpu_time}; GPU peak memory: {gpu_memory}")
    print("Select GPU in the report to compare device estimates with Python driver time.")
    for warning in session.result.warnings:
        print(f"Collection note: {warning}")


if __name__ == "__main__":
    main()
