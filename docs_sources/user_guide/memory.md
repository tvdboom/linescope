# Memory

Request optional memory collection with `profile(memory=True)` or `--memory`. Scalene exposes
allocation metrics that LineScope normalizes into simple delta/peak fields when available.
Capabilities and missing values remain explicit; not every backend/platform exposes the same
memory information.

On Unix, native allocator interception must be loaded when Python starts. The CLI memory mode
prepares that environment for its target process. For an existing notebook kernel or a direct
API session, prepare the native environment before launching Python; enabling memory after the
kernel has started cannot retroactively preload its allocator. Unsupported initialization fails
explicitly instead of producing invented memory values.

Scalene 2.3 also provides a native Windows memory collector, initialized by the adapter. Native
allocation metrics are sampled and may not equal the exact size of every short-lived allocation.
The native collector requires a supported Scalene binary wheel. Prefer Python 3.12 or newer for
Windows memory profiling: a Python 3.11 source build can provide CPU sampling without shipping
the required memory DLL, in which case the adapter reports the missing capability explicitly.

```python
from linescope import profile

with profile(backend="scalene", memory=True, display="none") as session:
    values = [bytearray(1024) for _ in range(10_000)]

session.save("memory.html")
```

## Driver and executor memory

Python memory columns concern the process being profiled. In Spark that is normally the driver.
A lazy DataFrame can describe a large distributed dataset while occupying little Python memory.
Conversely, `toPandas()` may move substantial data into the driver.

Spark operator peak memory and spill, when exposed by Spark, appear with the Spark execution.
They are separate measurements with different semantics. Do not add them to the driver's Python
memory or interpret distributed operator peaks as one process-wide peak.
