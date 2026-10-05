"""LineScope.

Author: Mavs
Description: Reduce captured Spark plans to main data-flow steps for reports.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from linescope.model import SparkOperator


class _StepKind(StrEnum):
    """Name the main operations in language useful without Spark expertise.

    Attributes
    ----------
    READ : str
        Load rows from an input source or cached data.

    GENERATE : str
        Create rows inside Spark rather than reading external data.

    FILTER : str
        Retain rows matching a condition.

    JOIN : str
        Match rows from separate inputs.

    AGGREGATE : str
        Group rows and calculate summaries.

    SORT : str
        Order rows by one or more values.

    SHUFFLE : str
        Redistribute data between workers.

    BROADCAST : str
        Share one input with the workers processing another input.

    PYTHON : str
        Execute Python code in worker processes.

    WINDOW : str
        Calculate values over related rows.

    UNION : str
        Append rows from separate inputs.

    LIMIT : str
        Restrict the number of output rows.

    WRITE : str
        Save the result to an output destination.

    PROJECT : str
        Select or calculate columns without changing the number of rows.

    OTHER : str
        Retain an operation whose semantics cannot be safely simplified.

    """

    READ = "Read data"
    GENERATE = "Generate rows"
    FILTER = "Keep matching rows"
    JOIN = "Combine tables"
    AGGREGATE = "Group and summarize"
    SORT = "Sort rows"
    SHUFFLE = "Move data between workers"
    BROADCAST = "Share a table with workers"
    PYTHON = "Run Python code"
    WINDOW = "Calculate over related rows"
    UNION = "Combine input rows"
    LIMIT = "Take a limited result"
    WRITE = "Write result"
    PROJECT = "Calculate columns"
    OTHER = "Process rows"


@dataclass(frozen=True)
class _SparkCost:
    """Preserve comparable Spark costs without inventing missing metrics.

    Attributes
    ----------
    time_ns : int | None, default=None
        Largest reported timing in nanoseconds, if available.

    peak_memory_bytes : int | None, default=None
        Largest explicitly labeled peak-memory counter, if available.

    spill_bytes : int | None, default=None
        Largest reported spill counter in bytes, if available.

    time_label : str, default=""
        Name of the selected timing metric for explaining its scope.

    """

    time_ns: int | None = None
    peak_memory_bytes: int | None = None
    spill_bytes: int | None = None
    time_label: str = ""


@dataclass
class _SparkStep:
    """Keep a main operation and its upstream dependencies together.

    Attributes
    ----------
    number : int
        One-based position in input-to-output data-flow order, not time order.

    operator : [SparkOperator]
        Original captured node owning the measurements and detail link.

    kind : _StepKind
        Plain-language category of the physical operation.

    inputs : list[int]
        Earlier step numbers feeding this operation; branches stay separate.

    cost : _SparkCost
        Costs reported on this node, excluding shared pipeline timings.

    rows : int | None
        Total output rows reported by Spark or preserved from a known input.
        Missing counters on operations that change rows remain unknown.

    rows_from_input : bool
        Whether the row count was carried through a row-preserving operation
        instead of measured on this node.

    """

    number: int
    operator: SparkOperator
    kind: _StepKind
    inputs: list[int]
    cost: _SparkCost
    rows: int | None
    rows_from_input: bool


@dataclass
class _SparkPipeline:
    """Retain shared costs independently of the operations they cover.

    Attributes
    ----------
    operator : [SparkOperator]
        Captured fused-pipeline node that owns the shared measurement.

    cost : _SparkCost
        Reported costs that must never be assigned to individual children.

    steps : list[int], default=list()
        Main step numbers inside this fused pipeline. Input adapters end
        membership so upstream work is not attributed to this pipeline.

    """

    operator: SparkOperator
    cost: _SparkCost
    steps: list[int] = field(default_factory=list)


def _operator_cost(operator: SparkOperator) -> _SparkCost:
    """Select reported costs without adding overlapping SQL metrics.

    Multiple timings on one node can overlap. Use the largest reported
    timing, preserving its name, and never derive action totals from it.
    Byte counters for data size, shuffle, and spill are not peak memory.

    Parameters
    ----------
    operator : [SparkOperator]
        Captured node whose metric names and native units are inspected.

    Returns
    -------
    _SparkCost
        Known costs with missing measurements left unavailable.

    """
    times: list[tuple[int, str]] = []
    memory: list[int] = []
    spills: list[int] = []
    for key, metric in operator.metrics.items():
        structured = isinstance(metric, dict)
        name = str(metric.get("name") or key) if structured else str(key)
        kind = str(metric.get("type", "")).lower() if structured else ""
        value = metric.get("value") if structured else metric
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            or value < 0
        ):
            continue

        category = f"{key} {name}".lower()
        if kind in ("timing", "nstiming") or (not structured and str(key).endswith("_ns")):
            times.append((int(value * (1_000_000 if kind == "timing" else 1)), name))
        elif kind == "size" or (not structured and "byte" in category):
            if "spill" in category:
                spills.append(int(value))
            elif "memory" in category and "peak" in category:
                memory.append(int(value))

    duration, label = max(times, key=lambda item: item[0]) if times else (None, "")
    return _SparkCost(duration, max(memory, default=None), max(spills, default=None), label)


def _output_rows(operator: SparkOperator) -> int | None:
    """Read total output-row counters without mistaking input rows for output.

    Prefer Spark's canonical counter. Accept explicitly named output-row
    counters from integrations, but never use partition counts, shuffle
    records, estimates, or invalid numbers as output cardinality.

    Parameters
    ----------
    operator : [SparkOperator]
        Captured node whose row counters are inspected.

    Returns
    -------
    int | None
        Nonnegative whole output count, or None when unavailable.

    """
    metrics = sorted(operator.metrics.items(), key=lambda item: item[0] != "numOutputRows")
    for key, metric in metrics:
        structured = isinstance(metric, dict)
        name = str(metric.get("name") or "") if structured else ""
        names = {str(value).replace("_", "").replace(" ", "").lower() for value in (key, name)}
        output_names = {"numoutputrows", "outputrows", "numberofoutputrows"}
        if not names.intersection(output_names) and not (not structured and key == "rows"):
            continue
        if structured and str(metric.get("type", "")).lower() not in {
            "",
            "sum",
            "count",
            "unknown",
        }:
            continue

        value = metric.get("value") if structured else metric
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            and value >= 0
            and int(value) == value
        ):
            return int(value)

    return None


def _step_kind(operator: SparkOperator) -> _StepKind:
    """Translate recognized physical operators into main user-facing steps.

    Keep unknown operations visible instead of guessing their semantics.

    Parameters
    ----------
    operator : [SparkOperator]
        Node whose Spark name identifies the operation.

    Returns
    -------
    _StepKind
        Category suitable for a concise data-flow overview.

    """
    name = operator.name.lower()
    if "python" in name or "pandas" in name:
        return _StepKind.PYTHON
    if "join" in name or "cartesianproduct" in name:
        return _StepKind.JOIN
    if "aggregate" in name:
        return _StepKind.AGGREGATE
    if "broadcastexchange" in name:
        return _StepKind.BROADCAST
    if "exchange" in name or "repartition" in name:
        return _StepKind.SHUFFLE
    if "limit" in name or "takeordered" in name:
        return _StepKind.LIMIT
    if "scan" in name:
        return _StepKind.READ
    if name.startswith("range"):
        return _StepKind.GENERATE
    if "filter" in name:
        return _StepKind.FILTER
    if name.startswith("sort"):
        return _StepKind.SORT
    if "window" in name:
        return _StepKind.WINDOW
    if "union" in name:
        return _StepKind.UNION
    if any(word in name for word in ("write", "insert", "append", "save")):
        return _StepKind.WRITE
    if name.startswith("project"):
        return _StepKind.PROJECT
    return _StepKind.OTHER


def _spark_steps(operators: list[SparkOperator]) -> tuple[list[_SparkStep], list[_SparkPipeline]]:
    """Reduce a plan to a dependency-preserving overview without executing it.

    Traverse inputs before consumers and omit unmeasured plumbing and column
    projections. Fused timing stays attached to its owning pipeline. Carry
    row counts only through explicitly recognized row-preserving nodes;
    never infer output from a filter, join, aggregate, limit, or unknown node.

    Parameters
    ----------
    operators : list[[SparkOperator]]
        Captured physical-plan roots, normally from the final adaptive plan.

    Returns
    -------
    tuple[list[_SparkStep], list[_SparkPipeline]]
        Main steps in data-flow order and independently measured pipelines.

    """
    steps: list[_SparkStep] = []
    pipelines: list[_SparkPipeline] = []
    outputs: dict[str, tuple[list[int], int | None]] = {}

    def visit(
        operator: SparkOperator,
        pipeline: _SparkPipeline | None = None,
    ) -> tuple[list[int], int | None]:
        """Retain upstream step references while bounding repeated nodes.

        Parameters
        ----------
        operator : [SparkOperator]
            Captured node to simplify without mutating the original tree.

        pipeline : _SparkPipeline | None, default=None
            Enclosing fused group, ending at an input or query-stage boundary.

        Returns
        -------
        tuple[list[int], int | None]
            Visible outputs and known cardinality of this subtree.

        """
        if operator.id in outputs:
            return outputs[operator.id]

        # Install an empty result first so malformed cycles cannot recurse.
        outputs[operator.id] = ([], None)
        name = operator.name.split(" ", 1)[0].lower()
        cost = _operator_cost(operator)
        fused = name == "wholestagecodegen"
        wrapper = name in {
            "inputadapter",
            "resultquerystage",
            "shufflequerystage",
            "broadcastquerystage",
            "adaptivesparkplan",
        }
        if fused:
            pipeline = _SparkPipeline(operator, cost)
            if cost != _SparkCost():
                pipelines.append(pipeline)
        elif wrapper:
            pipeline = None

        children = [visit(child, pipeline) for child in operator.children]
        inputs = list(dict.fromkeys(number for numbers, _ in children for number in numbers))
        rows = _output_rows(operator)
        preserves_rows = (
            fused
            or wrapper
            or name
            in {
                "project",
                "sort",
                "exchange",
                "shuffleexchange",
                "broadcastexchange",
                "aqeshuffleread",
                "columnartorow",
                "rowtocolumnar",
            }
        )
        from_input = rows is None and preserves_rows and len(children) == 1
        if from_input:
            rows = children[0][1]

        plumbing = wrapper or name in {
            "project",
            "aqeshuffleread",
            "columnartorow",
            "rowtocolumnar",
        }
        if fused or (plumbing and cost == _SparkCost() and operator.children):
            output = (inputs, rows)
        else:
            number = len(steps) + 1
            steps.append(
                _SparkStep(number, operator, _step_kind(operator), inputs, cost, rows, from_input)
            )
            if pipeline is not None:
                pipeline.steps.append(number)
            output = ([number], rows)

        outputs[operator.id] = output
        return output

    for operator in operators:
        visit(operator)

    pipelines.sort(key=lambda pipeline: min(pipeline.steps, default=len(steps) + 1))
    return steps, pipelines
