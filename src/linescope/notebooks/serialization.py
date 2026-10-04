"""LineScope.

Author: Mavs
Description: Transport normalized child profiles as versioned JSON data.

"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    GPUStats,
    LineStats,
    MemorySample,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
    SparkExecutionStats,
    SparkOperator,
    SymbolDefinition,
    SymbolRef,
)


def dumps_result(result: ProfileResult) -> bytes:
    """Encode a normalized result without executable objects.

    Keep unsupported measurements as JSON null values.

    """
    return json.dumps({"version": 1, "result": asdict(result)}, ensure_ascii=True).encode("utf-8")


def _fields(data: dict[str, Any], **decoded: Any) -> dict[str, Any]:
    """Combine serialized fields with decoded nested model values.

    Replace only the supplied nested fields while retaining all other serialized
    values.

    Parameters
    ----------
    data : dict[str, Any]
        Normalized or serialized values used by this operation.

    **decoded : Any
        Nested model values replacing their serialized representations.

    Returns
    -------
    dict[str, Any]
        Combined fields with decoded nested model replacements.

    """
    return {**data, **decoded}


def _operator(data: dict[str, Any]) -> SparkOperator:
    """Reconstruct an operator and its nested children from serialized data.

    Restore source locations without substituting values for unavailable
    metrics.

    Parameters
    ----------
    data : dict[str, Any]
        Normalized or serialized values used by this operation.

    Returns
    -------
    [SparkOperator]
        Reconstructed operator model with nested children.

    """
    return SparkOperator(
        **_fields(
            data,
            children=[_operator(child) for child in data["children"]],
            locations=[SourceLocation(**location) for location in data["locations"]],
        )
    )


def _execution(data: dict[str, Any]) -> SparkExecution:
    """Reconstruct an observed Spark action from serialized data.

    Restore nested operator, location, and metric models.

    Parameters
    ----------
    data : dict[str, Any]
        Normalized or serialized values used by this operation.

    Returns
    -------
    [SparkExecution]
        Reconstructed action model with available measurements.

    """
    return SparkExecution(
        **_fields(
            data,
            location=SourceLocation(**data["location"]) if data["location"] else None,
            stats=SparkExecutionStats(**data["stats"]),
            operators=[_operator(operator) for operator in data["operators"]],
        )
    )


def _run(data: dict[str, Any]) -> ProfileRun:
    """Reconstruct a profiling run and its nested child tree.

    Restore source, line, function, and Spark models while retaining unknown
    measurements.

    Parameters
    ----------
    data : dict[str, Any]
        Normalized or serialized values used by this operation.

    Returns
    -------
    [ProfileRun]
        Reconstructed profiling run and child tree.

    """
    lines = [
        LineStats(
            **_fields(
                line,
                location=SourceLocation(**line["location"]),
                memory=MemoryStats(**line["memory"]) if line["memory"] else None,
                ram=ProcessMemoryStats(**line["ram"]) if line.get("ram") else None,
                gpu=GPUStats(**line["gpu"]) if line["gpu"] else None,
                calls=[
                    SymbolRef(**_fields(call, target=SourceLocation(**call["target"])))
                    for call in line["calls"]
                ],
            )
        )
        for line in data["lines"]
    ]

    return ProfileRun(
        **_fields(
            data,
            source=SourceUnit(**data["source"]) if data["source"] else None,
            lines=lines,
            functions=[FunctionStats(**function) for function in data["functions"]],
            spark_executions=[_execution(execution) for execution in data["spark_executions"]],
            children=[_run(child) for child in data["children"]],
            memory_samples=[
                MemorySample(
                    **_fields(
                        sample,
                        location=SourceLocation(**sample["location"])
                        if sample["location"]
                        else None,
                    )
                )
                for sample in data.get("memory_samples", [])
            ],
        )
    )


def loads_result(payload: bytes) -> ProfileResult:
    """Decode the supported JSON schema into normalized model objects.

    Reject unknown versions and malformed profiles. Never import or execute
    types named by a remote payload.

    """
    envelope = json.loads(payload)

    if envelope["version"] != 1:
        raise ValueError("Unsupported child profile format.")

    data = envelope["result"]
    return ProfileResult(
        root_run=_run(data["root_run"]),
        sources={key: SourceUnit(**source) for key, source in data["sources"].items()},
        backend=data["backend"],
        capabilities=BackendCapabilities(**data["capabilities"]),
        symbols=[SymbolDefinition(**symbol) for symbol in data["symbols"]],
        warnings=list(data["warnings"]),
    )
