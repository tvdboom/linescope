"""LineScope.

Author: Mavs
Description: Collector contracts and backend registration.

"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from linescope.enums import Backend
from linescope.model import BackendCapabilities, GPUStats, MemoryStats


@dataclass
class RawLine:
    """Carry a measurement before source ownership and navigation are applied.

    `samples` counts observed project thread frames. Keep it None when the
    collector cannot supply counts; samples are independent of `hits`.

    Attributes
    ----------
    filename : str
        Runtime filename awaiting project ownership and snapshot resolution.

    line : int
        One-based source line reported by the collector.

    wall_time_ns : int | None
        Collected driver line time, or None when unavailable.

    hits : int | None
        Exact line execution count, or None when unsupported.

    memory : [MemoryStats] | None
        Collected allocation measurements, or None when unavailable.

    gpu : [GPUStats] | None
        Collected GPU work, or None when unavailable.

    samples : int | None
        Collected sampling observations, or None when unsupported; never exact
        hits.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:MemoryStats
    - linescope.backends.base:RawBackendResult

    """

    filename: str
    line: int
    wall_time_ns: int | None = None
    hits: int | None = None
    memory: MemoryStats | None = None
    gpu: GPUStats | None = None
    samples: int | None = None


@dataclass
class RawBackendResult:
    """Return only collector measurements, independent of the report renderer.

    Attributes
    ----------
    lines : list[[RawLine]]
        Collector measurements before source attribution and navigation.

    warnings : list[str]
        Honest diagnostics about collection limits or failures.

    function_calls : dict[tuple[str, str, int], int]
        Exact call counts keyed by filename, qualified name, and first line.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.backends.base:ProfilerBackend
    - linescope.backends.base:RawLine

    """

    lines: list[RawLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    function_calls: dict[tuple[str, str, int], int] = field(default_factory=dict)


class ProfilerBackend(Protocol):
    """Define the minimal pluggable measurement lifecycle.

    Attributes
    ----------
    name : str | [Backend]
        Built-in backend identifier or registered custom collector name.

    capabilities : [BackendCapabilities]
        Measurements supported by this collector.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.backends.base:RawBackendResult
    - linescope.backends.base:register_backend

    """

    name: str | Backend
    capabilities: BackendCapabilities

    def start(self) -> None:
        """Begin collecting measurements.

        Install only the instrumentation needed by this collector.

        """
        ...

    def stop(self) -> None:
        """Stop collecting and release instrumentation.

        Restore hooks owned by this collector, including after workload
        failures.

        """
        ...

    def result(self) -> RawBackendResult:
        """Return a detached measurement snapshot.

        Return normalized raw measurements for source attribution by the
        session.

        """
        ...


BackendFactory = Callable[..., ProfilerBackend]
_factories: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register a collector factory without changing the rendering pipeline.

    Raise `ValueError` if the name is empty or already registered.

    Parameters
    ----------
    name : str
        Unique name selected by the API or CLI.

    factory : BackendFactory
        Factory accepting `accepts`, `on_source`, `memory`, and `root`
        keywords. GPU requests additionally pass `gpu=True` to compatible
        collectors.

    """
    if not name or name in _factories or name in tuple(Backend):
        raise ValueError(f"Backend name is empty or already registered: {name!r}")

    _factories[name] = factory


def create_backend(name: str | Backend, **options: Any) -> ProfilerBackend:
    """Construct a backend lazily.

    Import optional measurement packages only when their backend is selected.

    Raise `ValueError` if the name is neither a built-in backend nor a
    registered factory.

    Parameters
    ----------
    name : str | [Backend]
        Built-in backend member or its string value. Registered custom
        backend names are accepted as strings.

    **options
        Keyword arguments passed to the selected collector factory.

    Returns
    -------
    [ProfilerBackend]
        Collector ready to start measuring.

    """
    if name in _factories:
        return _factories[name](**options)

    try:
        backend = Backend(name)
    except ValueError as error:
        available = ", ".join([*Backend, *_factories])
        raise ValueError(f"Unknown backend {name!r}; available: {available}") from error

    if backend is Backend.TRACE:
        from linescope.backends.trace import TraceBackend

        return TraceBackend(**options)

    if backend is Backend.SCALENE:
        from linescope.backends.scalene import ScaleneBackend

        return ScaleneBackend(**options)

    from linescope.backends.tachyon import TachyonBackend

    return TachyonBackend(**options)
