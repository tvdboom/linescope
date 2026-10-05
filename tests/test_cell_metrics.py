"""LineScope.

Author: Mavs
Description: Check per-cell deltas, unknown metrics, and safe compact rendering.

"""

from copy import deepcopy

import pytest

from linescope.enums import RunStatus
from linescope.model import (
    BackendCapabilities,
    GPUStats,
    LineStats,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
)
from linescope.notebooks.summary import cell_result
from linescope.render.cell import render_cell_summary
from tests.test_render import ReportDOM


@pytest.mark.parametrize("baseline", ["absent", "unknown", "known"])
def test_cell_deltas_keep_memory_signed_and_discard_cumulative_peaks(baseline):
    """Separate new cell costs from cumulative memory peaks and references.

    Preserve unknown memory baselines and signed releases without mutating
    the parent result or counting previous integration references again.

    """
    location = SourceLocation("cell", 1)
    before = LineStats(
        location,
        wall_time_ns=10,
        hits=2,
        samples=1,
        spark_executions=["old"],
        notebook_runs=["old-child"],
    )
    if baseline != "absent":
        before.memory = MemoryStats(delta_bytes=None if baseline == "unknown" else 20)
        before.ram = ProcessMemoryStats(delta_bytes=None if baseline == "unknown" else 20)
        before.gpu = GPUStats(time_ns=10)
    after = LineStats(
        location,
        wall_time_ns=30,
        hits=3,
        samples=4,
        memory=MemoryStats(delta_bytes=-5, peak_bytes=999),
        ram=ProcessMemoryStats(delta_bytes=-5, peak_bytes=999),
        gpu=GPUStats(time_ns=5, peak_memory_bytes=999),
        spark_executions=["old", "new"],
        notebook_runs=["old-child", "new-child"],
    )
    old_spark, new_spark = SparkExecution("old"), SparkExecution("new")
    old_child, new_child = ProfileRun(), ProfileRun()
    previous = ProfileRun(lines=[before], spark_executions=[old_spark], children=[old_child])
    current = ProfileResult(
        ProfileRun(
            lines=[after], spark_executions=[old_spark, new_spark], children=[old_child, new_child]
        ),
        {},
        "trace",
        BackendCapabilities(),
        warnings=["diagnostic"],
    )
    original = deepcopy(current)
    result = cell_result(current, previous, elapsed_ns=100, status=RunStatus.FAILED)
    row = result.root_run.lines[0]
    assert (row.wall_time_ns, row.hits, row.samples) == (20, 1, 3)
    expected = {"absent": -5, "unknown": None, "known": -25}[baseline]
    assert row.memory.delta_bytes == row.ram.delta_bytes == expected
    assert row.memory.peak_bytes is None
    assert row.ram.peak_bytes is None
    assert row.gpu.peak_memory_bytes is None
    assert row.gpu.time_ns == (5 if baseline == "absent" else None)
    assert row.spark_executions == ["new"]
    assert row.notebook_runs == ["new-child"]
    assert result.root_run.spark_executions == [new_spark]
    assert result.root_run.children == [new_child]
    assert result.root_run.elapsed_ns == 100
    assert result.root_run.status == RunStatus.FAILED
    row.memory.delta_bytes = 0
    result.warnings.clear()
    assert current == original


def test_cell_omits_unchanged_lines_and_treats_counter_reset_as_unknown():
    """Omit navigation-only rows and avoid negative cumulative counter deltas.

    Include newly measured source without a baseline and preserve unavailable
    timings and GPU measurements as unknown.

    """
    before = LineStats(SourceLocation("old", 1), 20, hits=2, samples=3)
    reset = LineStats(before.location, 5, hits=1, samples=1)
    unknown = LineStats(SourceLocation("new", 1), gpu=GPUStats())
    active = LineStats(SourceLocation("new", 2), 1, hits=1, samples=1)
    current = ProfileResult(
        ProfileRun(lines=[reset, unknown, active]), {}, "trace", BackendCapabilities()
    )
    result = cell_result(
        current, ProfileRun(lines=[before]), elapsed_ns=1, status=RunStatus.SUCCESS
    )
    assert result.root_run.lines == [active]
    assert result.root_run.lines[0] is not active


@pytest.mark.parametrize("metrics", [False, True])
def test_cell_rendering_escapes_sources_and_limits_busiest_rows(metrics):
    """Escape source and diagnostics while rendering available metric domains.

    Show only the busiest five locations, include external source origins,
    and preserve unknown measurements with an em dash.

    """
    source = SourceUnit("cell", "<script>origin</script>", "<script>work()</script>\n" * 6)
    rows = [
        LineStats(
            SourceLocation(source.id, number),
            wall_time_ns=number,
            hits=1,
            samples=1,
            memory=MemoryStats(delta_bytes=-1),
            ram=ProcessMemoryStats(delta_bytes=1),
            gpu=GPUStats(time_ns=1),
        )
        for number in range(1, 7)
    ]
    rows.append(LineStats(SourceLocation("missing", 7), wall_time_ns=99))
    result = ProfileResult(
        ProfileRun(
            lines=rows,
            status=RunStatus.FAILED,
            spark_executions=[SparkExecution("action")],
            children=[ProfileRun()],
        ),
        {source.id: source},
        "trace",
        BackendCapabilities(
            hit_counts=metrics, sample_counts=metrics, memory=metrics, gpu=metrics, sampled=metrics
        ),
        warnings=["<script>secret()</script>"],
    )
    html = render_cell_summary(result, execution_count=4)
    document = ReportDOM(html).root
    assert "<script>" not in html
    assert "iframe" not in html
    assert len(document.find_all("tr")) == 6
    assert "LineScope · Cell 4" in document.text()
    assert "Failed" in document.text()
    assert "Showing the 5 busiest of 7 measured lines" in document.text()
    assert "Unavailable" in document.text()
    assert "1 Spark actions" in document.text()
    assert "1 notebook runs" in document.text()
    assert ("Hits" in document.text()) is metrics
    assert ("Samples" in document.text()) is metrics
    assert ("RAM change" in document.text()) is metrics
    assert ("GPU time" in document.text()) is metrics
    assert "—" in document.text()
    assert "&lt;script&gt;secret()&lt;/script&gt;" in html
    same_cell = render_cell_summary(result, cell=source)
    assert "<small>" not in same_cell


def test_empty_cell_summary_is_completed_and_preserves_unknown_elapsed():
    """Display a compact empty result without invented elapsed measurements.

    Omit the table and diagnostics when the cell collected no line activity.

    """
    result = ProfileResult(ProfileRun(), {}, "trace", BackendCapabilities())
    document = ReportDOM(render_cell_summary(result)).root
    assert "Cell results" in document.text()
    assert "Completed" in document.text()
    assert "No line measurements" in document.text()
    assert not document.find_all("table")
    assert not document.find_all("details")
