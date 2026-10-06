"""LineScope.

Author: Mavs
Description: Verify report column labels, sizing, and retained metrics.

"""

import re

import pytest

from linescope.enums import SourceKind
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
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
from linescope.notebooks.remote import notebook_sources
from linescope.render import render_html
from linescope.render.cell import render_cell_summary
from tests.test_render import ReportDOM, cell_values


@pytest.mark.parametrize("kind", [SourceKind.PYTHON, SourceKind.NOTEBOOK])
@pytest.mark.parametrize("backend", ["trace", "scalene", "custom-sampler"])
@pytest.mark.parametrize("spark", [False, True])
def test_shared_columns_keep_order_and_extra_metrics(
    kind: SourceKind, backend: str, *, spark: bool
) -> None:
    """Keep shared headings stable across collectors and source integrations.

    Retain tracing counts, memory, GPU, and Spark navigation before the final
    source column. Leave unsupported sampling counts unavailable in each view.

    Parameters
    ----------
    kind : [SourceKind]
        Python file or executed notebook cell snapshot.

    backend : str
        Trace collector, sampling collector, or sampler without counts.

    spark : bool
        Whether the profiled line also triggers a captured Spark action.

    """
    sampled = backend != "trace"
    known_samples = backend == "scalene"
    unit = SourceUnit(
        "source",
        "interactive · cell 2" if kind == SourceKind.NOTEBOOK else "/project/work.py",
        "def work():\n    return 42\n",
        kind,
    )
    location = SourceLocation(unit.id, 2)
    line = LineStats(
        location,
        wall_time_ns=1000,
        hits=None if sampled else 2,
        samples=3 if known_samples else None,
        memory=MemoryStats(delta_bytes=100),
        ram=ProcessMemoryStats(delta_bytes=200, peak_bytes=1000),
        gpu=GPUStats(time_ns=500, peak_memory_bytes=2000),
        spark_executions=["action"] if spark else [],
    )
    result = ProfileResult(
        ProfileRun(
            lines=[line],
            functions=[
                FunctionStats(
                    unit.id,
                    "work",
                    1,
                    total_time_ns=1000,
                    calls=None if sampled else 1,
                    samples=line.samples,
                    line_count=2,
                )
            ],
            spark_executions=[SparkExecution("action", location=location)] if spark else [],
        ),
        {unit.id: unit},
        backend,
        BackendCapabilities(
            hit_counts=not sampled,
            sampled=sampled,
            sample_counts=known_samples,
            memory=True,
            gpu=True,
        ),
    )
    document = ReportDOM(render_html(result)).root
    count_headers = ["Samples"] if sampled else []
    expected_headers = {
        "overview": ["Time", "Location", *count_headers, "Source"],
        "files": ["Time", "Location", *count_headers, "Kind", "Lines"],
        "functions": [
            "Time",
            "Location",
            "Samples" if sampled else "Calls",
            "Source",
            "Lines",
        ],
    }
    for page_id, expected in expected_headers.items():
        table = document.find_all("section", id=page_id)[0].find_all("table")[0]
        assert [header.text() for header in table.find_all("th")] == expected
        assert all(len(cell_values(row)) == len(expected) for row in table.find_all("tr")[1:])
        if sampled:
            assert cell_values(table.find_all("tr")[1])[2] == ("3" if known_samples else "—")

    shared = ["", "Time", *(["Samples"] if known_samples else [])]
    source_table = document.find_all("table", css="source-table")[0]
    extras = [
        *([] if sampled else ["Hits", "Avg / hit"]),
        "Mem Change",
        "Peak Mem",
        "Estimated GPU time",
        "GPU peak memory",
        *(["Context"] if spark else []),
    ]
    assert [header.text() for header in source_table.find_all("thead")[0].find_all("th")] == [
        *shared,
        *extras,
        "Source",
    ]
    source_row = source_table.find_all("tr", css="source-row")[1]
    values = cell_values(source_row)
    assert values[0:2] == ["2", "1.0 µs"]
    line_header = source_table.find_all("thead")[0].find_all("th")[0]
    assert line_header.attributes["class"] == "line-number"
    assert line_header.text() == ""
    assert not line_header.find_all("button")
    source_header = source_table.find_all("thead")[0].find_all("th")[-1]
    assert source_header.find_all("button")[0].attributes["aria-label"] == (
        "Sort by Source, descending"
    )
    assert values[-1] == "    return 42"
    assert values[len(shared) : -1] == [
        *([] if sampled else ["2", "<1 µs"]),
        "+200 B",
        "1.0 KB",
        "<1 µs",
        "2.0 KB",
        *(["Spark #action"] if spark else []),
    ]

    compact = ReportDOM(render_cell_summary(result, cell=unit)).root
    compact_extras = [
        *([] if sampled else ["Hits"]),
        "Mem Change",
        "Python retained",
        "Estimated GPU time",
    ]
    assert [header.text() for header in compact.find_all("th")] == [
        *shared,
        *compact_extras,
        "Source",
    ]
    assert compact.find_all("th")[0].attributes["aria-label"] == "Line number"
    compact_rows = compact.find_all("tbody")[0].find_all("tr")
    assert len(compact_rows) == 2
    assert cell_values(compact_rows[0])[-4:] == ["—", "—", "—", "def work():"]
    assert cell_values(compact_rows[1])[-4:] == [
        "+200 B",
        "+100 B",
        "<1 µs",
        "    return 42",
    ]

    function = document.find_all("section", id="functions")[0]
    links = function.find_all("tbody")[0].find_all("a")
    assert len(links) == 2
    assert links[0].attributes["href"] == links[1].attributes["href"]
    target = document.find_all("tr", id=links[0].attributes["href"][1:])[0]
    assert target.attributes["data-line"] == "1"


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize(
    "name",
    [
        "notebook_example.ipynb",
        "analysis_of_the_complete_notebook_example_with_a_<long>_name.ipynb",
    ],
)
def test_hotspot_locations_allow_content_sizing(name: str, *, sampled: bool) -> None:
    """Let complete notebook locations use available table width.

    Keep automatic column sizing and wrapping available instead of fixing the
    location width. Preserve the escaped name and exact cell navigation with
    either three or four overview columns.

    Parameters
    ----------
    name : str
        Notebook filename, including a long name with HTML-sensitive text.

    sampled : bool
        Whether a sample-count column separates location and source.

    """
    units = notebook_sources(name, "unused = 0\n# COMMAND ----------\nvalue = 1\n")
    unit = units[1]
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(unit.id, 1), 1000, samples=1)]),
        {item.id: item for item in units},
        "scalene" if sampled else "trace",
        BackendCapabilities(sampled=sampled, sample_counts=sampled),
    )
    document = ReportDOM(render_html(result)).root
    table = document.find_all("table", css="hot-lines")[0]
    row = table.find_all("tbody")[0].find_all("tr")[0]
    location = row.find_all("td")[1]
    assert location.text() == f"{name} · cell 2:1"
    assert not location.find_all("br")
    link = location.find_all("a")[0]
    target = document.find_all("tr", id=link.attributes["href"][1:])[0]
    assert target.attributes["data-line"] == "1"
    assert cell_values(row)[-1] == "value = 1"

    css = re.sub(r"/\*.*?\*/", "", document.find_all("style")[0].text(), flags=re.DOTALL)
    location_style = {}
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if any(
            selector.strip() in {".hot-lines th:nth-child(2)", ".hot-lines td:nth-child(2)"}
            for selector in selectors.split(",")
        ):
            location_style.update(
                declaration.split(":", 1) for declaration in declarations.split(";") if declaration
            )
    assert location_style.get("width", "auto") == "auto"
    assert location_style.get("white-space", "normal") == "normal"
    assert location_style.get("overflow-wrap") == "anywhere"


@pytest.mark.parametrize("sampled", [False, True])
def test_overview_times_use_plain_table_text(*, sampled: bool) -> None:
    """Display overview durations with the same text style as file summaries.

    Keep durations in ordinary table cells so they inherit the table's text
    color, font size, and normal weight in both report themes.

    Parameters
    ----------
    sampled : bool
        Whether the overview includes observation counts.

    """
    unit = SourceUnit("source", "work.py", "value = 1\n")
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(unit.id, 1), 1000, samples=1)]),
        {unit.id: unit},
        "scalene" if sampled else "trace",
        BackendCapabilities(sampled=sampled, sample_counts=sampled),
    )
    document = ReportDOM(render_html(result)).root
    for page_id in ("overview", "files"):
        table = document.find_all("section", id=page_id)[0].find_all("table")[0]
        row = table.find_all("tbody")[0].find_all("tr")[0]
        time = row.find_all("td")[0]
        assert time.children == ["1.0 µs"]
        assert not time.attributes
