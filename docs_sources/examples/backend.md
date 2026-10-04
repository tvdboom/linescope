# Custom backend

The backend protocol is intentionally small. A collector starts and stops,
returns raw measurements, and declares supported capabilities. The rest of
LineScope consumes normalized results and remains independent of that
collector's implementation.

```pycon
from linescope.model import BackendCapabilities
capabilities = BackendCapabilities(hit_counts=False, memory=False, sampled=True)
capabilities.sampled
capabilities.hit_counts
```

A sampling implementation should leave `hits=None`; receiving ten samples is not
proof that a line executed ten times. A function-only collector should declare
`line_time=False` rather than manufacture line timings. An external attach
backend should declare whether scoped collection and notebook use are supported.

Use the built-in trace collector and Scalene adapter as concrete references for
the collection and normalization contract. Registration is process-local; it
does not change the installed package or project defaults.

For a minimal runnable registration example, subclass the Scalene collector.
Its constructor already accepts the backend factory keywords, and its
measurements keep their original semantics.

This demo enables driver memory collection and requires Python 3.11–3.14.
On Linux and macOS, prepare the [memory allocator
environment](../user_guide/backends.md#memory) before starting Python.

```console
just demo-custom-backend
uv run python examples/custom_backend_example.py
```

The Just recipe also opens the saved `custom-backend.html` report.

:: example: custom_backend_example.py

A new measurement engine can instead implement the `ProfilerBackend` protocol
and return `RawBackendResult` records. Its factory accepts `accepts`,
`on_source`, `memory`, and `root`; call the supplied source callback when
observing a file so the snapshot matches the run.
