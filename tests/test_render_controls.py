"""LineScope.

Author: Mavs
Description: Verify report column sorting metadata and optional summary heat.

"""

import pytest

from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    GPUStats,
    LineStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render.html import render_html
from tests.test_render import ReportDOM, cell_values


def _profile(*, sampled: bool = False) -> ProfileResult:
    """Build a controlled profile with positive, zero, and unknown costs.

    Parameters
    ----------
    sampled : bool, default=False
        Whether observation counts replace traced execution counts.

    Returns
    -------
    ProfileResult
        Three snapshots with distinct names and sortable measurements.

    """
    unit = SourceUnit(
        "zeta", "zeta.py", "def zulu(): return 1\ndef Alpha(): return 0\n# unknown\n"
    )
    zero = SourceUnit("alpha", "alpha.py", "zero = 0\n")
    unknown = SourceUnit("unknown", "unknown.py", "# unmeasured\n")
    run = ProfileRun(
        lines=[
            LineStats(
                SourceLocation(unit.id, 1),
                2000,
                hits=None if sampled else 2,
                samples=10 if sampled else None,
                ram=ProcessMemoryStats(delta_bytes=-512, peak_bytes=1024),
                gpu=GPUStats(time_ns=4000, peak_memory_bytes=2048),
            ),
            LineStats(
                SourceLocation(unit.id, 2),
                0,
                hits=None if sampled else 0,
                samples=0 if sampled else None,
                ram=ProcessMemoryStats(delta_bytes=0, peak_bytes=0),
                gpu=GPUStats(time_ns=0, peak_memory_bytes=0),
            ),
            LineStats(SourceLocation(zero.id, 1), 0, hits=0, samples=0 if sampled else None),
        ],
        functions=[
            FunctionStats(unit.id, "unknown", 3, line_count=None),
            FunctionStats(unit.id, "Alpha", 2, 0, calls=0, samples=0, line_count=1),
            FunctionStats(unit.id, "zulu", 1, 2000, calls=1234, samples=10, line_count=2),
        ],
    )
    return ProfileResult(
        run,
        {item.id: item for item in (unit, zero, unknown)},
        "scalene" if sampled else "trace",
        BackendCapabilities(
            sampled=sampled,
            sample_counts=sampled,
            hit_counts=not sampled,
            memory=True,
            gpu=True,
        ),
    )


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize("page_id", ["functions", "files"])
def test_summary_columns_sort_and_heat_defaults_to_none(page_id: str, *, sampled: bool) -> None:
    """Expose each column's sorter and disable summary heat on initial load.

    Parameters
    ----------
    page_id : str
        Summary page to inspect.

    sampled : bool
        Whether the report displays Samples instead of Calls.

    """
    document = ReportDOM(render_html(_profile(sampled=sampled))).root
    page = document.find_all("section", id=page_id)[0]
    header = page.find_all("div", css="summary-header")[0]
    assert header.children[-1].attributes["class"] == "source-controls"
    controls = header.find_all("button", css="source-heat")
    assert [button.attributes["data-heat"] for button in controls] == ["none", "time"]
    assert [button.attributes["aria-pressed"] for button in controls] == ["true", "false"]
    table = page.find_all("table", css="sortable-table")[0]
    headings = table.find_all("th")
    sorters = table.find_all("button", css="table-sort")
    assert len(headings) == len(sorters)
    expected_keys = (
        ["time", "line", "count", "name", "lines"]
        if page_id == "functions"
        else ["time", "name", *(["samples"] if sampled else []), "kind", "lines"]
    )
    assert [button.attributes["data-sort"] for button in sorters] == expected_keys
    for button in sorters:
        key = button.attributes["data-sort"]
        assert button.attributes["data-sort-label"] == button.text()
        icon = button.find_all("svg", css="table-sort-icon")[0]
        assert icon.attributes["aria-hidden"] == "true"
        assert icon.attributes["focusable"] == "false"
        assert len(icon.find_all("path")) == 3
        assert button.attributes["data-sort-type"] == (
            "text" if key in {"name", "kind"} else "number"
        )
        assert all(f"data-{key}" in row.attributes for row in table.find_all("tr", css="heat-row"))
    active = [heading for heading in headings if "aria-sort" in heading.attributes]
    assert len(active) == 1
    assert active[0].attributes["aria-sort"] == "descending"
    assert active[0].find_all("button")[0].attributes["data-sort"] == "time"


@pytest.mark.parametrize("sampled", [False, True])
def test_summary_rows_preserve_unknowns_zeroes_heat_and_navigation(*, sampled: bool) -> None:
    """Keep raw metrics, available heat, and definition links together.

    Parameters
    ----------
    sampled : bool
        Whether observation counts are available for file aggregation.

    """
    profile = _profile(sampled=sampled)
    document = ReportDOM(render_html(profile)).root
    functions = document.find_all("section", id="functions")[0].find_all("tr", css="heat-row")
    assert [row.attributes["data-name"] for row in functions] == ["zulu", "Alpha", "unknown"]
    assert [row.attributes["data-time"] for row in functions] == ["2000", "0", ""]
    assert [row.attributes["data-lines"] for row in functions] == ["2", "1", ""]
    assert [row.attributes["data-count"] for row in functions] == [
        "10" if sampled else "1234",
        "0",
        "",
    ]
    assert [row.attributes["data-heat-time"] for row in functions] == [
        "1.00000",
        "0.00000",
        "0.00000",
    ]
    files = document.find_all("section", id="files")[0].find_all("tr", css="heat-row")
    assert [row.attributes["data-name"] for row in files] == ["zeta.py", "alpha.py", "unknown.py"]
    assert [row.attributes["data-time"] for row in files] == ["2000", "0", ""]
    assert [row.attributes["data-heat-time"] for row in files] == ["1.00000", "0.00000", "0.00000"]
    for row in [*functions, *files]:
        assert row.attributes["style"] == "--heat:0.00000"
        for link in row.find_all("a"):
            assert document.find_all("tr", id=link.attributes["href"][1:])
    for page in document.find_all("section", css="source-page"):
        selected = page.find_all("button", css="source-heat", **{"aria-pressed": "true"})
        assert [button.attributes["data-heat"] for button in selected] == ["time"]
        for row in page.find_all("tr", css="source-row"):
            assert row.attributes["style"] == f"--heat:{row.attributes['data-heat-time']}"
    assert profile.root_run.functions[0].total_time_ns is None
    assert profile.root_run.functions[2].calls == 1234
    assert cell_values(functions[2])[0] == "—"


@pytest.mark.parametrize("sampled", [False, True])
def test_source_sorters_use_original_units_for_every_available_metric(*, sampled: bool) -> None:
    """Match every sortable source heading with an honest raw row value.

    Parameters
    ----------
    sampled : bool
        Whether Samples replaces Hits and Avg / hit.

    """
    document = ReportDOM(render_html(_profile(sampled=sampled))).root
    page = document.find_all("section", css="source-page")[0]
    assert not page.find_all("button", css="source-order-reset")
    assert "Reset order" not in page.text()
    table = page.find_all("table")[0]
    sorters = table.find_all("button", css="table-sort")
    assert [button.attributes["data-sort"] for button in sorters] == [
        "time",
        *(["samples"] if sampled else ["hits", "average"]),
        "memory",
        "peak",
        "gpu-time",
        "gpu-memory",
        "line",
    ]
    rows = table.find_all("tr", css="source-row")
    assert rows[0].attributes["data-memory"] == "-512"
    assert rows[0].attributes["data-peak"] == "1024"
    assert rows[0].attributes["data-gpu-time"] == "4000"
    assert rows[0].attributes["data-gpu-memory"] == "2048"
    assert rows[0].attributes["data-samples" if sampled else "data-hits"] == (
        "10" if sampled else "2"
    )
    if not sampled:
        assert rows[0].attributes["data-average"] == "1000"
    for button in sorters:
        key = button.attributes["data-sort"]
        assert button.attributes["data-sort-type"] == "number"
        assert f"data-{key}" in rows[-1].attributes
    assert rows[-1].attributes["data-time"] == ""
    assert rows[-1].attributes["data-memory"] == ""
    assert rows[-1].attributes["data-gpu-time"] == ""
    source = next(heading for heading in table.find_all("th") if heading.text() == "Source")
    assert source is table.find_all("thead")[0].find_all("th")[-1]
    assert source.attributes["aria-sort"] == "ascending"
    assert source.attributes["title"] == "Sort by line number"
    button = source.find_all("button")[0]
    assert button.text() == "Source"
    assert button.attributes["data-sort"] == "line"
    assert button.attributes["data-sort-direction"] == "ascending"
    assert button.find_all("svg", css="table-sort-icon")
    line_header = table.find_all("thead")[0].find_all("th")[0]
    assert line_header.attributes["aria-label"] == "Line number"
    assert not line_header.find_all("button")
    assert "aria-sort" not in line_header.attributes
