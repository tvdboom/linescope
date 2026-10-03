"""LineScope.

Author: Mavs
Description: Backend-independent snapshots, measurements, and run relationships.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


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
    kind: Literal["python", "notebook"] = "python"


@dataclass(frozen=True)
class SymbolDefinition:
    """Identify a statically resolved project definition.

    See Also
    --------
    - linescope.model:SourceLocation
    - linescope.model:SourceUnit
    - linescope.model:SymbolRef
    """

    kind: Literal["function", "class", "method", "notebook"]
    qualified_name: str
    source_id: str
    line: int
    column: int | None = None


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
    executed_plan: str | None = None
    initial_plan: str | None = None
    optimized_plan: str | None = None
    parsed_plan: str | None = None
    analyzed_plan: str | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[int] = field(default_factory=list)
    status: str = "success"
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


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
    status: str = "success"
    metadata: dict[str, Any] = field(default_factory=dict)


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
    backend: str
    capabilities: BackendCapabilities
    symbols: list[SymbolDefinition] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
