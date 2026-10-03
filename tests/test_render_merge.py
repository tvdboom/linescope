"""Validate report semantics for independently collected child profiles."""

from dataclasses import asdict

import pytest
from test_render import ReportDOM, cell_values

from linescope.model import (
    BackendCapabilities,
    LineStats,
    MemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render import render_html


def test_merged_child_uses_its_own_memory_and_sampling_capabilities():
    parent = SourceUnit("parent", "parent.py", "invoke_child()\n")
    child = SourceUnit("child", "child.py", "value = 1\n# unobserved\n")
    child_run = ProfileRun(
        source=child,
        lines=[LineStats(SourceLocation(child.id, 1), 2_000_000, memory=MemoryStats(1024, 2048))],
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
    assert "Python time" in parent_page.text()
    assert "Driver memory" not in parent_page.text()
    assert "Estimated time" in child_page.text()
    assert "Driver memory Δ" in child_page.text()
    rows = child_page.find_all("tr", css="source-row")
    assert cell_values(rows[0])[1:6] == ["2.00 ms", "—", "—", "+1.0 KiB", "2.0 KiB"]
    assert cell_values(rows[1])[1:6] == ["—", "—", "—", "—", "—"]
    assert "Mixed collection" in document.text()
    assert "Observed lines" in document.text()


def test_shared_snapshot_combines_child_measurements_without_mutating_inputs():
    unit = SourceUnit("shared", "shared.py", "work()\n")
    parent_line = LineStats(SourceLocation(unit.id, 1), 2_000_000, 2, MemoryStats(100, 1000))
    child_line = LineStats(SourceLocation(unit.id, 1), 3_000_000, 3, MemoryStats(-50, 2000))
    root = ProfileRun(lines=[parent_line], children=[ProfileRun(lines=[child_line])])
    result = ProfileResult(
        root, {unit.id: unit}, "trace", BackendCapabilities(hit_counts=True, memory=True)
    )
    document = ReportDOM(render_html(result)).root
    rows = document.find_all("tr", css="source-row")
    assert len(rows) == 1
    assert cell_values(rows[0])[1:6] == ["5.00 ms", "5", "1.00 ms", "+50 B", "2.0 KiB"]
    assert parent_line.wall_time_ns == 2_000_000
    assert parent_line.hits == 2
    assert parent_line.memory == MemoryStats(100, 1000)
    child_line.hits = None
    document = ReportDOM(render_html(result)).root
    assert cell_values(document.find_all("tr", css="source-row")[0])[2:4] == ["—", "—"]


@pytest.mark.parametrize("override", [False, True])
def test_nested_runs_inherit_nearest_ancestor_capabilities(override):
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
            assert "Python time" in page.text()
            assert "Estimated time" not in page.text()
            assert "Driver memory" not in page.text()
            assert cell_values(page.find_all("tr", css="source-row")[1])[1:4] == ["—", "0", "—"]
        else:
            assert "Estimated time" in page.text()
            assert "Driver memory Δ" in page.text()
            assert cell_values(page.find_all("tr", css="source-row")[1])[1:6] == ["—"] * 5
    assert "Python time" in pages["sibling.py"].text()
    assert "Driver memory" not in pages["sibling.py"].text()
    assert cell_values(pages["sibling.py"].find_all("tr", css="source-row")[1])[2] == "0"
