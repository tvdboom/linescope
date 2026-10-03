"""LineScope.

Author: Mavs
Description: Collector contracts and backend registration.

"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from linescope.model import BackendCapabilities, MemoryStats


@dataclass
class RawLine:
    """Carry a measurement before source ownership and navigation are applied.

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


@dataclass
class RawBackendResult:
    """Return only collector measurements, independent of the report renderer.

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

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.backends.base:RawBackendResult
    - linescope.backends.base:register_backend
    """

    name: str
    capabilities: BackendCapabilities

    def start(self) -> None:
        """Begin collecting measurements."""
        ...

    def stop(self) -> None:
        """Stop collecting and release instrumentation."""
        ...

    def result(self) -> RawBackendResult:
        """Return a detached measurement snapshot."""
        ...


BackendFactory = Callable[..., ProfilerBackend]
_factories: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register a collector factory without changing the rendering pipeline.

    Parameters
    ----------
    name : str
        Unique name selected by the API or CLI.

    factory : callable
        Factory accepting `accepts`, `on_source`, `memory`, and `root` keywords.

    Raises
    ------
    ValueError
        If the name is empty or already registered.
    """
    if not name or name in _factories or name in ("trace", "scalene"):
        raise ValueError(f"Backend name is empty or already registered: {name!r}")
    _factories[name] = factory


def create_backend(name: str, **options: Any) -> ProfilerBackend:
    """Construct a backend lazily so optional packages are never required on import."""
    if name == "trace":
        from linescope.backends.trace import TraceBackend

        return TraceBackend(**options)
    if name == "scalene":
        from linescope.backends.scalene import ScaleneBackend

        return ScaleneBackend(**options)
    if name not in _factories:
        raise ValueError(
            f"Unknown backend {name!r}; available: trace, scalene, {', '.join(_factories)}"
        )
    return _factories[name](**options)
