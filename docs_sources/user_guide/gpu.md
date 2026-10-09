# GPU
-----

Use Scalene to collect GPU measurements alongside Python line time. Install
`linescope[scalene]` or `linescope[full]` on Python 3.11–3.14, then enable
collection:

```python
from linescope import profile

with profile(backend="scalene", gpu=True) as session:
    train_model()
```

For scripts, use `linescope --backend scalene --gpu train.py`. Notebook cell
magic also accepts `--backend scalene --gpu`. See the
[GPU notebook](../examples/notebooks/gpu_example.ipynb) for a CUDA example.

Python can launch device work and return before it finishes. Synchronize queued
work within the session when you need it included in the observation period.
`gpu=True` enables measurement; your workload's device selection still
determines where calculations run.

!!! warning "Select Scalene for GPU measurements"
    Trace is the default and cannot collect GPU metrics. Requesting `gpu=True`
    with Trace emits a `RuntimeWarning`, records it in
    `session.result.warnings`, and continues Python profiling. Tachyon rejects
    GPU requests.

## Collection support

Scalene's NVIDIA and AWS Neuron collectors supply device measurements. Apple
MPS metrics are unavailable through the adapter. Missing devices, unsupported
runtimes, or failed samples leave measurements unavailable and record
collection diagnostics.

NVIDIA utilization can include other processes when per-process accounting is
unavailable. Device-memory readings refer to the profiled process, but NVIDIA
WDDM drivers on Windows do not expose them through NVML. GPU time sampling can
continue when memory readings are unavailable.

## Read the GPU view

Select **GPU** to compare attributed GPU time and sampled memory peaks with
**Driver time**, then follow a source link to the measured line. Source tables
also include **GPU time** and **GPU peak memory**. When memory is unavailable,
the GPU view shows the recorded collection reason if one is available.

GPU time can overlap CPU work, so do not add it to driver wall time. Device
memory is separate from [Python process RAM](backends.md#memory) and
[Spark executor metrics](spark.md#plans-and-metrics). Peaks are sampled and can
miss short spikes.

## Measurement model

[GPUStats](../api/model/memory/gpustats.md) stores estimated GPU time and
sampled peak device memory for a source line. A
[custom backend](backends.md#custom-backends) can supply these through
`RawLine.gpu` and declare
`BackendCapabilities.gpu=True`.
