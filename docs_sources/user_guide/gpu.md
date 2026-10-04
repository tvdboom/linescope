# GPU
-----

GPU work is a separate measurement domain from Python-driver time and memory. A
Python line that launches device work can return before that work finishes, so
driver line timings alone do not describe GPU execution.

## Collection support

The Python API accepts `gpu=True`, and the CLI and notebook cell magic accept
`--gpu`. These options require a backend that implements GPU collection and
declares `BackendCapabilities.gpu=True`.

Scalene collects GPU utilization and device memory alongside Python line time on
supported devices. It is included with LineScope on Python 3.11–3.14. Enable
collection explicitly:

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
GPU initialization is unavailable. Trace and Tachyon reject GPU requests.

The adapter uses Scalene's NVIDIA and AWS Neuron collectors. Apple MPS metrics
remain unavailable because Scalene's per-process MPS timing needs additional
PyTorch instrumentation. NVIDIA utilization can include other processes when
per-process accounting is unavailable; device-memory samples refer to the
profiled process.

## Measurement model

[GPUStats](../api/model/gpustats.md) represents estimated GPU time and sampled
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
