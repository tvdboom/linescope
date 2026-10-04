"""LineScope.

Author: Mavs
Description: Profile CUDA signal projections with sampled GPU time and memory.

"""

from pathlib import Path
from time import perf_counter

import torch

from linescope import profile


def load_signals() -> tuple[torch.Tensor, torch.Tensor]:
    """Prepare reproducible signals and transfer them to the GPU.

    Generate input on the CPU so host preparation and device transfers appear
    separately from the repeated GPU calculations.

    """
    generator = torch.Generator().manual_seed(42)
    signals = torch.randn(2048, 1024, generator=generator)
    weights = torch.randn(1024, 1024, generator=generator) / 32
    return signals.to("cuda"), weights.to("cuda")


def project_signals(signals: torch.Tensor, weights: torch.Tensor) -> tuple[torch.Tensor, int]:
    """Project signals long enough for GPU sampling to observe repeated work.

    Synchronize each iteration to finish queued device work inside the
    profiling session. Driver waiting and sampled GPU time remain separate
    measurements, and the iteration count depends on the device.

    """
    deadline = perf_counter() + 3
    iterations = 0
    while True:
        projected = torch.relu(signals @ weights)
        torch.cuda.synchronize()
        iterations += 1
        if perf_counter() >= deadline:
            return projected, iterations


def main() -> None:
    """Run the CUDA workload and save its source profile.

    Require a CUDA-enabled PyTorch build and an available NVIDIA GPU before
    starting collection. Device memory collection does not require enabling
    Python-driver allocation sampling.

    """
    if not torch.cuda.is_available():
        raise SystemExit(
            "The GPU example requires an NVIDIA GPU, a compatible driver, and CUDA-enabled "
            "PyTorch. See docs_sources/examples/gpu.md for setup."
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
    for warning in session.result.warnings:
        print(f"Collection note: {warning}")


if __name__ == "__main__":
    main()
