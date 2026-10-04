"""LineScope.

Author: Mavs
Description: Backend-independent snapshots, measurements, and run relationships.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from linescope.enums import Backend, RunStatus, SourceKind, SymbolKind


@dataclass(frozen=True)
class BackendCapabilities:
    """Describe measurements a collector can actually provide.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:ProfileResult
    - linescope.model:SourceUnit

    """

    line_time: bool = True
    function_time: bool = True
    hit_counts: bool = False
    memory: bool = False
    scoped: bool = True
    attach: bool = False
    notebook: bool = True
    spark_driver: bool = True
    spark_executors: bool = False
    sampled: bool = False
    gpu: bool = False


@dataclass(frozen=True)
class SourceLocation:
    """Locate a one-based line and zero-based character column in a snapshot.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:SourceUnit
    - linescope.model:SymbolRef

    """

    source_id: str
    line: int
    column: int | None = None
    symbol: str | None = None


@dataclass(frozen=True)
class SourceUnit:
    """Preserve the full source text observed during collection.

    See Also
    --------
    - linescope.model:ProfileResult
    - linescope.model:SourceLocation
    - linescope.model:SymbolDefinition

    """

    id: str
    path: str
    source: str
    kind: SourceKind | str = SourceKind.PYTHON

    def __post_init__(self) -> None:
        """Normalize the source kind to its enum member.

        Accept string values when constructing source snapshots.

        """
        object.__setattr__(self, "kind", SourceKind(self.kind))


@dataclass(frozen=True)
class SymbolDefinition:
    """Identify a statically resolved project definition.

    See Also
    --------
    - linescope.model:SourceLocation
    - linescope.model:SourceUnit
    - linescope.model:SymbolRef

    """

    kind: SymbolKind | str
    qualified_name: str
    source_id: str
    line: int
    column: int | None = None

    def __post_init__(self) -> None:
        """Normalize the symbol kind to its enum member.

        Accept string values when constructing resolved definitions.

        """
        object.__setattr__(self, "kind", SymbolKind(self.kind))


@dataclass(frozen=True)
class SymbolRef:
    """Link one source token, rather than an entire line, to a definition.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:SourceLocation
    - linescope.model:SymbolDefinition

    """

    name: str
    line: int
    column: int
    end_column: int
    target: SourceLocation


@dataclass
class MemoryStats:
    """Describe Python driver memory in bytes; absent measurements stay unknown.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.model:LineStats
    - linescope.model:SparkExecutionStats

    """

    delta_bytes: int | None = None
    peak_bytes: int | None = None


@dataclass
class GPUStats:
    """Describe sampled GPU work separately from Python driver wall time.

    Parameters
    ----------
    time_ns : int | None, default=None
        GPU utilization integrated over sampled intervals, in nanoseconds.

    peak_memory_bytes : int | None, default=None
        Highest device-memory value observed for this source line.

    """

    time_ns: int | None = None
    peak_memory_bytes: int | None = None


@dataclass
class LineStats:
    """Aggregate a visible source line without inventing unsupported hit counts.

    See Also
    --------
    - linescope.model:FunctionStats
    - linescope.model:MemoryStats
    - linescope.model:SourceLocation

    """

    location: SourceLocation
    wall_time_ns: int | None = None
    hits: int | None = None
    memory: MemoryStats | None = None
    calls: list[SymbolRef] = field(default_factory=list)
    spark_executions: list[str] = field(default_factory=list)
    notebook_runs: list[str] = field(default_factory=list)
    gpu: GPUStats | None = None


@dataclass
class FunctionStats:
    """Index function costs derived from visible line measurements.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:ProfileRun
    - linescope.model:SymbolDefinition

    """

    source_id: str
    qualified_name: str
    first_line: int
    total_time_ns: int | None = None
    calls: int | None = None


@dataclass
class SparkExecutionStats:
    """Keep wall duration separate from cumulative executor work.

    See Also
    --------
    - linescope.model:MemoryStats
    - linescope.model:SparkExecution
    - linescope.model:SparkOperator

    """

    wall_time_ns: int | None = None
    executor_time_ns: int | None = None
    rows: int | None = None
    bytes_read: int | None = None
    shuffle_read_bytes: int | None = None
    shuffle_write_bytes: int | None = None
    peak_memory_bytes: int | None = None
    spill_bytes: int | None = None


@dataclass
class SparkOperator:
    """Represent a physical operator and the metrics Spark makes available.

    See Also
    --------
    - linescope.model:SourceLocation
    - linescope.model:SparkExecution
    - linescope.model:SparkExecutionStats

    """

    id: str
    name: str
    description: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    children: list[SparkOperator] = field(default_factory=list)
    locations: list[SourceLocation] = field(default_factory=list)


@dataclass
class SparkExecution:
    """Snapshot one observed action and its available executed query plans.

    See Also
    --------
    - linescope.model:ProfileRun
    - linescope.model:SparkExecutionStats
    - linescope.model:SparkOperator

    """

    id: str
    name: str = "Spark action"
    location: SourceLocation | None = None

    stats: SparkExecutionStats = field(default_factory=SparkExecutionStats)
    operators: list[SparkOperator] = field(default_factory=list)

    # Keep observed query plans beside their action, even when JVM access
    # cannot provide every preparation stage or a final AQE plan.
    executed_plan: str | None = None
    initial_plan: str | None = None
    optimized_plan: str | None = None
    parsed_plan: str | None = None
    analyzed_plan: str | None = None

    stages: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[int] = field(default_factory=list)
    status: RunStatus | str = RunStatus.SUCCESS
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize the execution outcome to its enum member.

        Accept string values when constructing observed Spark actions.

        """
        self.status = RunStatus(self.status)


@dataclass
class ProfileRun:
    """Represent a profiling scope and any separately executed child notebooks.

    See Also
    --------
    - linescope.model:LineStats
    - linescope.model:ProfileResult
    - linescope.model:SparkExecution

    """

    id: str = field(default_factory=lambda: uuid4().hex)
    parent_id: str | None = None
    source: SourceUnit | None = None

    elapsed_ns: int = 0
    lines: list[LineStats] = field(default_factory=list)
    functions: list[FunctionStats] = field(default_factory=list)
    spark_executions: list[SparkExecution] = field(default_factory=list)

    children: list[ProfileRun] = field(default_factory=list)
    name: str = "Profile"
    status: RunStatus | str = RunStatus.SUCCESS
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize the run outcome to its enum member.

        Accept string values when constructing profiling scopes.

        """
        self.status = RunStatus(self.status)


@dataclass
class ProfileResult:
    """Combine portable source snapshots with backend-independent run data.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.model:ProfileRun
    - linescope.model:SourceUnit

    """

    root_run: ProfileRun
    sources: dict[str, SourceUnit]
    backend: Backend | str
    capabilities: BackendCapabilities
    symbols: list[SymbolDefinition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
