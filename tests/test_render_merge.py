"""LineScope.

Author: Mavs
Description: Validate report semantics for independently collected child
profiles.

"""

from dataclasses import asdict

import pytest

from linescope.model import (
    BackendCapabilities,
    LineStats,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render import render_html
from tests.test_render import ReportDOM, cell_values


def test_merged_child_uses_its_own_memory_and_sampling_capabilities():
    """Verify merged child uses its own memory and sampling capabilities.

    Render controlled parent and child profiles and inspect metric ownership
    without mutating input snapshots.

    """
    parent = SourceUnit("parent", "parent.py", "invoke_child()\n")
    child = SourceUnit("child", "child.py", "value = 1\n# unobserved\n")
    child_run = ProfileRun(
        source=child,
        lines=[
            LineStats(
                SourceLocation(child.id, 1), 2_000_000, ram=ProcessMemoryStats(2048, 1024, 4096)
            )
        ],
        metadata={
            "child_backend": "scalene",
            "child_capabilities": asdict(BackendCapabilities(sampled=True, memory=True)),
        },
    )
    result = ProfileResult(
        ProfileRun(
            source=parent,
            lines=[LineStats(SourceLocation(parent.id, 1), 5_000_000, 1)],
            children=[child_run],
        ),
        {parent.id: parent, child.id: child},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    document = ReportDOM(render_html(result)).root
    pages = document.find_all("section")
    parent_page = next(
        page
        for page in pages
        if "parent.py" in "".join(item.text() for item in page.find_all("h1"))
    )
    child_page = next(
        page
        for page in pages
        if "child.py" in "".join(item.text() for item in page.find_all("h1"))
    )
    assert parent_page.find_all("th")[1].attributes["title"] == "Python line time"
    assert "Mem Change" not in parent_page.text()
    assert (
        child_page.find_all("th")[1].attributes["title"] == "Sampled estimates of Python line time"
    )
    assert "Mem Change" in child_page.text()
    assert [
        button.attributes["data-sort"]
        for button in parent_page.find_all("button", css="table-sort")
    ] == ["time", "hits", "average", "line"]
    assert [
        button.attributes["data-sort"]
        for button in child_page.find_all("button", css="table-sort")
    ] == ["time", "memory", "peak", "line"]
    rows = child_page.find_all("tr", css="source-row")
    assert [header.text() for header in parent_page.find_all("th")] == [
        "",
        "Time",
        "Hits",
        "Avg / hit",
        "Source",
    ]
    assert [header.text() for header in child_page.find_all("th")] == [
        "",
        "Time",
        "Mem Change",
        "Peak Mem",
        "Source",
    ]
    assert [cell_values(rows[0])[index] for index in (1, 2, 3)] == ["2.00 ms", "+1.0 KB", "4.1 KB"]
    assert [cell_values(rows[1])[index] for index in (1, 2, 3)] == ["—"] * 3
    assert [
        button.attributes["data-heat"]
        for button in parent_page.find_all("button", css="source-heat")
    ] == ["none", "time"]
    assert [
        button.attributes["data-heat"]
        for button in child_page.find_all("button", css="source-heat")
    ] == ["none", "time", "memory"]
    assert "Mixed collection" in document.text()
    assert "Observed lines" in document.text()


def test_shared_snapshot_combines_child_measurements_without_mutating_inputs():
    """Check the expected behavior in this regression case.

    Verify shared snapshot combines child measurements without mutating inputs.

    """
    unit = SourceUnit("shared", "shared.py", "work()\n")
    parent_line = LineStats(SourceLocation(unit.id, 1), 2_000_000, 2, MemoryStats(100, 1000))
    child_line = LineStats(SourceLocation(unit.id, 1), 3_000_000, 3, MemoryStats(-50, 2000))
    root = ProfileRun(lines=[parent_line], children=[ProfileRun(lines=[child_line])])
    result = ProfileResult(
        root, {unit.id: unit}, "trace", BackendCapabilities(hit_counts=True, memory=True)
    )
    document = ReportDOM(render_html(result)).root
    rows = document.find_all("tr", css="source-row")
    summary = document.find_all("div", css="source-summary")[0].find_all("p")[0]
    assert summary.text() == "1 lines · Total time: 5.00 ms"
    assert len(rows) == 1
    assert [cell_values(rows[0])[index] for index in (1, 2, 3, 4, 5)] == [
        "5.00 ms",
        "5",
        "1.00 ms",
        "—",
        "—",
    ]
    assert parent_line.wall_time_ns == 2_000_000
    assert parent_line.hits == 2
    assert parent_line.memory == MemoryStats(100, 1000)
    child_line.hits = None
    document = ReportDOM(render_html(result)).root
    assert cell_values(document.find_all("tr", css="source-row")[0])[2:4] == ["—", "—"]


def test_shared_trace_and_sampling_sources_preserve_known_counts():
    """Verify shared trace and sampling sources preserve known counts.

    Render controlled parent and child profiles and inspect metric ownership
    without mutating input snapshots.

    """
    unit = SourceUnit("shared", "shared.py", "traced()\nsampled()\n# unobserved\n")
    parent_line = LineStats(SourceLocation(unit.id, 1), 2000, hits=2)
    child_line = LineStats(SourceLocation(unit.id, 2), 3000, samples=3)
    child = ProfileRun(
        lines=[child_line],
        metadata={
            "child_backend": "scalene",
            "child_capabilities": asdict(BackendCapabilities(sampled=True, sample_counts=True)),
        },
    )
    result = ProfileResult(
        ProfileRun(lines=[parent_line], children=[child]),
        {unit.id: unit},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    table = ReportDOM(render_html(result)).root.find_all("table", css="source-table")[0]
    assert [header.text() for header in table.find_all("th")] == [
        "",
        "Time",
        "Samples",
        "Hits",
        "Avg / hit",
        "Source",
    ]
    rows = table.find_all("tr", css="source-row")
    assert [cell_values(rows[0])[index] for index in (1, 2, 3, 4)] == [
        "2.0 µs",
        "—",
        "2",
        "1.0 µs",
    ]
    assert [cell_values(rows[1])[index] for index in (1, 2, 3, 4)] == ["3.0 µs", "3", "—", "—"]
    assert [cell_values(rows[2])[index] for index in (1, 2, 3, 4)] == ["—"] * 4

    # Counts become unavailable when collectors contribute to the same line.
    child_line.location = SourceLocation(unit.id, 1)
    table = ReportDOM(render_html(result)).root.find_all("table", css="source-table")[0]
    assert [header.text() for header in table.find_all("th")] == [
        "",
        "Time",
        "Source",
    ]
    assert cell_values(table.find_all("tr", css="source-row")[0]) == ["1", "5.0 µs", "traced()"]
    assert parent_line.hits == 2
    assert child_line.samples == 3


@pytest.mark.parametrize("override", [False, True])
def test_nested_runs_inherit_nearest_ancestor_capabilities(override):
    """Verify nested runs inherit nearest ancestor capabilities.

    Render controlled parent and child profiles and inspect metric ownership
    without mutating input snapshots.

    """
    trace = BackendCapabilities(hit_counts=True)
    sampled = BackendCapabilities(sampled=True, memory=True)
    units = {
        name: SourceUnit(name, f"{name}.py", "work()\n# unobserved\n")
        for name in ("grandchild", "descendant", "sibling")
    }
    descendant = ProfileRun(
        source=units["descendant"],
        lines=[LineStats(SourceLocation("descendant", 1), 3_000_000)],
    )
    grandchild = ProfileRun(
        # Source locations also inherit capabilities when the run has no own source.
        lines=[LineStats(SourceLocation("grandchild", 1), 2_000_000)],
        metadata={"child_capabilities": asdict(trace)} if override else {},
        children=[descendant],
    )
    child = ProfileRun(
        metadata={"child_capabilities": asdict(sampled)},
        children=[ProfileRun(children=[grandchild])],
    )
    sibling = ProfileRun(source=units["sibling"])
    result = ProfileResult(ProfileRun(children=[child, sibling]), units, "trace", trace)
    document = ReportDOM(render_html(result)).root
    pages = {
        title.text(): page
        for page in document.find_all("section")
        for title in page.find_all("h1", css="path-title")
    }
    for name in ("grandchild", "descendant"):
        page = pages[f"{name}.py"]
        if override:
            assert page.find_all("th")[1].attributes["title"] == "Python line time"
            assert "Mem Change" not in page.text()
            values = cell_values(page.find_all("tr", css="source-row")[1])
            assert [values[index] for index in (1, 2, 3)] == ["—", "0", "—"]
        else:
            assert page.find_all("th")[1].attributes["title"] == (
                "Sampled estimates of Python line time"
            )
            assert "Mem Change" in page.text()
            values = cell_values(page.find_all("tr", css="source-row")[1])
            assert [values[index] for index in (1, 2, 3)] == ["—"] * 3
    assert pages["sibling.py"].find_all("th")[1].attributes["title"] == "Python line time"
    assert "Mem Change" not in pages["sibling.py"].text()
    assert cell_values(pages["sibling.py"].find_all("tr", css="source-row")[1])[2] == "0"
