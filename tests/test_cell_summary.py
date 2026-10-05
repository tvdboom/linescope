"""LineScope.

Author: Mavs
Description: Verify cell deltas, compact rendering, and honest metric semantics.

"""

from copy import deepcopy

import pytest

from linescope import Config, DisplayMode, RunStatus
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


@pytest.mark.parametrize("mode", ["cell-summary", DisplayMode.CELL_SUMMARY])
def test_compact_mode_normalizes_to_enum(mode):
    """Accept the compact display option through string and enum interfaces.

    Use the same validated configuration path as global and session options.

    """
    assert Config(display=mode).display is DisplayMode.CELL_SUMMARY


def test_cell_delta_keeps_only_new_costs_and_preserves_cumulative_result():
    """Subtract repeated project work without mutating the session's report.

    Keep signed memory changes and integration ownership separate from driver
    timings; cumulative peaks cannot become cell peaks.

    """
    location = SourceLocation("helper", 2)
    old = LineStats(
        location,
        wall_time_ns=100,
        hits=2,
        samples=4,
        memory=MemoryStats(delta_bytes=200, peak_bytes=500),
        ram=ProcessMemoryStats(rss_bytes=1000, delta_bytes=50, peak_bytes=1200),
        gpu=GPUStats(time_ns=100, peak_memory_bytes=900),
        spark_executions=["old-spark"],
        notebook_runs=["old-child"],
    )
    new = deepcopy(old)
    new.wall_time_ns = 150
    new.hits = 5
    new.samples = 6
    new.memory.delta_bytes = 100
    new.ram.delta_bytes = -50
    new.gpu.time_ns = 130
    new.spark_executions.append("new-spark")
    new.notebook_runs.append("new-child")
    unchanged = LineStats(SourceLocation("unrelated", 1), wall_time_ns=90, hits=1)
    old_spark, new_spark = SparkExecution("old-spark"), SparkExecution("new-spark")
    old_child, new_child = ProfileRun(id="old-child"), ProfileRun(id="new-child")
    previous = ProfileRun(
        lines=[old, deepcopy(unchanged)], spark_executions=[old_spark], children=[old_child]
    )
    current = ProfileResult(
        ProfileRun(
            lines=[new, unchanged, LineStats(SourceLocation("navigation", 1))],
            spark_executions=[old_spark, new_spark],
            children=[old_child, new_child],
        ),
        {"helper": SourceUnit("helper", "helpers.py", "def helper():\n    return 42")},
        "trace",
        BackendCapabilities(hit_counts=True, memory=True),
    )
    before = deepcopy(current)
    snapshot = cell_result(current, previous, elapsed_ns=200, status=RunStatus.SUCCESS)
    assert current == before
    assert snapshot.root_run.elapsed_ns == 200
    assert len(snapshot.root_run.lines) == 1
    line = snapshot.root_run.lines[0]
    assert (line.wall_time_ns, line.hits, line.samples) == (50, 3, 2)
    assert line.memory == MemoryStats(delta_bytes=-100)
    assert line.ram == ProcessMemoryStats(delta_bytes=-100)
    assert line.gpu == GPUStats(time_ns=30)
    assert line.spark_executions == ["new-spark"]
    assert line.notebook_runs == ["new-child"]
    assert snapshot.root_run.spark_executions == [new_spark]
    assert snapshot.root_run.children == [new_child]
    line.hits = 99
    assert new.hits == 5


def test_sampled_cell_preserves_unknown_counts_and_memory_baselines():
    """Keep unavailable measurements distinct from known zeroes.

    An unknown memory baseline and resetting cumulative counters cannot produce
    trustworthy per-cell differences.

    """
    location = SourceLocation("sampled", 1)
    current = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(
                    location,
                    samples=5,
                    memory=MemoryStats(delta_bytes=100),
                    ram=ProcessMemoryStats(delta_bytes=200),
                    gpu=GPUStats(time_ns=300),
                )
            ]
        ),
        {"sampled": SourceUnit("sampled", "cell", "answer = 42")},
        "scalene",
        BackendCapabilities(sampled=True, sample_counts=True, memory=True),
    )
    previous = ProfileRun(
        lines=[
            LineStats(
                location,
                samples=2,
                memory=MemoryStats(),
                ram=ProcessMemoryStats(),
                gpu=GPUStats(),
            )
        ]
    )
    snapshot = cell_result(current, previous, elapsed_ns=10, status=RunStatus.SUCCESS)
    line = snapshot.root_run.lines[0]
    assert line.samples == 3
    assert line.hits is None
    assert line.wall_time_ns is None
    assert line.memory.delta_bytes is None
    assert line.ram.delta_bytes is None
    assert line.gpu.time_ns is None
    dom = ReportDOM(render_cell_summary(snapshot)).root
    headings = [element.text() for element in dom.find_all("th")]
    assert "Samples" in headings
    assert "Hits" not in headings
    values = [element.text() for element in dom.find_all("td")]
    assert values == ["1", "answer = 42cell", "—", "3", "—", "—"]
    previous.lines[0].samples = 10
    assert (
        cell_result(current, previous, elapsed_ns=10, status=RunStatus.SUCCESS).root_run.lines
        == []
    )


def test_compact_renderer_limits_rows_and_escapes_source_and_diagnostics():
    """Render a bounded fragment without introducing executable source markup.

    Preserve complete snapshots in the input result and label source from other
    cells while keeping all styles inside the summary's scope.

    """
    source = SourceUnit(
        "cell", '<img src=x onerror="alert(1)">', "\n".join(f"value = {i}" for i in range(7))
    )
    helper = SourceUnit("helper", "old cell", '<script>alert("source")</script>')
    result = ProfileResult(
        ProfileRun(
            status=RunStatus.FAILED,
            lines=[
                *(
                    LineStats(SourceLocation("cell", i + 1), wall_time_ns=i, hits=1)
                    for i in range(7)
                ),
                LineStats(SourceLocation("helper", 1), wall_time_ns=100, hits=1),
            ],
        ),
        {source.id: source, helper.id: helper},
        'custom<script>alert("backend")</script>',
        BackendCapabilities(hit_counts=True),
        warnings=['<script>alert("diagnostic")</script>'],
    )
    html = render_cell_summary(result, cell=source, execution_count=8)
    dom = ReportDOM(html).root
    assert len(dom.find_all("tbody")[0].find_all("tr")) == 5
    assert dom.find_all("h4")[0].text() == "LineScope · Cell 8"
    assert dom.find_all("code")[0].text() == helper.source
    assert "Showing the 5 busiest of 8 measured lines." in dom.text()
    assert "Failed" in dom.text()
    assert not dom.find_all("script")
    assert not dom.find_all("iframe")
    assert not dom.find_all("img")
    assert len(result.root_run.lines) == 8
    selectors = dom.find_all("style")[0].text().split("}")
    assert all(
        selector.strip().startswith(".linescope-cell-summary")
        for selector in selectors
        if selector.strip()
    )


def test_empty_cell_reports_absent_observations():
    """Describe an unmeasured cell without inventing timings or counts.

    Short sampled cells and syntax failures can have no line observations.

    """
    result = ProfileResult(ProfileRun(), {}, "trace", BackendCapabilities())
    dom = ReportDOM(render_cell_summary(result)).root
    assert "No line measurements were collected for this cell." in dom.text()
    assert not dom.find_all("table")
