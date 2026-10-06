"""LineScope.

Author: Mavs
Description: Derive detached per-cell measurements from normalized snapshots.

"""

from __future__ import annotations

from copy import deepcopy

from linescope.enums import RunStatus
from linescope.model import (
    GPUStats,
    LineStats,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
)


def _change(value: int | None, previous: int | None) -> int | None:
    """Subtract an earlier observation from an additive measurement.

    An absent earlier observation contributes no collected measurement. An
    unavailable current value remains unknown. Callers handle unavailable
    memory baselines separately because missing memory is not a zero reading.

    Parameters
    ----------
    value : int | None
        Current cumulative observation.

    previous : int | None
        Earlier observation, or None before any observation was collected.

    Returns
    -------
    int | None
        Collected change, or None when the current value is unavailable.

    """
    return None if value is None else value - (previous or 0)


def _line_change(line: LineStats, previous: LineStats | None) -> LineStats:
    """Extract additive cell costs without reusing cumulative peaks.

    Keep process RSS and memory peaks unknown: subtracting two maxima does
    not recover the cell's peak. Preserve signed allocation and RAM changes.

    Parameters
    ----------
    line : [LineStats]
        Current normalized line measurements.

    previous : [LineStats] | None
        Earlier observations for the same snapshot and one-based line.

    Returns
    -------
    [LineStats]
        Detached measurements collected between the two snapshots.

    """
    before = previous or LineStats(line.location)
    changed = LineStats(
        line.location,
        wall_time_ns=_change(line.wall_time_ns, before.wall_time_ns),
        hits=_change(line.hits, before.hits),
        samples=_change(line.samples, before.samples),
        spark_executions=[
            identity
            for identity in line.spark_executions
            if identity not in before.spark_executions
        ],
        notebook_runs=[
            identity for identity in line.notebook_runs if identity not in before.notebook_runs
        ],
    )
    # Timing and count collectors are cumulative; a reset cannot yield a
    # trustworthy cell delta. Signed memory changes have different semantics.
    for name in ("wall_time_ns", "hits", "samples"):
        value = getattr(changed, name)
        if value is not None and value < 0:
            setattr(changed, name, None)

    if line.memory is not None:
        changed.memory = MemoryStats(
            delta_bytes=(
                None
                if before.memory is not None and before.memory.delta_bytes is None
                else _change(
                    line.memory.delta_bytes,
                    before.memory.delta_bytes if before.memory else None,
                )
            )
        )

    if line.ram is not None:
        changed.ram = ProcessMemoryStats(
            delta_bytes=(
                None
                if before.ram is not None and before.ram.delta_bytes is None
                else _change(line.ram.delta_bytes, before.ram.delta_bytes if before.ram else None)
            )
        )

    if line.gpu is not None:
        duration = (
            None
            if before.gpu is not None and before.gpu.time_ns is None
            else _change(line.gpu.time_ns, before.gpu.time_ns if before.gpu else None)
        )
        changed.gpu = GPUStats(time_ns=duration if duration is None or duration >= 0 else None)

    return changed


def _has_activity(line: LineStats) -> bool:
    """Identify newly observed measurements or integration references.

    Ignore navigation-only lines and unchanged cumulative observations.

    Parameters
    ----------
    line : [LineStats]
        Detached cell measurements to inspect.

    Returns
    -------
    bool
        Whether the cell contributed a measurement or a new reference.

    """
    return any(
        (
            line.wall_time_ns,
            line.hits,
            line.samples,
            line.memory.delta_bytes if line.memory else None,
            line.ram.delta_bytes if line.ram else None,
            line.gpu.time_ns if line.gpu else None,
            line.spark_executions,
            line.notebook_runs,
        )
    )


def cell_result(
    current: ProfileResult,
    previous: ProfileRun,
    *,
    elapsed_ns: int,
    status: RunStatus,
) -> ProfileResult:
    """Build a cell result while retaining the cumulative session untouched.

    Include project functions defined in earlier cells or imported files when
    they received new measurements, including first observations with zero
    counts. Exclude unchanged earlier observations. Child runs and Spark actions
    remain separate from driver line time.

    Parameters
    ----------
    current : [ProfileResult]
        Latest normalized cumulative session result.

    previous : [ProfileRun]
        Detached observations from immediately before the cell executed.

    elapsed_ns : int
        Cell execution duration in monotonic nanoseconds, excluding summary
        rendering and time spent waiting between cells.

    status : [RunStatus]
        Cell outcome, without exception messages or notebook parameter values.

    Returns
    -------
    [ProfileResult]
        Detached cell measurements with immutable captured source snapshots.

    """
    baseline = {line.location: line for line in previous.lines}
    changes = [_line_change(line, baseline.get(line.location)) for line in current.root_run.lines]
    spark_ids = {execution.id for execution in previous.spark_executions}
    child_ids = {run.id for run in previous.children}
    run = ProfileRun(
        elapsed_ns=elapsed_ns,
        status=status,
        lines=[
            line
            for line in changes
            if _has_activity(line)
            or (
                line.location not in baseline
                and any(
                    value is not None for value in (line.wall_time_ns, line.hits, line.samples)
                )
            )
        ],
        spark_executions=[
            deepcopy(execution)
            for execution in current.root_run.spark_executions
            if execution.id not in spark_ids
        ],
        children=[
            deepcopy(child) for child in current.root_run.children if child.id not in child_ids
        ],
    )
    return ProfileResult(
        run,
        dict(current.sources),
        current.backend,
        current.capabilities,
        warnings=list(current.warnings),
    )
