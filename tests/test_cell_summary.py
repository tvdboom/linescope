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
    SourceKind,
    SourceLocation,
    SourceUnit,
    SparkExecution,
)
from linescope.notebooks.summary import cell_result
from linescope.render import render_html
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
    assert values == ["1", "—", "3", "—", "—", "answer = 42"]
    assert dom.find_all("td")[0].attributes["title"] == "cell:1"
    previous.lines[0].samples = 10
    assert (
        cell_result(current, previous, elapsed_ns=10, status=RunStatus.SUCCESS).root_run.lines
        == []
    )


@pytest.mark.parametrize("metric", ["wall_time_ns", "hits", "samples"])
def test_cell_delta_preserves_first_zero_observations(metric: str):
    """Retain known zero observations without reusing previous cell costs.

    Keep a new zero-valued line available to the renderer while omitting
    unchanged observations and navigation-only locations from earlier cells.

    Parameters
    ----------
    metric : str
        Available additive measurement whose first recorded value is zero.

    """
    source = SourceUnit("cell", "cell.py", "pass")
    prior = LineStats(SourceLocation("prior", 1), **{metric: 0})
    observed = LineStats(SourceLocation(source.id, 1), **{metric: 0})
    current = ProfileResult(
        ProfileRun(lines=[prior, observed, LineStats(SourceLocation("navigation", 1))]),
        {source.id: source},
        "trace" if metric == "hits" else "custom-sampler",
        BackendCapabilities(hit_counts=metric == "hits", sample_counts=metric == "samples"),
    )
    before = deepcopy(current)
    snapshot = cell_result(
        current, ProfileRun(lines=[deepcopy(prior)]), elapsed_ns=10, status=RunStatus.SUCCESS
    )
    assert snapshot.root_run.lines == [observed]
    document = ReportDOM(render_cell_summary(snapshot, cell=source)).root
    values = [element.text() for element in document.find_all("td")]
    assert values[1 if metric == "wall_time_ns" else 2] != "—"
    if metric != "wall_time_ns":
        assert values[2] == "0"
    assert current == before


def test_compact_renderer_shows_all_rows_and_escapes_source_origins():
    """Render complete source without introducing executable source markup.

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
    assert len(dom.find_all("tbody")[0].find_all("tr")) == 8
    table = dom.find_all("table")[0]
    assert table.attributes["aria-label"] == "LineScope cell 8: 8 source lines"
    assert [header.text() for header in table.find_all("th")] == ["", "Time", "Hits", "Source"]
    line_header = table.find_all("th")[0]
    assert line_header.attributes["aria-label"] == "Line number"
    assert "aria-sort" not in line_header.attributes
    assert not line_header.find_all("button")
    assert line_header.attributes["class"] == "ls-cell-line-number"
    source_header = table.find_all("th")[-1]
    assert source_header.attributes["aria-sort"] == "ascending"
    assert source_header.find_all("button")[0].attributes["data-sort"] == "line"
    assert "padding-left:6px;" in dom.find_all("style")[0].text()
    assert dom.find_all("code")[-1].text() == helper.source
    assert (
        dom.find_all("tbody")[0].find_all("tr")[-1].find_all("td")[0].attributes["title"]
        == "old cell:1"
    )
    assert dom.find_all("section")[0].attributes["data-status"] == "failed"
    assert not dom.find_all("header")
    assert not dom.find_all("h4")
    assert not dom.find_all("p")
    assert not dom.find_all("details")
    assert "diagnostic" not in html
    assert result.warnings == ['<script>alert("diagnostic")</script>']
    assert not dom.find_all("script")
    assert not dom.find_all("iframe")
    assert not dom.find_all("img")
    assert "alert" not in table.attributes["onclick"]
    assert len(result.root_run.lines) == 8
    selectors = dom.find_all("style")[0].text().split("}")
    assert all(
        selector.strip().startswith(".linescope-cell-summary")
        for selector in selectors
        if selector.strip()
    )


@pytest.mark.parametrize(
    "capabilities",
    [
        BackendCapabilities(),
        BackendCapabilities(hit_counts=True, memory=True, gpu=True),
        BackendCapabilities(sampled=True, sample_counts=True, memory=True, gpu=True),
        BackendCapabilities(hit_counts=True, sample_counts=True),
    ],
)
def test_compact_sorters_use_raw_values_and_restore_source_order(
    capabilities: BackendCapabilities,
):
    """Expose a matching sort arrow and raw value for every cell column.

    Preserve negative changes, known zeroes, unknown measurements, and escaped
    source text. Keep original row order separate from each source's line
    numbers so the Source arrow restores current-cell and called-source order.

    Parameters
    ----------
    capabilities : [BackendCapabilities]
        Available columns for deterministic or sampled collection.

    """
    cell = SourceUnit("cell", "cell.py", 'value = "<tag> & data"\n\nvalue += "z"\npass')
    helper = SourceUnit("helper", "helpers.py", "helper_value = 1")
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(
                    SourceLocation(cell.id, 1),
                    wall_time_ns=999,
                    hits=0,
                    samples=0,
                    ram=ProcessMemoryStats(delta_bytes=-2048),
                    memory=MemoryStats(delta_bytes=0),
                    gpu=GPUStats(time_ns=1_000_001),
                ),
                LineStats(SourceLocation(cell.id, 3), wall_time_ns=1000, hits=12, samples=2),
                LineStats(SourceLocation(helper.id, 1), wall_time_ns=0, hits=1, samples=1),
            ]
        ),
        {cell.id: cell, helper.id: helper},
        "custom",
        capabilities,
    )
    before = deepcopy(result)
    document = ReportDOM(render_cell_summary(result, cell=cell)).root
    table = document.find_all("table")[0]
    headers = table.find_all("th")
    sorters = table.find_all("button", css="table-sort")
    expected = ["time"]
    if capabilities.sample_counts:
        expected.append("samples")
    if capabilities.hit_counts:
        expected.append("hits")
    if capabilities.memory:
        expected.extend(("memory", "allocation"))
    if capabilities.gpu:
        expected.append("gpu-time")
    expected.append("line")
    assert [button.attributes["data-sort"] for button in sorters] == expected
    assert len(headers) == len(sorters) + 1
    assert not headers[0].find_all("button")
    assert sorters[-1].text() == "Source"
    assert headers[-1].attributes["title"] == "Sort by line number"
    assert [heading.attributes.get("aria-sort") for heading in headers] == [
        *([None] * (len(headers) - 1)),
        "ascending",
    ]
    rows = table.find_all("tbody")[0].find_all("tr")
    assert [row.attributes["data-order"] for row in rows] == ["0", "1", "2", "3"]
    assert [row.attributes["data-line"] for row in rows] == ["1", "3", "4", "1"]
    assert rows[0].attributes["data-time"] == "999"
    assert rows[1].attributes["data-time"] == "1000"
    assert rows[2].attributes["data-time"] == ""
    assert rows[3].attributes["data-time"] == "0"
    assert rows[0].attributes["data-hits"] == rows[0].attributes["data-samples"] == "0"
    assert rows[0].attributes["data-memory"] == "-2048"
    assert rows[0].attributes["data-allocation"] == "0"
    assert rows[0].attributes["data-gpu-time"] == "1000001"
    assert rows[0].attributes["data-source"] == cell.source.splitlines()[0]
    assert rows[1].attributes["data-gap-before"] == "1"
    assert rows[2].attributes["data-gap-before"] == rows[3].attributes["data-gap-before"] == "0"
    for button in sorters:
        key = button.attributes["data-sort"]
        assert button.attributes["type"] == "button"
        assert button.attributes["data-sort-type"] == "number"
        assert button.attributes["data-sort-direction"] == (
            "ascending" if key == "line" else "descending"
        )
        icon = button.find_all("svg", css="table-sort-icon")[0]
        assert icon.attributes["aria-hidden"] == "true"
        assert len(icon.find_all("path")) == 3
        assert all(f"data-{key}" in row.attributes for row in rows)
    assert not document.find_all("tag")
    assert not document.find_all("script")
    assert result == before


@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.FAILED])
@pytest.mark.parametrize(
    ("backend", "capabilities"),
    [
        ("trace", BackendCapabilities(hit_counts=True)),
        ("scalene", BackendCapabilities(sampled=True, sample_counts=True, memory=True)),
    ],
)
def test_unmeasured_cell_shows_source_and_unknown_metrics(
    status: RunStatus, backend: str, capabilities: BackendCapabilities
):
    """Display captured source when a cell has no line measurements.

    Short sampled cells and syntax failures can have no line observations.
    Retain source snapshots, execution outcomes, and diagnostics in the result.

    Parameters
    ----------
    status : [RunStatus]
        Outcome retained in the detached cell result.

    backend : str
        Collector name for sampled or deterministic measurements.

    capabilities : [BackendCapabilities]
        Supported columns whose measurements remain unknown for this cell.

    """
    source = SourceUnit("cell", "cell.py", "answer = 42")
    result = ProfileResult(
        ProfileRun(status=status),
        {source.id: source},
        backend,
        capabilities,
        warnings=["No samples collected."],
    )
    before = deepcopy(result)
    document = ReportDOM(render_cell_summary(result, cell=source, execution_count=8)).root
    rows = document.find_all("tbody")[0].find_all("tr")
    assert len(rows) == 1
    values = [element.text() for element in rows[0].find_all("td")]
    assert values[:2] == ["1", "—"]
    assert values[-1] == "answer = 42"
    assert all(value == "—" for value in values[2:-1])
    assert rows[0].attributes["style"] == "--heat:0.00000"
    assert document.find_all("section")[0].attributes["data-status"] == status
    assert "No samples collected." not in document.text()
    assert result == before


def test_missing_cell_source_and_measurements_produce_no_summary():
    """Suppress output when neither cell source nor observations are available.

    Unrelated cumulative session snapshots must not become the current cell's
    source or measurements.

    """
    result = ProfileResult(
        ProfileRun(),
        {"prior": SourceUnit("prior", "prior.py", "old_value = 1")},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    assert render_cell_summary(result) == ""


def test_compact_source_preserves_long_lines_and_indentation():
    """Retain complete source text in the last column without truncation.

    Preserve indentation, quotes, and markup characters in long source lines
    while keeping paths out of the visible metric columns.

    """
    text = '    payload = "' + '<tag key="value">&data</tag> ' * 80 + '"'
    unit = SourceUnit("helper", "project/<helpers>.py", text)
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(unit.id, 1), wall_time_ns=1000, hits=1)]),
        {unit.id: unit},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    document = ReportDOM(render_cell_summary(result)).root
    cells = document.find_all("tbody")[0].find_all("td")
    assert cells[-1].find_all("code")[0].text() == text
    assert cells[0].text() == "1"
    assert cells[0].attributes["title"] == "project/<helpers>.py:1"
    assert cells[0].attributes["aria-label"] == "project/<helpers>.py:1"
    assert not document.find_all("tag")
    style = document.find_all("style")[0].text()
    assert "white-space:pre;" in style
    assert "overflow-x:auto" in style
    assert "text-overflow:ellipsis" not in style


@pytest.mark.parametrize("metric", ["wall_time_ns", "samples", "hits"])
def test_compact_all_lines_follow_source_line_order(metric: str):
    """Display every source line in ascending line-number order.

    Include unmeasured lines and keep observed source text and metrics together
    without changing the input result.

    Parameters
    ----------
    metric : str
        Available measurement attached to a subset of source lines.

    """
    unit = SourceUnit("cell", "cell.py", "\n".join(f"value = {i}" for i in range(1, 29)))
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(unit.id, number), **{metric: cost})
                for number, cost in [(28, 60), (19, 50), (25, 40), (27, 30), (5, 20), (1, 10)]
            ]
        ),
        {unit.id: unit},
        "trace" if metric == "hits" else "custom-sampler",
        BackendCapabilities(
            hit_counts=metric == "hits",
            sample_counts=metric == "samples",
            sampled=metric != "hits",
        ),
    )
    before = deepcopy(result)
    document = ReportDOM(render_cell_summary(result, cell=unit)).root
    rows = document.find_all("tbody")[0].find_all("tr")
    assert [int(row.find_all("td")[0].text()) for row in rows] == list(range(1, 29))
    assert [row.find_all("code")[0].text() for row in rows] == [
        f"value = {number}" for number in range(1, 29)
    ]
    if metric != "wall_time_ns":
        assert [
            rows[number - 1].find_all("td")[2].text() for number in [1, 5, 19, 25, 27, 28]
        ] == ["10", "20", "50", "40", "30", "60"]
        assert all(row.find_all("td")[1].text() == "—" for row in rows)
    assert rows[1].find_all("td")[1].text() == "—"
    assert result == before


def test_compact_keeps_zero_samples_and_called_source_context():
    """Show zero counts and relevant source context with honest unknowns.

    Preserve indentation in both the current cell and called project source,
    while omitting blank and comment-only rows. Keep unrelated snapshots
    outside this cell's output and leave its metrics untouched.

    """
    cell = SourceUnit("cell", "cell.py", "# comment\n\nanswer = helper()\npass")
    helper = SourceUnit("helper", "helpers.py", "def helper():\n    return 42\n# context")
    unrelated = SourceUnit("unrelated", "prior.py", "prior_value = 1")
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(helper.id, 2), wall_time_ns=100, samples=2),
                LineStats(SourceLocation(cell.id, 4), wall_time_ns=0, samples=0),
            ]
        ),
        {unit.id: unit for unit in (unrelated, helper)},
        "custom-sampler",
        BackendCapabilities(sampled=True, sample_counts=True, memory=True, gpu=True),
    )
    before = deepcopy(result)
    document = ReportDOM(render_cell_summary(result, cell=cell)).root
    rows = document.find_all("tbody")[0].find_all("tr")
    assert [row.find_all("code")[0].text() for row in rows] == [
        *cell.source.splitlines()[2:],
        *helper.source.splitlines()[:2],
    ]
    assert [row.find_all("td")[0].text() for row in rows] == ["3", "4", "1", "2"]
    assert not document.find_all("tr", css="ls-cell-gap")
    assert rows[1].find_all("td")[2].text() == "0"
    assert rows[1].find_all("td")[1].text() != "—"
    assert rows[1].attributes["style"] == "--heat:0.00000"
    assert [element.text() for element in rows[0].find_all("td")] == [
        "3",
        "—",
        "—",
        "—",
        "—",
        "—",
        "answer = helper()",
    ]
    assert rows[3].find_all("td")[0].attributes["title"] == "helpers.py:2"
    assert "prior_value" not in document.text()
    assert result == before


@pytest.mark.parametrize("blank_count", [1, 2, 30])
def test_compact_collapses_source_gaps_without_adding_table_rows(blank_count: int):
    """Mark each internal source gap with one compact row border.

    Collapse whitespace runs of any length and mark omitted comments too.
    Preserve measured and unknown code rows, source transitions, and the full
    report without adding leading or trailing separators.

    Parameters
    ----------
    blank_count : int
        Number of whitespace-only lines between the first two code rows.

    """
    text = "\n".join(
        [
            "",
            " \t",
            "first = 1",
            *([" \t"] * blank_count),
            "second = 2",
            "third = 3",
            "# omitted comment",
            "fourth = 4",
            "",
            " ",
        ]
    )
    cell = SourceUnit("cell", "cell.py", text)
    helper = SourceUnit("helper", 'helpers/<code "path">.py', "\n" * (blank_count + 20) + "pass")
    result = ProfileResult(
        ProfileRun(
            lines=[
                *(
                    LineStats(SourceLocation(cell.id, number), wall_time_ns=0, hits=0)
                    for number in range(1, len(text.splitlines()) + 1)
                    if number != blank_count + 5
                ),
                LineStats(SourceLocation(helper.id, blank_count + 21), wall_time_ns=100, hits=1),
            ]
        ),
        {cell.id: cell, helper.id: helper},
        "trace",
        BackendCapabilities(hit_counts=True, memory=True, gpu=True),
    )
    before = deepcopy(result)
    document = ReportDOM(render_cell_summary(result, cell=cell)).root
    rows = document.find_all("tbody")[0].find_all("tr")
    assert len(rows) == 5
    assert [row.find_all("td")[0].text() for row in rows] == [
        "3",
        str(blank_count + 4),
        str(blank_count + 5),
        str(blank_count + 7),
        str(blank_count + 21),
    ]
    assert [row.find_all("code")[0].text() for row in rows] == [
        "first = 1",
        "second = 2",
        "third = 3",
        "fourth = 4",
        "pass",
    ]
    gaps = document.find_all("tr", css="ls-cell-gap")
    assert gaps == [rows[1], rows[3]]
    assert all(len(row.find_all("td")) == 7 for row in rows)
    assert rows[1].find_all("td")[0].attributes["title"] == (
        f"{blank_count} source line{'s' if blank_count != 1 else ''} "
        f"omitted before line {blank_count + 4}"
    )
    assert rows[3].find_all("td")[0].attributes["aria-label"] == (
        f"1 source line omitted before line {blank_count + 7}"
    )
    assert rows[4].find_all("td")[0].attributes["title"] == (f"{helper.path}:{blank_count + 21}")
    assert rows[1].find_all("td")[2].text() == "0"
    assert rows[2].find_all("td")[2].text() == "—"
    assert (
        document.find_all("table")[0].attributes["aria-label"] == "LineScope cell: 5 source lines"
    )
    full_rows = ReportDOM(render_html(result)).root.find_all("tr", css="source-row")
    assert len(full_rows) == len(text.splitlines()) + len(helper.source.splitlines())
    assert result == before


@pytest.mark.parametrize(
    ("text", "visible"),
    [
        ('"""Module docs.\n\nText.\n"""\n# comment\nanswer = 42  # keep\n', [6]),
        (
            (
                'class Example:\n    """Class docs."""\n    async def read(self):\n'
                '        """Method docs.\n\n        Details.\n        """\n'
                '        def nested():\n            """Nested docs."""\n'
                "            return 42\n        return nested()\n"
            ),
            [1, 3, 8, 10, 11],
        ),
        ('payload = """Data.\n# data\n\nmore data\n"""\nlabel = "# text"\n', [1, 2, 4, 5, 6]),
        ('def café(): """Function docs."""; return 42\nclass Small: """Class docs."""\n', [1, 2]),
        ('"""Module docs.\nDetails.\n"""; answer = 42\n', [3]),
        ("(\n    'Module docs.'\n    'More docs.'\n)\nanswer = 42\n", [5]),
        ('answer = 42\n"""A runtime string expression."""\n', [1, 2]),
        ("values = [\n    # comment\n    42,  # keep\n]\n\n", [1, 3, 4]),
        ("# comment\nanswer = (\n", [2]),
        ('%time answer = 42\n"""Module docs."""\n# comment\nanswer += 1\n', [1, 4]),
    ],
)
def test_compact_hides_blank_comment_and_docstring_rows(text: str, visible: list[int]):
    """Filter documentation without dropping code, data, or original numbers.

    Keep observed hidden lines out of fallback rows and retain the complete
    source snapshot and measurements for the full report. Visible zero-sample
    lines remain distinguishable from unobserved lines.

    Parameters
    ----------
    text : str
        Captured Python or notebook source, including multiline strings.

    visible : list[int]
        Original one-based line numbers containing visible source.

    """
    cell = SourceUnit("cell", "cell", text, kind=SourceKind.NOTEBOOK)
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(cell.id, number), wall_time_ns=0, samples=0)
                for number in range(1, len(text.splitlines()) + 1)
            ]
        ),
        {cell.id: cell},
        "custom-sampler",
        BackendCapabilities(sampled=True, sample_counts=True),
    )
    before = deepcopy(result)
    rows = (
        ReportDOM(render_cell_summary(result, cell=cell)).root.find_all("tbody")[0].find_all("tr")
    )
    assert [int(row.find_all("td")[0].text()) for row in rows] == visible
    assert [row.find_all("code")[0].text() for row in rows] == [
        text.splitlines()[number - 1] for number in visible
    ]
    assert all(row.find_all("td")[2].text() == "0" for row in rows)
    assert "Unavailable" not in "".join(row.text() for row in rows)
    complete = ReportDOM(render_html(result)).root.find_all("tr", css="source-row")
    assert [int(row.attributes["data-line"]) for row in complete] == list(
        range(1, len(text.splitlines()) + 1)
    )
    assert result == before


@pytest.mark.parametrize("text", ["", "\n \t\n", "# comment\n\n", '"""Only documentation."""'])
def test_compact_empty_or_documentation_only_cell_has_no_table(text: str):
    """Suppress a compact table when every row is blank or documentation.

    Preserve even observed documentation lines in the normalized result.

    Parameters
    ----------
    text : str
        Empty, blank, comment-only, or docstring-only source text.

    """
    cell = SourceUnit("cell", "cell.py", text)
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(cell.id, 1), hits=1)]),
        {cell.id: cell},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    assert render_cell_summary(result, cell=cell) == ""
    assert len(result.root_run.lines) == 1


def test_compact_retains_measurements_with_unavailable_source():
    """Keep observed metrics visible when source capture is incomplete.

    Label unknown snapshots and out-of-range locations without fabricating
    source text or dropping their measurements.

    """
    cell = SourceUnit("cell", "cell.py", "answer = 42")
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(cell.id, 9), samples=0),
                LineStats(SourceLocation("missing", 3), samples=1),
            ]
        ),
        {cell.id: cell},
        "custom-sampler",
        BackendCapabilities(sampled=True, sample_counts=True),
    )
    document = ReportDOM(render_cell_summary(result, cell=cell)).root
    rows = document.find_all("tbody")[0].find_all("tr")
    assert [row.find_all("code")[0].text() for row in rows] == [
        "answer = 42",
        "Unavailable",
        "Unavailable",
    ]
    assert [row.find_all("td")[2].text() for row in rows] == ["—", "0", "1"]


def test_compact_time_heat_matches_full_report_and_leaves_unknowns_uncolored():
    """Reuse report heat intensity without inventing missing line timings.

    Scale measured lines within the cell and leave unknown, zero, and negative
    durations uncolored even when sample counts are available.

    """
    unit = SourceUnit("cell", "cell.py", "\n".join(f"value = {i}" for i in range(5)))
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(unit.id, i), wall_time_ns=duration, samples=1)
                for i, duration in enumerate([None, 0, 1_000_000, 100_000_000, -1], 1)
            ]
        ),
        {unit.id: unit},
        "custom-sampler",
        BackendCapabilities(sampled=True, sample_counts=True),
    )
    compact = ReportDOM(render_cell_summary(result, cell=unit)).root
    complete = ReportDOM(render_html(result)).root
    full_heat = {
        row.attributes["data-line"]: row.attributes["data-heat-time"]
        for row in complete.find_all("tr", css="source-row")
    }
    heat = {}
    for row in compact.find_all("tbody")[0].find_all("tr"):
        number = row.find_all("td")[0].text()
        intensity = row.attributes["style"].removeprefix("--heat:")
        assert intensity == full_heat[number]
        heat[number] = float(intensity)
    assert heat["4"] == 1
    assert 0 < heat["3"] < heat["4"]
    assert heat["1"] == heat["2"] == heat["5"] == 0
    assert compact.find_all("td")[1].text() == "—"
    assert "—" in compact.find_all("tbody")[0].text()
    assert "#dc2626" in compact.find_all("style")[0].text()
