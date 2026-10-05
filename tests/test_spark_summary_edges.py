"""LineScope.

Author: Mavs
Description: Preserve metric units and dependency ownership in Spark summaries.

"""

from copy import deepcopy

import pytest

from linescope.model import SparkOperator
from linescope.render.spark import _operator_cost, _output_rows, _spark_steps, _step_kind


@pytest.mark.parametrize(
    ("operator", "label"),
    [
        ("BatchEvalPython", "Run Python code"),
        ("SortMergeJoin", "Combine tables"),
        ("HashAggregate", "Group and summarize"),
        ("BroadcastExchange", "Share a table with workers"),
        ("Exchange", "Move data between workers"),
        ("CollectLimit", "Take a limited result"),
        ("FileScan", "Read data"),
        ("Range", "Generate rows"),
        ("Filter", "Keep matching rows"),
        ("Sort", "Sort rows"),
        ("Window", "Calculate over related rows"),
        ("Union", "Combine input rows"),
        ("WriteFiles", "Write result"),
        ("Project", "Calculate columns"),
        ("UnknownOperation", "Process rows"),
    ],
)
def test_operator_categories_keep_unknown_semantics_visible(operator, label):
    """Describe recognized operations while preserving unknown plan nodes.

    The report must not infer a filter, join, or data-source meaning for
    unsupported operators.

    """
    assert _step_kind(SparkOperator("node", operator)) == label


def test_costs_keep_overlapping_timings_and_memory_domains_separate():
    """Select the largest timing rather than sum overlapping node metrics.

    Respect Spark's timing units and distinguish peak memory from shuffle
    volume and spill bytes, including real measured zeroes.

    """
    operator = SparkOperator(
        "node",
        "Sort",
        metrics={
            "sort": {"value": 2, "type": "timing", "name": "sorting"},
            "other": {"value": 1, "type": "nstiming"},
            "duration_ns": 5,
            "peak_memory_bytes": 0,
            "spill_bytes": 10,
            "dataSize": {"value": 999, "type": "size"},
            "inputBytes": 999,
            "invalid": {"value": float("inf"), "type": "timing"},
            "boolean": {"value": True, "type": "size"},
            "negative": {"value": -1, "type": "timing"},
            "unknown": {"value": None, "type": "timing"},
            "untyped": {"value": 4, "type": "unknown"},
        },
    )
    cost = _operator_cost(operator)
    assert cost.time_ns == 2_000_000
    assert cost.time_label == "sorting"
    assert cost.peak_memory_bytes == 0
    assert cost.spill_bytes == 10


@pytest.mark.parametrize("value", [None, True, -1, 1.5, float("nan"), "2"])
def test_invalid_output_counters_do_not_become_row_estimates(value):
    """Leave malformed cardinality unavailable rather than deriving a count.

    Never interpret input-row or partition counters as output cardinality.

    """
    operator = SparkOperator(
        "node",
        "Filter",
        metrics={
            "numOutputRows": {"value": value},
            "inputRows": 20,
            "partitions": 2,
        },
    )
    assert _output_rows(operator) is None


def test_output_rows_prefer_canonical_counters_then_valid_named_fallback():
    """Prefer Spark's canonical output count and preserve known zeroes.

    Fall back to an explicitly named output counter when the canonical value
    is unavailable, without estimating from input volume.

    """
    operator = SparkOperator("node", "Filter", metrics={"rows": 4, "numOutputRows": 0})
    assert _output_rows(operator) == 0
    operator.metrics = {
        "numOutputRows": None,
        "custom": {"value": 2.0, "name": "number of output rows"},
    }
    assert _output_rows(operator) == 2


def test_pipeline_costs_stop_at_input_boundaries_and_shared_nodes():
    """Keep fused timings on their owner and dependencies in input order.

    Deduplicate shared subtrees, carry known cardinality through row-preserving
    nodes, and avoid mutating the captured operator plan.

    """
    scan = SparkOperator("scan", "FileScan", metrics={"numOutputRows": 20})
    adapter = SparkOperator("adapter", "InputAdapter", children=[scan])
    project = SparkOperator("project", "Project", children=[adapter])
    sort = SparkOperator("sort", "Sort", children=[project])
    fused = SparkOperator(
        "fused",
        "WholeStageCodegen (1)",
        metrics={"time": {"value": 2, "type": "timing"}},
        children=[sort],
    )
    join = SparkOperator("join", "SortMergeJoin", children=[fused, scan])
    original = deepcopy(join)
    steps, pipelines = _spark_steps([join])
    assert [step.operator.id for step in steps] == ["scan", "sort", "join"]
    assert steps[1].rows == 20
    assert steps[1].rows_from_input
    assert steps[2].rows is None
    assert steps[2].inputs == [2, 1]
    assert pipelines[0].steps == [2]
    assert pipelines[0].cost.time_ns == 2_000_000
    assert steps[1].cost.time_ns is None
    assert join == original


def test_measured_plumbing_empty_pipeline_and_cycles_remain_bounded():
    """Keep measured projections visible and terminate malformed plan cycles.

    Omit unmeasured fused plumbing while retaining available source-node
    measurements and unknown row counts at cycle boundaries.

    """
    scan = SparkOperator("scan", "Scan", metrics={"numOutputRows": 3})
    project = SparkOperator("project", "Project", metrics={"duration_ns": 1}, children=[scan])
    fused = SparkOperator("fused", "WholeStageCodegen", children=[project])
    steps, pipelines = _spark_steps([fused, project])
    assert [step.operator.id for step in steps] == ["scan", "project"]
    assert steps[1].rows == 3
    assert not pipelines
    cycle = SparkOperator("cycle", "UnknownOperation")
    cycle.children.append(cycle)
    steps, _pipelines = _spark_steps([cycle])
    assert len(steps) == 1
    assert steps[0].rows is None
