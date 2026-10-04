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

    `sample_counts` identifies collectors that count observed project thread
    frames, independently of support for execution `hit_counts`.

    Attributes
    ----------
    line_time : bool
        Whether the collector provides measurements for individual lines.

    function_time : bool
        Whether visible line costs can be indexed by function.

    hit_counts : bool
        Whether exact line executions and function calls are available.

    memory : bool
        Whether Python driver memory measurements are available.

    scoped : bool
        Whether collection can be restricted to a profiling scope.

    attach : bool
        Whether the collector can attach to an existing process.

    notebook : bool
        Whether captured notebook source can receive measurements.

    spark_driver : bool
        Whether driver lines can be correlated with Spark actions.

    spark_executors : bool
        Whether executor-side Python measurements are supported.

    sampled : bool
        Whether times are sampling estimates rather than traced intervals.

    gpu : bool
        Whether GPU utilization and device memory are available.

    sample_counts : bool
        Whether per-line sampling observations are available independently of
        hits.

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
    sample_counts: bool = False


@dataclass(frozen=True)
class SourceLocation:
    """Locate a one-based line and zero-based character column in a snapshot.

    Attributes
    ----------
    source_id : str
        Identifier of the snapshot containing this location.

    line : int
        One-based source line number.

    column : int | None
        Zero-based character offset, or None when unavailable.

    symbol : str | None
        Resolved symbol label, or None when unavailable.

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

    Attributes
    ----------
    id : str
        Stable identifier used by measurements and navigation links.

    path : str
        Original file path or notebook cell label shown in the report.

    source : str
        Complete source text frozen when the snapshot is captured.

    kind : [SourceKind] | str
        Source category normalized to [SourceKind] at construction.

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

    Attributes
    ----------
    kind : [SymbolKind] | str
        Definition category normalized to [SymbolKind] at construction.

    qualified_name : str
        Lexical name including enclosing classes and functions.

    source_id : str
        Identifier of the snapshot containing the definition.

    line : int
        One-based line where the definition begins.

    column : int | None
        Zero-based character offset, or None when unavailable.

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

    Attributes
    ----------
    name : str
        Original spelling of the linked source token.

    line : int
        One-based line containing the reference.

    column : int
        Inclusive zero-based character offset where the token starts.

    end_column : int
        Exclusive zero-based character offset where the token ends.

    target : [SourceLocation]
        Conservatively resolved destination in a captured snapshot.

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
    """Describe allocation measurements in bytes, separate from process RAM.

    The shared collector measures net retained Python allocations by allocation
    site. It leaves allocation peaks unavailable; process RSS belongs to
    [ProcessMemoryStats]. Custom collectors can supply their own allocation
    measurements, keeping missing values unknown.

    Attributes
    ----------
    delta_bytes : int | None
        Signed allocation change in bytes; None means unavailable.

    peak_bytes : int | None
        Highest measured allocated bytes; None means unavailable.

    See Also
    --------
    - linescope.model:BackendCapabilities
    - linescope.model:LineStats
    - linescope.model:SparkExecutionStats

    """

    delta_bytes: int | None = None
    peak_bytes: int | None = None


@dataclass
class ProcessMemoryStats:
    """Describe observed process RAM during a source line's execution.

    RSS includes native libraries, shared pages, and profiler overhead. Other
    threads can contribute to changes. Repeated executions retain the last
    boundary reading, sum observed changes, and keep the highest peak.

    Parameters
    ----------
    rss_bytes : int | None, default=None
        Process RAM after the latest completed execution interval.

    delta_bytes : int | None, default=None
        Sum of process RAM changes during this line's intervals.

    peak_bytes : int | None, default=None
        Highest process RAM observed during this line's intervals.

    Attributes
    ----------
    rss_bytes : int | None
        Last boundary RAM reading in bytes; None means unavailable.

    delta_bytes : int | None
        Accumulated signed RAM change in bytes; None means unavailable.

    peak_bytes : int | None
        Maximum observed process RAM in bytes; None means unavailable.

    """

    rss_bytes: int | None = None
    delta_bytes: int | None = None
    peak_bytes: int | None = None


@dataclass(frozen=True)
class MemorySample:
    """Preserve a RAM reading and its position in one process's timeline.

    Parameters
    ----------
    elapsed_ns : int
        Time since memory collection began, in nanoseconds.

    rss_bytes : int | None
        Resident process memory; None identifies an unavailable reading.

    location : [SourceLocation] | None, default=None
        Project line at the observation, or None for a baseline.

    Attributes
    ----------
    elapsed_ns : int
        Monotonic elapsed observation time in nanoseconds.

    rss_bytes : int | None
        Observed resident memory in bytes; None marks a gap.

    location : [SourceLocation] | None
        Source navigation target; None marks an unlinked observation.

    """

    elapsed_ns: int
    rss_bytes: int | None
    location: SourceLocation | None = None


@dataclass
class GPUStats:
    """Describe sampled GPU work separately from Python driver wall time.

    Parameters
    ----------
    time_ns : int | None, default=None
        GPU utilization integrated over sampled intervals, in nanoseconds.

    peak_memory_bytes : int | None, default=None
        Highest device-memory value observed for this source line.

    Attributes
    ----------
    time_ns : int | None
        GPU utilization integrated over sampled intervals, in nanoseconds.

    peak_memory_bytes : int | None
        Highest observed device memory in bytes, or None if unavailable.

    """

    time_ns: int | None = None
    peak_memory_bytes: int | None = None


@dataclass
class LineStats:
    """Aggregate a visible source line without inventing unsupported hit counts.

    `samples` counts observed project thread frames, separately from line
    execution `hits`. None means that sample counts are unavailable.

    Attributes
    ----------
    location : [SourceLocation]
        Snapshot location of the measured or referenced source line.

    wall_time_ns : int | None
        Measured driver wall time in nanoseconds, or None if unavailable.

    hits : int | None
        Exact execution count, or None for unsupported or sampled counts.

    memory : [MemoryStats] | None
        Allocation measurements, separate from process RAM, or None when
        unavailable.

    calls : list[[SymbolRef]]
        Individually resolved symbol links on this source line.

    spark_executions : list[str]
        Identifiers of Spark actions correlated with this line.

    notebook_runs : list[str]
        Identifiers of child notebook invocations linked from this line.

    gpu : [GPUStats] | None
        GPU measurements kept separate from driver wall time.

    samples : int | None
        Collected sampling observations, or None when unsupported; never exact
        hits.

    ram : ProcessMemoryStats | None
        Observed process RAM during this line, separate from allocation deltas.

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
    samples: int | None = None
    ram: ProcessMemoryStats | None = None


@dataclass
class FunctionStats:
    """Index function costs derived from visible line measurements.

    `samples` sums observations of the function's own lines, excluding nested
    definitions. Sample counts never imply function execution counts.

    Attributes
    ----------
    source_id : str
        Identifier of the snapshot containing the function.

    qualified_name : str
        Lexical function or method name, including enclosing scopes.

    first_line : int
        One-based line where the function definition begins.

    total_time_ns : int | None
        Sum of known visible line costs, excluding nested definitions.

    calls : int | None
        Exact invocation count, or None when unsupported by the collector.

    samples : int | None
        Collected sampling observations, or None when unsupported; never exact
        hits.

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
    samples: int | None = None


@dataclass
class SparkExecutionStats:
    """Keep wall duration separate from cumulative executor work.

    Attributes
    ----------
    wall_time_ns : int | None
        Driver-observed action duration in nanoseconds, if available.

    executor_time_ns : int | None
        Cumulative executor task time, separate from driver duration.

    rows : int | None
        Reported row count, or None when unavailable.

    bytes_read : int | None
        Reported input bytes, or None when unavailable.

    shuffle_read_bytes : int | None
        Reported shuffle bytes read, or None when unavailable.

    shuffle_write_bytes : int | None
        Reported shuffle bytes written, or None when unavailable.

    peak_memory_bytes : int | None
        Reported peak executor memory, or None when unavailable.

    spill_bytes : int | None
        Reported spilled bytes, or None when unavailable.

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

    Attributes
    ----------
    id : str
        Identifier of the physical plan node.

    name : str
        Spark's operator name for this node.

    description : str
        Captured plan description for inspecting the operation.

    metrics : dict[str, Any]
        Available raw metrics, retaining names, values, and unit metadata.

    children : list[[SparkOperator]]
        Child nodes in the observed physical plan.

    locations : list[[SourceLocation]]
        Project source locations correlated with this operation.

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

    Attributes
    ----------
    id : str
        Unique identifier used by action views and source references.

    name : str
        Observed Spark action or query name.

    location : [SourceLocation] | None
        Exact project line that triggered the action, when available.

    stats : [SparkExecutionStats]
        Available action duration and cumulative executor measurements.

    operators : list[[SparkOperator]]
        Observed physical operator trees, preferring the final AQE plan.

    executed_plan : str | None
        Executed physical plan text, or None when unavailable.

    initial_plan : str | None
        Initial physical plan text before AQE, when exposed by Spark.

    optimized_plan : str | None
        Optimized logical plan text, or None when unavailable.

    parsed_plan : str | None
        Parsed logical plan text, or None when unavailable.

    analyzed_plan : str | None
        Analyzed logical plan text, or None when unavailable.

    stages : list[dict[str, Any]]
        Available stage summaries and task metric distributions.

    jobs : list[int]
        Spark job identifiers associated with this action.

    status : [RunStatus] | str
        Action outcome normalized to [RunStatus] at construction.

    warnings : list[str]
        Diagnostics explaining unavailable or partial action metadata.

    metadata : dict[str, Any]
        Collection context and plan availability diagnostics.

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

    Attributes
    ----------
    id : str
        Unique run identifier, generated when not supplied.

    parent_id : str | None
        Owning run identifier, or None for a root run.

    source : [SourceUnit] | None
        Primary notebook snapshot, when the run represents a notebook.

    elapsed_ns : int
        Elapsed driver duration of the profiling scope, in nanoseconds.

    lines : list[[LineStats]]
        Normalized visible source line measurements and references.

    functions : list[[FunctionStats]]
        Function costs indexed from this run's visible measurements.

    spark_executions : list[[SparkExecution]]
        Spark actions observed within this profiling scope.

    children : list[[ProfileRun]]
        Separate child notebook runs and inline notebook references.

    name : str
        Display label for this profiling scope.

    status : [RunStatus] | str
        Run outcome or reference role normalized at construction.

    metadata : dict[str, Any]
        Run context, correlation identifiers, and collection diagnostics.

    memory_samples : list[MemorySample]
        Process RAM observations ordered by elapsed collection time.

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
    memory_samples: list[MemorySample] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize the run outcome to its enum member.

        Accept string values when constructing profiling scopes.

        """
        self.status = RunStatus(self.status)


@dataclass
class ProfileResult:
    """Combine portable source snapshots with backend-independent run data.

    Attributes
    ----------
    root_run : [ProfileRun]
        Root profiling scope and its child run tree.

    sources : dict[str, [SourceUnit]]
        Complete source snapshots keyed by stable identifiers.

    backend : [Backend] | str
        Built-in collector name or registered custom backend name.

    capabilities : [BackendCapabilities]
        Measurements the selected collector actually supports.

    symbols : list[[SymbolDefinition]]
        Conservatively resolved definitions across captured source.

    warnings : list[str]
        Collector and integration limitations recorded during profiling.

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
