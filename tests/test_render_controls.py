"""LineScope.

Author: Mavs
Description: Verify report column sorting metadata and optional summary heat.

"""

import re

import pytest

from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    GPUStats,
    LineStats,
    MemorySample,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
    SparkExecutionStats,
    SparkOperator,
)
from linescope.render.html import render_html
from tests.test_render import ReportDOM, cell_values


def _css_declarations(css: str, selector: str) -> dict[str, str]:
    """Read the first exact selector rule from embedded report styles.

    Parameters
    ----------
    css : str
        Self-contained stylesheet embedded in the generated report.

    selector : str
        Complete selector whose layout declarations should be inspected.

    Returns
    -------
    dict[str, str]
        Property values, or an empty mapping when the rule is absent.

    """
    rule = re.search(rf"(?<![\w.-]){re.escape(selector)}\{{([^}}]*)\}}", css)
    if rule is None:
        return {}

    return dict(declaration.split(":", 1) for declaration in rule[1].split(";") if declaration)


@pytest.mark.parametrize("selector", ["html", "body", ".workspace", "main", ".page"])
def test_report_pages_leave_vertical_scrolling_to_the_document(selector: str):
    """Allow long reports to reach the browser's outer scrollbar.

    Parameters
    ----------
    selector : str
        Report ancestor that must grow with the active summary page.

    """
    css = ReportDOM(render_html(_profile())).root.find_all("style")[0].text()
    declarations = _css_declarations(css, selector)

    assert declarations
    assert declarations.get("height", "auto") == "auto"
    assert declarations.get("max-height", "none") == "none"
    assert declarations.get("overflow", "visible") == "visible"
    assert declarations.get("overflow-y", "visible") == "visible"


@pytest.mark.parametrize("selector", [".spark-plan-overview>.table-scroll", ".spark-cost-scroll"])
def test_spark_tables_expand_without_nested_vertical_scrollbars(selector: str):
    """Keep every Spark step and operator in the page's scrolling flow.

    Parameters
    ----------
    selector : str
        Spark table wrapper that previously clipped rows to a fixed height.

    """
    css = ReportDOM(render_html(_profile())).root.find_all("style")[0].text()
    declarations = _css_declarations(css, selector)

    assert declarations.get("max-height", "none") == "none"
    assert declarations.get("overscroll-behavior", "auto") == "auto"
    assert _css_declarations(css, ".table-scroll")["overflow"] == "auto"


def test_source_navigation_keeps_its_viewport_and_spark_links_keep_their_alignment():
    """Preserve source scrolling and align Spark targets after a page reset.

    Reset the document before scrolling to a selected operator so its
    disclosure remains visible when browser scrolling owns the report.

    """
    document = ReportDOM(render_html(_profile())).root
    css = document.find_all("style")[0].text()
    javascript = document.find_all("script")[0].text()

    assert _css_declarations(css, ".source-view")["height"] == "100dvh"
    assert _css_declarations(css, ".source-view")["overflow"] == "hidden"
    assert _css_declarations(css, ".source-page")["overflow"] == "hidden"
    assert _css_declarations(css, ".source-scroll")["overflow"] == "auto"
    assert re.search(r"@media\s+screen\s*\{\s*\.source-view\s*\{", css)
    assert (
        "classList.toggle('source-view', selected.classList.contains('source-page'))" in javascript
    )
    assert javascript.index("window.scrollTo(0, 0)") < javascript.index("target.scrollIntoView(")


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


def _table_profile(*, sampled: bool = False) -> ProfileResult:
    """Populate every report table with controlled numeric and text values.

    Parameters
    ----------
    sampled : bool, default=False
        Whether the hot-line table includes a Samples column.

    Returns
    -------
    ProfileResult
        Spark, memory, child-notebook, and metadata tables with zero and
        unavailable measurements and escaped source names.

    """
    result = _profile(sampled=sampled)
    result.root_run.memory_samples = [MemorySample(0, 1024)]
    result.root_run.metadata = {"detail <&>": "value <&>", "unknown": None}
    result.root_run.lines.append(
        LineStats(SourceLocation("missing", 99), ram=ProcessMemoryStats(delta_bytes=0))
    )
    scan = SparkOperator("scan", "Scan <&>", metrics={"numOutputRows": 12})
    operation = SparkOperator(
        "sort",
        "Sort",
        metrics={"time_ns": 1_000_000, "peak_memory_bytes": 1024, "spill_bytes": 0},
        children=[scan],
    )
    pipeline = SparkOperator(
        "pipeline", "WholeStageCodegen (1)", metrics={"time_ns": 2_000_000}, children=[operation]
    )
    result.root_run.spark_executions = [
        SparkExecution(
            "measured",
            "collect <&>",
            SourceLocation("zeta", 2),
            SparkExecutionStats(
                wall_time_ns=1_000_000_000,
                executor_time_ns=2_000_000_000,
                peak_memory_bytes=0,
                spill_bytes=1024,
            ),
            operators=[pipeline],
        ),
        SparkExecution("unknown", "unknown"),
        SparkExecution("zero", "zero", stats=SparkExecutionStats(wall_time_ns=0)),
    ]
    result.root_run.children = [
        ProfileRun(name="child <&>", metadata={"parent_wait_time_ns": 0}, status="failed")
    ]
    return result


@pytest.mark.parametrize("sampled", [False, True])
def test_every_report_table_exposes_sortable_columns_with_raw_values(*, sampled: bool):
    """Expose arrows and corresponding row values in every report table.

    Parameters
    ----------
    sampled : bool
        Whether to include the sampled hot-line layout.

    """
    document = ReportDOM(render_html(_table_profile(sampled=sampled))).root
    tables = document.find_all("table")
    assert len(tables) > 15
    assert not document.find_all("button", css="spark-order")
    assert "Highest first" not in document.text()
    for table in tables:
        assert "sortable-table" in table.attributes["class"].split()
        for heading in table.find_all("thead")[0].find_all("th"):
            if heading.attributes.get("aria-label") == "Line number":
                continue
            button = heading.find_all("button", css="table-sort")[0]
            assert button.text() == heading.text()
            assert button.find_all("svg", css="table-sort-icon")
            key = button.attributes["data-sort"]
            rows = [row for body in table.find_all("tbody") for row in body.find_all("tr")]
            assert all(
                f"data-{key}" in row.attributes
                for row in rows
                if "source-cell-heading" not in row.attributes.get("class", "").split()
            )


def test_plan_steps_preserve_data_flow_and_separate_shared_sort_values():
    """Keep step order and pipeline ownership while adding numeric sorters.

    Shared timings remain unavailable on their unmeasured constituent steps.

    """
    document = ReportDOM(render_html(_table_profile())).root
    steps = document.find_all("table", css="spark-steps")[0]
    headers = steps.find_all("thead")[0].find_all("th")
    assert headers[0].attributes["aria-sort"] == "ascending"
    assert [button.attributes["data-sort-type"] for button in steps.find_all("button")] == [
        "number",
        "text",
        "number",
        "number",
        "number",
    ]
    rows = steps.find_all("tbody")[0].find_all("tr")
    assert [row.attributes["data-column-0"] for row in rows] == ["1", "2"]
    assert [row.attributes["data-column-2"] for row in rows] == ["", "1000000"]
    assert [row.attributes["data-column-4"] for row in rows] == ["12", "12"]
    shared = next(table for table in document.find_all("table") if "Shared steps" in table.text())
    assert shared.find_all("tbody")[0].find_all("tr")[0].attributes["data-column-1"] == "2000000"
    for link in steps.find_all("a"):
        assert document.find_all("details", id=link.attributes["href"][1:])


def test_memory_and_invocation_tables_keep_negative_zero_and_missing_values():
    """Sort by bytes and parent wait while retaining unavailable source.

    A missing snapshot must remain renderable beside freed memory and zero.

    """
    document = ReportDOM(render_html(_table_profile())).root
    memory = document.find_all("table", css="memory-growth")[0]
    rows = memory.find_all("tbody")[0].find_all("tr")
    assert [row.attributes["data-column-0"] for row in rows] == ["0", "0", "-512"]
    assert rows[1].attributes["data-column-1"] == ""
    assert rows[1].attributes["data-column-2"] == ""
    assert rows[1].attributes["data-column-3"] == ""
    assert rows[2].attributes["data-column-3"] == "def zulu(): return 1"
    invocations = document.find_all("table", css="notebook-invocations")[0]
    row = invocations.find_all("tbody")[0].find_all("tr")[0]
    assert row.attributes["data-column-0"] == "child <&>"
    assert row.attributes["data-column-1"] == "0"
    assert row.attributes["data-column-2"] == "failed"
    assert document.find_all("section", id=row.find_all("a")[0].attributes["href"][1:])


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize("page_id", ["functions", "files"])
def test_summary_columns_sort_and_heat_defaults_to_none(page_id: str, *, sampled: bool):
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
def test_summary_rows_preserve_unknowns_zeroes_heat_and_navigation(*, sampled: bool):
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
def test_source_sorters_use_original_units_for_every_available_metric(*, sampled: bool):
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
