# GPU
-----

GPU work is a separate measurement domain from Python-driver time and memory. A
Python line that launches device work can return before that work finishes, so
driver line timings alone do not describe GPU execution.

Run the [GPU notebook](../examples/notebooks/gpu_example.ipynb) to see a
complete CUDA workload with Scalene GPU collection and its output.

With `gpu=True`, select **GPU** in the sidebar to see **Attributed GPU time**
and **GPU peak memory** cards and compare device
estimates with **Driver time**, sort the measured lines, and follow a link to
the exact captured source. The source view also includes **GPU time**
and **GPU peak memory** columns.

When the GPU peak memory is unavailable, its widget shows a small red
notification below the value with the recorded collection reason. This
appears on the GPU view. If the collector records no reason,
the notification states that no GPU memory measurements were recorded.

Changing `gpu=True` to `gpu=False` disables these additional measurements and
the GPU view. The workload still runs on the GPU when its tensors are on CUDA;
PyTorch's device selection determines where calculations run.

!!! warning "Select Scalene for GPU measurements"
    Trace is the default backend and does not support GPU profiling. Set
    `backend="scalene"` together with `gpu=True`, or use
    `--backend scalene --gpu`. Enabling GPU collection with Trace emits a
    `RuntimeWarning` and records the diagnostic in `session.result.warnings`;
    Python profiling continues with GPU measurements unavailable. Tachyon
    rejects GPU requests.

## Collection support

The Python API accepts `gpu=True`, and the CLI and notebook cell magic accept
`--gpu`. These options require a backend that implements GPU collection and
declares `BackendCapabilities.gpu=True`.

Scalene collects GPU utilization and device memory alongside Python line time on
supported devices. Install `linescope[scalene]` or `linescope[full]` on Python
3.11–3.14, then enable collection explicitly:

```python
from linescope import profile

with profile(backend="scalene", gpu=True) as session:
    train_model()
```

```console
linescope --backend scalene --gpu train.py
```

Collection depends on Scalene's device support and runtime libraries. Missing
devices, unsupported runtimes, or failed samples leave GPU measurements
unavailable and produce diagnostic metadata. CPU collection can continue when
GPU initialization is unavailable.

The adapter uses Scalene's NVIDIA and AWS Neuron collectors. Apple MPS metrics
remain unavailable because Scalene's per-process MPS timing needs additional
PyTorch instrumentation. NVIDIA utilization can include other processes when
per-process accounting is unavailable; device-memory samples refer to the
profiled process.
NVIDIA WDDM drivers on Windows do not expose per-process device memory through
NVML. Those memory readings stay unavailable while GPU time sampling continues.

## Measurement model

[GPUStats](../api/model/memory/gpustats.md) represents estimated GPU time
and sampled
peak device memory for a source line. GPU time can overlap CPU work and must
stay separate from driver wall time. Device memory also stays separate from
[Python-driver memory](backends.md#memory) and
[Spark executor metrics](spark.md#plans-and-metrics). Unknown values remain
unavailable. The [report](reports.md#line-columns) displays estimated GPU time
and peak device memory alongside the driver measurements when supported.

A [custom backend](backends.md#custom-backends) can return GPU measurements
through `RawLine.gpu` and declare the corresponding capability. Collection
depends on that backend's supported devices, runtime libraries, and sampling
method. The model fields alone do not imply that a device was measured.
