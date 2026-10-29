# GPU

Install `linescope[scalene]` on Python 3.11–3.14, then enable GPU collection:

```python
from linescope import profile

with profile(backend="scalene", gpu=True):
    train_model()
```

The CLI equivalent is `linescope --backend scalene --gpu train.py`.
Cell magic accepts the same flags. See the
[GPU notebook](../examples/notebooks/gpu_example.ipynb) for a CUDA workload.

Synchronize queued device work inside the session to include it in the
measurement period. `gpu=True` collects metrics; it does not select a device
for your calculations.

## Collection support

Scalene supports NVIDIA and AWS Neuron metrics. Apple MPS metrics are
unavailable. Trace warns and continues without GPU data; Tachyon rejects GPU
requests.

NVIDIA utilization may include other processes when per-process accounting is
unavailable. Device memory refers to the profiled process, but Windows WDDM
drivers do not expose it through NVML. GPU time can still be collected.

## Read the GPU view

The **GPU** view and source tables show estimated device time and sampled peak
memory beside driver time. Collection failures appear as diagnostics.

GPU and CPU work can overlap. Do not add GPU time to driver wall time. Device
memory is separate from [process RAM](backends.md#memory) and
[Spark metrics](spark.md#plans-and-metrics). Sampled peaks can miss brief
spikes.

## Measurement model

[GPUStats](../api/model/memory/gpustats.md) stores line-level device
measurements.
A [custom backend](backends.md#custom-backends) supplies them through
`RawLine.gpu` with `BackendCapabilities.gpu=True`.
