# Profile a GPU workload

The runnable example in `examples/gpu_example.py` prepares synthetic signals on
the CPU, transfers them to an NVIDIA GPU, and repeatedly applies a matrix
projection and ReLU activation for at least three seconds. It uses Scalene with
`gpu=True` on Python 3.11–3.14 and saves `gpu.html`.

## Run the example

Use Windows or Linux with an NVIDIA GPU and a driver compatible with CUDA 12.8.
The optional `gpu` dependency group installs CUDA-enabled PyTorch from its
official CUDA 12.8 index. PyTorch is only needed for this example; it is not a
LineScope runtime dependency. Consult the [PyTorch installation
guide](https://pytorch.org/get-started/locally/) for supported hardware and
driver setup, and the [uv PyTorch
guide](https://docs.astral.sh/uv/guides/integration/pytorch/) for other builds.

```console
uv run --locked --group gpu python examples/gpu_example.py
```

From a repository checkout with Just installed, `just demo-gpu` runs the same
script and opens `gpu.html` in your browser. Run `just sync` first to install
all extras and dependency groups, including PyTorch and its device libraries.
The script exits with a setup message before profiling when CUDA is unavailable.

## Read the report

:: example: gpu_example.py

Follow `load_signals` to compare CPU preparation and device transfers, then
`project_signals` to inspect the GPU work. CUDA initialization happens before
collection. Each projection synchronizes before continuing, so its device work
finishes inside the session. The synchronization line can show driver waiting;
estimated GPU time remains a separate metric and can overlap driver time.

GPU time and peak device memory depend on Scalene's device support and available
samples. Unknown values remain unavailable. The script prints collection notes
if Scalene cannot sample the device, even when PyTorch can execute CUDA work.
Python-driver memory collection is disabled; GPU memory sampling works
independently of it. See the [GPU guide](../user_guide/gpu.md) for measurement
limitations and the [profiling scope](../user_guide/backends.md#profiling-scope)
for thread and worker coverage.
