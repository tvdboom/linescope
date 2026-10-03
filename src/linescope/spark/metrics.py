"""Read Spark metrics without triggering a distributed action."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def scala_items(value: Any) -> list[Any]:
    """Convert a Scala collection into a Python list.

    Parameters
    ----------
    value : object
        Scala iterable, Java iterable, or a Python test double.

    Returns
    -------
    list
        Available collection members. Unavailable collections are empty.

    Examples
    --------
    ```pycon
    >>> from linescope.spark.metrics import scala_items
    >>> scala_items([1, 2])
    [1, 2]
    >>> scala_items(None)
    []
    ```
    """
    if value is None:
        return []
    if isinstance(value, Iterable):
        return list(value)
    try:
        iterator = value.iterator()
        output = []
        while iterator.hasNext():
            output.append(iterator.next())
        return output
    except Exception:
        return []


def optional_call(value: Any, name: str, default: Any = None) -> Any:
    """Read an optional JVM property or zero-argument method.

    Parameters
    ----------
    value : object
        JVM object or compatible Python test double.
    name : str
        Property or zero-argument method name.
    default : object, default=None
        Returned when the member is absent or access is restricted.

    Returns
    -------
    object
        Available member value or the supplied default.
    """
    try:
        member = getattr(value, name)
        return member() if callable(member) else member
    except Exception:
        return default


def operator_metrics(plan: Any) -> dict[str, Any]:
    """Extract metric values, names, and native Spark units from a plan node.

    Parameters
    ----------
    plan : object
        JVM ``SparkPlan`` node.

    Returns
    -------
    dict
        Metric keys mapped to dictionaries containing ``value``, ``name``,
        and ``type``. Spark's units are preserved rather than guessed.

    Examples
    --------
    ```pycon
    >>> from linescope.spark.metrics import operator_metrics
    >>> operator_metrics(None)
    {}
    ```
    """
    metrics = optional_call(plan, "metrics")
    if isinstance(metrics, dict):
        entries = metrics.items()
    else:
        entries = [
            (optional_call(item, "_1"), optional_call(item, "_2")) for item in scala_items(metrics)
        ]
    output: dict[str, Any] = {}
    for key, metric in entries:
        if key is None:
            continue
        raw_name = optional_call(metric, "name")
        name = optional_call(raw_name, "get", str(key))
        value = optional_call(metric, "value")
        if value is not None:
            output[str(key)] = {
                "value": int(value),
                "name": str(name),
                "type": str(optional_call(metric, "metricType", "unknown")),
            }
    return output


def _stage_details(context: Any, stage_id: int) -> dict[str, Any]:
    """Read optional status-store aggregates without requiring the Spark UI."""
    try:
        store = context._jsc.sc().statusStore()
        empty = context._jvm.java.util.ArrayList()
        quantiles = context._gateway.new_array(context._jvm.double, 3)
        for index, value in enumerate((0.5, 0.95, 1.0)):
            quantiles[index] = value
        attempts = scala_items(store.stageData(stage_id, False, empty, False, quantiles))
    except Exception:
        return {}
    if not attempts:
        return {}
    data = max(attempts, key=lambda item: optional_call(item, "attemptId", 0))
    attempt = optional_call(data, "attemptId", 0)
    result: dict[str, Any] = {
        "attempt": int(attempt),
        "status": str(optional_call(data, "status", "UNKNOWN")),
    }
    for field, method, scale in (
        ("cumulative_executor_time_ns", "executorRunTime", 1_000_000),
        ("input_bytes", "inputBytes", 1),
        ("output_bytes", "outputBytes", 1),
        ("shuffle_read_bytes", "shuffleReadBytes", 1),
        ("shuffle_write_bytes", "shuffleWriteBytes", 1),
        ("memory_spill_bytes", "memoryBytesSpilled", 1),
        ("disk_spill_bytes", "diskBytesSpilled", 1),
    ):
        value = optional_call(data, method)
        if isinstance(value, (int, float)):
            result[field] = int(value * scale)
    timestamps = []
    for name in ("submissionTime", "completionTime"):
        value = optional_call(data, name)
        unwrapped = optional_call(value, "get", value)
        timestamps.append(optional_call(unwrapped, "getTime"))
    if all(isinstance(value, (int, float)) for value in timestamps):
        result["wall_time_ns"] = max(0, int(timestamps[1] - timestamps[0])) * 1_000_000
        result["_submitted_at_ms"] = int(timestamps[0])
        result["_completed_at_ms"] = int(timestamps[1])
    try:
        distribution = store.taskSummary(stage_id, int(attempt), quantiles)
        if distribution.isDefined():
            durations = optional_call(distribution.get(), "duration")
            values = scala_items(durations)
            if len(values) == 3:
                result["task_duration_ns"] = {
                    name: int(float(value) * 1_000_000)
                    for name, value in zip(("p50", "p95", "max"), values, strict=True)
                }
    except Exception:
        pass
    return result


def stage_statistics(
    context: Any,
    job_ids: list[int],
    *,
    action_window: tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Read retained stage/task summaries for known jobs.

    Parameters
    ----------
    context : object
        Spark context exposing the public status tracker.
    job_ids : list of int
        Jobs associated with the execution.
    action_window : tuple of int or None, default=None
        Optional action start/end in Unix milliseconds, used to distinguish
        newly completed stage attempts from previously completed dependencies.

    Returns
    -------
    list of dict
        Retained stage IDs, names, and task counts. When the JVM status store
        permits access, records also contain stage elapsed time, cumulative
        executor time, I/O and spill bytes, and task duration quantiles. Time
        values use nanoseconds. Missing entries remain absent.

    Notes
    -----
    Cumulative executor time sums task execution across executors. Stage wall
    time spans submission to completion and includes scheduling. Retained
    stage details may include dependencies reused across actions. Executor
    totals therefore require unique completed stage attempts entirely inside
    the action window and complete retained status records. Reused/skipped
    dependencies are excluded; overlapping or missing records leave the
    execution total unknown. Operator timings are never summed into this total.

    Examples
    --------
    ```pycon
    >>> from linescope.spark.metrics import stage_statistics
    >>> stage_statistics(None, [])
    []
    ```
    """
    try:
        tracker = context.statusTracker()
    except Exception:
        return []
    stages: dict[int, dict[str, Any]] = {}
    for job_id in job_ids:
        try:
            job = tracker.getJobInfo(job_id)
            for stage_id in job.stageIds if job is not None else []:
                stage = tracker.getStageInfo(stage_id)
                if stage is not None:
                    stages[int(stage_id)] = {
                        "id": int(stage_id),
                        "name": stage.name,
                        "tasks": int(stage.numTasks),
                        "completed_tasks": int(stage.numCompletedTasks),
                        "failed_tasks": int(stage.numFailedTasks),
                    }
                    stages[int(stage_id)].update(_stage_details(context, int(stage_id)))
                    details = stages[int(stage_id)]
                    submitted = details.pop("_submitted_at_ms", None)
                    completed = details.pop("_completed_at_ms", None)
                    if action_window is not None:
                        start, end = action_window
                        if details.get("status") == "SKIPPED":
                            details["attribution"] = "skipped dependency"
                        elif completed is not None and completed < start:
                            details["attribution"] = "previously completed dependency"
                        elif (
                            submitted is not None
                            and completed is not None
                            and start <= submitted <= completed <= end
                            and details.get("status") == "COMPLETE"
                        ):
                            details["attribution"] = "completed during this action"
                        else:
                            details["attribution"] = "overlapping or unavailable stage interval"
        except Exception:
            continue
    return list(stages.values())


def _execution_executor_time(
    context: Any, job_ids: list[int], stages: list[dict[str, Any]]
) -> int | None:
    """Sum complete, uniquely attributed stage attempts without reused work."""
    try:
        tracker = context.statusTracker()
        expected: set[int] = set()
        for job_id in job_ids:
            job = tracker.getJobInfo(job_id)
            if job is None:
                return None
            expected.update(int(stage_id) for stage_id in job.stageIds)
    except Exception:
        return None
    by_id = {stage["id"]: stage for stage in stages}
    if not expected or not expected <= by_id.keys():
        return None
    values: dict[tuple[int, int], int] = {}
    for stage_id in expected:
        stage = by_id[stage_id]
        attribution = stage.get("attribution")
        if attribution in {"skipped dependency", "previously completed dependency"}:
            continue
        value = stage.get("cumulative_executor_time_ns")
        attempt = stage.get("attempt")
        if (
            attribution != "completed during this action"
            or not isinstance(value, int)
            or not isinstance(attempt, int)
        ):
            return None
        values[(stage_id, attempt)] = value
    return sum(values.values()) if values else None
