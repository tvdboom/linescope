"""LineScope.

Author: Mavs
Description: Verify Spark overview dependencies and measurement provenance.

"""

from __future__ import annotations

import pytest

from linescope.model import SparkOperator
from linescope.render.spark import _output_rows, _spark_steps, _StepKind


def test_main_steps_follow_dependencies_and_keep_join_inputs_separate():
    """Keep both join branches and omit unmeasured implementation plumbing.

    Preserve measured output rows instead of adding the separate input counts.

    """
    left = SparkOperator("left", "Scan parquet", metrics={"numOutputRows": 1000})
    filtered = SparkOperator("filter", "Filter", metrics={"numOutputRows": 100}, children=[left])
    projected = SparkOperator("project", "Project", children=[filtered])
    right = SparkOperator("right", "Scan ExistingRDD", metrics={"numOutputRows": 20})
    broadcast = SparkOperator("broadcast", "BroadcastExchange", children=[right])
    stage = SparkOperator("stage", "BroadcastQueryStage 0", children=[broadcast])
    join = SparkOperator(
        "join", "BroadcastHashJoin", metrics={"numOutputRows": 300}, children=[projected, stage]
    )
    steps, pipelines = _spark_steps([join])

    assert [step.kind for step in steps] == [
        _StepKind.READ,
        _StepKind.FILTER,
        _StepKind.READ,
        _StepKind.BROADCAST,
        _StepKind.JOIN,
    ]
    assert [step.inputs for step in steps] == [[], [1], [], [3], [2, 4]]
    assert [step.rows for step in steps] == [1000, 100, 20, 20, 300]
    assert [step.rows_from_input for step in steps] == [False, False, False, True, False]
    assert not pipelines
    assert join.children == [projected, stage]


def test_fused_time_is_shared_only_with_steps_inside_its_boundary():
    """Keep shared timing on its pipeline and exclude input-adapter work.

    An upstream scan is not part of the downstream aggregation pipeline.

    """
    scan = SparkOperator("scan", "Scan parquet", metrics={"numOutputRows": 500})
    adapter = SparkOperator("adapter", "InputAdapter", children=[scan])
    aggregate = SparkOperator(
        "aggregate", "HashAggregate", metrics={"numOutputRows": 10}, children=[adapter]
    )
    sort = SparkOperator("sort", "Sort", metrics={"time_ns": 25}, children=[aggregate])
    pipeline = SparkOperator(
        "pipeline", "WholeStageCodegen (1)", metrics={"time_ns": 100}, children=[sort]
    )
    steps, pipelines = _spark_steps([pipeline])

    assert [step.cost.time_ns for step in steps] == [None, None, 25]
    assert pipelines[0].cost.time_ns == 100
    assert pipelines[0].steps == [2, 3]
    assert [step.rows for step in steps] == [500, 10, 10]
    assert steps[-1].rows_from_input


@pytest.mark.parametrize(
    "name", ["Filter", "HashAggregate", "HashJoin", "CollectLimit", "Mystery"]
)
def test_row_changing_or_unknown_operations_never_inherit_counts(name):
    """Leave output unknown when the operation can change row cardinality.

    Known input rows cannot stand in for a missing output measurement.

    """
    scan = SparkOperator("scan", "Scan", metrics={"numOutputRows": 100})
    steps, _ = _spark_steps([SparkOperator("root", name, children=[scan])])
    assert steps[-1].rows is None
    assert not steps[-1].rows_from_input


@pytest.mark.parametrize("name", ["Sort", "Exchange", "BroadcastExchange"])
def test_row_preserving_operations_carry_known_counts_with_provenance(name):
    """Carry a known input through recognized row-preserving operations.

    A measured zero remains a real output count, not an unavailable value.

    """
    scan = SparkOperator("scan", "Scan", metrics={"numOutputRows": 0})
    steps, _ = _spark_steps([SparkOperator("root", name, children=[scan])])
    assert steps[-1].rows == 0
    assert steps[-1].rows_from_input


@pytest.mark.parametrize("value", [None, True, -1, 1.5, float("nan"), float("inf"), "100"])
def test_invalid_output_row_counters_stay_unknown(value):
    """Reject invalid counts instead of rounding or coercing them.

    Unsupported values must stay unavailable in the overview.

    """
    assert _output_rows(SparkOperator("scan", "Scan", metrics={"numOutputRows": value})) is None


def test_output_counts_use_canonical_counter_and_ignore_input_and_shuffle_records():
    """Prefer measured output counts over unrelated runtime row counters.

    Retain native Spark metric descriptors and integration output counters.

    """
    node = SparkOperator(
        "scan",
        "Scan",
        metrics={
            "rows": 99,
            "numOutputRows": {"value": 0, "name": "number of output rows", "type": "sum"},
        },
    )
    assert _output_rows(node) == 0
    node.metrics = {"numInputRows": 20, "shuffleRecordsWritten": 30, "numPartitions": 2}
    assert _output_rows(node) is None
    node.metrics = {"custom": {"value": 42, "name": "number of output rows", "type": "sum"}}
    assert _output_rows(node) == 42


def test_measured_projections_are_retained_and_unknown_memory_is_not_data_size():
    """Keep expensive column work visible without converting data size to RAM.

    An unmeasured projection can be omitted, but an observed cost must survive.

    """
    scan = SparkOperator("scan", "Scan", metrics={"numOutputRows": 100})
    project = SparkOperator(
        "project",
        "Project",
        metrics={"time_ns": 100, "dataSize": {"value": 1024, "type": "size"}},
        children=[scan],
    )
    steps, _ = _spark_steps([project])
    assert steps[-1].kind == _StepKind.PROJECT
    assert steps[-1].cost.time_ns == 100
    assert steps[-1].cost.peak_memory_bytes is None
    assert steps[-1].rows == 100


def test_repeated_inputs_are_referenced_once_and_cycles_are_bounded():
    """Reuse visible dependencies and stop malformed cyclic plan traversal.

    Summaries must remain finite without modifying the captured operators.

    """
    scan = SparkOperator("scan", "Scan", metrics={"numOutputRows": 10})
    left = SparkOperator("left", "Filter", children=[scan])
    right = SparkOperator("right", "HashAggregate", children=[scan])
    join = SparkOperator("join", "HashJoin", children=[left, right])
    steps, _ = _spark_steps([join])
    assert [step.inputs for step in steps] == [[], [1], [1], [2, 3]]
    scan.children = [scan]
    steps, _ = _spark_steps([scan])
    assert len(steps) == 1
    assert steps[0].inputs == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("FileScan parquet", _StepKind.READ),
        ("Range", _StepKind.GENERATE),
        ("Filter", _StepKind.FILTER),
        ("SortMergeJoin", _StepKind.JOIN),
        ("CartesianProduct", _StepKind.JOIN),
        ("ObjectHashAggregate", _StepKind.AGGREGATE),
        ("Exchange", _StepKind.SHUFFLE),
        ("BroadcastExchange", _StepKind.BROADCAST),
        ("Sort", _StepKind.SORT),
        ("TakeOrderedAndProject", _StepKind.LIMIT),
        ("Window", _StepKind.WINDOW),
        ("Union", _StepKind.UNION),
        ("ArrowEvalPython", _StepKind.PYTHON),
        ("MapInPandas", _StepKind.PYTHON),
        ("WriteFiles", _StepKind.WRITE),
        ("Project", _StepKind.PROJECT),
        ("CustomOperation", _StepKind.OTHER),
    ],
)
def test_main_step_categories_explain_common_physical_work(name, expected):
    """Recognize main physical work and preserve unknown operation names.

    Read, movement, Python, and output operations need distinct explanations.

    """
    steps, _ = _spark_steps([SparkOperator("root", name)])
    assert steps[0].kind == expected


@pytest.mark.parametrize(
    "metric",
    [
        {"value": 10, "name": "output rows", "type": "average"},
        {"value": 10, "name": "output rows", "type": "size"},
        {"value": 10, "name": "input rows", "type": "sum"},
    ],
)
def test_row_descriptors_must_represent_total_output_counts(metric):
    """Exclude input counts, sizes, and averages from total rows after a step.

    A numeric value alone does not establish the measurement's meaning.

    """
    assert _output_rows(SparkOperator("scan", "Scan", metrics={"rows": metric})) is None
