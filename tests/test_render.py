"""LineScope.

Author: Mavs
Description: Portable report content, escaping, metrics, and navigation
contracts.

"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
import re

import pytest

from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
    SparkExecutionStats,
    SparkOperator,
    SymbolRef,
)
from linescope.render import render_html


@dataclass
class Element:
    """Provide element.

    Small test-only DOM supporting semantic HTML assertions.

    Attributes
    ----------
    tag : str
        HTML tag name, or `document` for the synthetic root.

    attributes : dict[str, str | None]
        Parsed HTML attributes used by semantic assertions.

    children : list[Element | str]
        Nested elements and text nodes in document order.

    """

    tag: str
    attributes: dict[str, str | None] = field(default_factory=dict)
    children: list[Element | str] = field(default_factory=list)

    def text(self):
        """Provide the controlled behavior used by this test.

        Collect nested element text in document order for semantic assertions.

        """
        return "".join(
            child.text() if isinstance(child, Element) else child for child in self.children
        )

    def find_all(self, tag=None, *, css=None, **attributes):
        """Find descendant elements matching the requested semantic selectors.

        Filter descendants by tag, CSS class, and explicit HTML attributes.

        """
        result = []
        for child in self.children:
            if not isinstance(child, Element):
                continue
            if (
                (tag is None or child.tag == tag)
                and (css is None or css in (child.attributes.get("class") or "").split())
                and all(child.attributes.get(key) == value for key, value in attributes.items())
            ):
                result.append(child)
            result.extend(child.find_all(tag, css=css, **attributes))
        return result


class ReportDOM(HTMLParser):
    """Provide report dom.

    Parse report markup without requiring a browser or third-party parser.

    Attributes
    ----------
    root : Element
        Synthetic document element containing all parsed report markup.

    stack : list[Element]
        Open elements used to attach parsed child elements and text.

    """

    def __init__(self, html):
        """Initialize the controlled test state and recorded observations.

        Retain only the state needed to observe arguments, results, and cleanup
        in the surrounding test.

        """
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        """Attach a parsed element and retain non-void nesting state.

        Treat void elements as complete so later content attaches to the correct
        parent.

        """
        element = Element(tag, dict(attributes))
        self.stack[-1].children.append(element)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(element)

    def handle_endtag(self, tag):
        """Close the matching parsed element and its nested stack entries.

        Retain earlier open ancestors when closing a nested element.

        """
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        """Append parsed text to the current report element.

        Preserve source spelling for escaping and metric label assertions.

        """
        self.stack[-1].children.append(data)


@pytest.fixture
def result():
    """Provide result.

    A normalized profile with two independently linked calls on one line.

    """
    unit = SourceUnit(
        "source:///project/main.py",
        "/project/main.py",
        (
            "def foo(value): return value\ndef bar(value): return value\nresult ="
            " foo(bar(3))\n# UNEXECUTED_SOURCE_MARKER\n"
        ),
    )
    line = LineStats(
        SourceLocation(unit.id, 3),
        wall_time_ns=120_000_000,
        hits=2,
        calls=[
            SymbolRef("foo", 3, 9, 12, SourceLocation(unit.id, 1, symbol="foo")),
            SymbolRef("bar", 3, 13, 16, SourceLocation(unit.id, 2, symbol="bar")),
        ],
    )
    run = ProfileRun(
        id="root",
        elapsed_ns=1_000_000_000,
        lines=[line],
        functions=[
            FunctionStats(unit.id, "foo", 1, 70_000_000, 1),
            FunctionStats(unit.id, "bar", 2, 50_000_000, 1),
        ],
    )
    return ProfileResult(run, {unit.id: unit}, "trace", BackendCapabilities(hit_counts=True))


def parse(result):
    """Parse rendered report markup into the lightweight assertion DOM.

    Keep navigation targets and table structure available to semantic checks.

    """
    return ReportDOM(render_html(result)).root


def source_rows(document):
    """Return rendered source table rows from the parsed report.

    Retain DOM elements so cell values and source anchors can be inspected
    separately.

    """
    return document.find_all("tr", css="source-row")


def cell_values(row):
    """Collect visible table cell text for metric assertions.

    Keep displayed unknown markers distinct from measured zeroes.

    """
    return [cell.text() for cell in row.children if isinstance(cell, Element) and cell.tag == "td"]


class TestSourceReport:
    """Check source report.

    Full-source presentation and optional metric behavior.

    """

    def test_full_source_including_unexecuted_lines(self, result):
        """Verify full source including unexecuted lines.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        rows = source_rows(document)
        unit = next(iter(result.sources.values()))
        assert len(rows) == len(unit.source.splitlines())
        assert [
            row.find_all("td", css="source-code")[0].text() for row in rows
        ] == unit.source.splitlines()
        assert cell_values(rows[3])[1:4] == ["—", "0", "—"]
        assert "UNEXECUTED_SOURCE_MARKER" in rows[3].text()

    def test_hits_average_and_heat(self, result):
        """Verify hits average and heat.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        rows = source_rows(parse(result))
        assert cell_values(rows[2])[1:4] == ["120.00 ms", "2", "60.00 ms"]
        assert rows[2].attributes["style"] == "--heat:1.00000"
        assert rows[3].attributes["style"] == "--heat:0.00000"

    @pytest.mark.parametrize("multiplier", [1, 1000])
    def test_heat_keeps_smaller_hotspots_visible_without_changing_times(self, result, multiplier):
        """Verify heat keeps smaller hotspots visible without changing times.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = SourceUnit("heat", "heat.py", "small()\nmedium()\nlarge()\nslowest()\n")
        durations = [1000, 10_000, 100_000, 1_000_000]
        result.sources = {unit.id: unit}
        result.root_run.lines = [
            LineStats(SourceLocation(unit.id, number), duration * multiplier, hits=1)
            for number, duration in enumerate(durations, 1)
        ]
        rows = source_rows(parse(result))
        heat = [float(row.attributes["style"].removeprefix("--heat:")) for row in rows]

        assert 0.09 < heat[0] < 0.11
        assert 0.3 < heat[1] < 0.4
        assert 0.6 < heat[2] < 0.7
        assert heat[3] == 1
        assert heat == sorted(heat)
        assert [cell_values(row)[1] for row in rows] == (
            ["1.0 µs", "10.0 µs", "100.0 µs", "1.00 ms"]
            if multiplier == 1
            else ["1.00 ms", "10.00 ms", "100.00 ms", "1.00 s"]
        )

    def test_heat_uses_the_same_scale_across_source_files(self, result):
        """Verify heat uses the same scale across source files.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = SourceUnit("second", "second.py", "medium()\nsmall()\n")
        result.sources[unit.id] = unit
        result.root_run.lines.extend(
            [
                LineStats(SourceLocation(unit.id, 1), 120_000_000),
                LineStats(SourceLocation(unit.id, 2), 1_200_000),
            ]
        )
        rows = source_rows(parse(result))

        assert rows[2].attributes["style"] == rows[4].attributes["style"]
        assert 0.3 < float(rows[5].attributes["style"].removeprefix("--heat:")) < 0.4

    @pytest.mark.parametrize("duration", [None, 0])
    def test_zero_and_unavailable_times_have_no_heat(self, result, duration):
        """Verify zero and unavailable times have no heat.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.lines[0].wall_time_ns = duration
        rows = source_rows(parse(result))

        assert all(row.attributes["style"] == "--heat:0.00000" for row in rows)
        assert cell_values(rows[2])[1] == ("—" if duration is None else "0 µs")

    @pytest.mark.parametrize("multiplier", [1, 1024])
    def test_memory_heat_scales_growth_without_coloring_frees_or_unknowns(
        self,
        result,
        multiplier,
    ):
        """Keep growth hotspots comparable across files without changing data.

        Check a logarithmic memory scale independently of time, including
        memory-only lines, decreases, zeroes, and unavailable readings.

        """
        unit = SourceUnit(
            "growth",
            "growth.py",
            "small()\nmedium()\nlarge()\npeak()\nfree()\nzero()\nunknown()\n",
        )
        second = SourceUnit("second", "second.py", "medium()\n")
        result.sources = {unit.id: unit, second.id: second}
        result.capabilities = BackendCapabilities(memory=True)
        deltas = [1000, 10_000, 100_000, 1_000_000, -2_000_000, 0, None]
        result.root_run.lines = [
            LineStats(
                SourceLocation(unit.id, number),
                ram=ProcessMemoryStats(delta_bytes=None if delta is None else delta * multiplier),
            )
            for number, delta in enumerate(deltas, 1)
        ]
        result.root_run.lines.append(
            LineStats(
                SourceLocation(second.id, 1),
                ram=ProcessMemoryStats(delta_bytes=10_000 * multiplier),
            )
        )
        rows = source_rows(parse(result))
        heat = [float(row.attributes["data-heat-memory"]) for row in rows]

        assert 0.09 < heat[0] < 0.11
        assert 0.3 < heat[1] < 0.4
        assert 0.6 < heat[2] < 0.7
        assert heat[3] == 1
        assert heat[4:7] == [0, 0, 0]
        assert heat[7] == heat[1]
        assert all(row.attributes["style"] == "--heat:0.00000" for row in rows)
        assert [row.attributes["data-memory"] for row in rows[:7]] == [
            "" if delta is None else str(delta * multiplier) for delta in deltas
        ]
        assert all(row.attributes["data-time"] == "" for row in rows)

    @pytest.mark.parametrize("delta", [None, 0, -1024])
    def test_memory_heat_has_no_color_without_positive_growth(self, result, delta):
        """Leave all rows uncolored when the report has no measured growth.

        Preserve missing values and signed measurements while avoiding an
        undefined heat scale for an empty or nonpositive maximum.

        """
        result.capabilities = BackendCapabilities(memory=True)
        result.root_run.lines[0].ram = ProcessMemoryStats(delta_bytes=delta)
        rows = source_rows(parse(result))
        assert all(row.attributes["data-heat-memory"] == "0.00000" for row in rows)

    def test_memory_heat_ignores_hidden_and_out_of_snapshot_measurements(self, result):
        """Scale growth using only rows with visible memory measurements.

        Keep unavailable child capabilities and invalid source locations
        from dimming the report's actual hotspots.

        """
        unit = next(iter(result.sources.values()))
        hidden = SourceUnit("hidden", "hidden.py", "work()\n")
        result.sources[hidden.id] = hidden
        result.capabilities = BackendCapabilities(memory=True)
        result.root_run.lines[0].ram = ProcessMemoryStats(delta_bytes=1000)
        result.root_run.lines.extend(
            LineStats(
                SourceLocation(source_id, line), ram=ProcessMemoryStats(delta_bytes=1_000_000)
            )
            for source_id, line in [(unit.id, 999), ("missing", 1)]
        )
        result.root_run.children = [
            ProfileRun(
                source=hidden,
                lines=[
                    LineStats(
                        SourceLocation(hidden.id, 1), ram=ProcessMemoryStats(delta_bytes=1_000_000)
                    )
                ],
                metadata={"child_capabilities": {"memory": False}},
            )
        ]
        rows = source_rows(parse(result))
        assert rows[2].attributes["data-heat-memory"] == "1.00000"
        assert rows[-1].attributes["data-heat-memory"] == "0.00000"
        assert rows[-1].attributes["data-memory"] == ""

    def test_source_colors_follow_explicit_and_system_dark_themes(self, result):
        """Verify source colors follow explicit and system dark themes.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        css = parse(result).find_all("style")[0].text()
        root = re.search(r":root\{([^}]+)\}", css).group(1)
        dark = re.search(r"body\.dark\{([^}]+)\}", css).group(1)
        system = re.search(r"body:not\(\.light\):not\(\.dark\)\{([^}]+)\}", css).group(1)
        row = re.search(r"\.source-row\{([^}]+)\}", css).group(1)

        assert "var(--panel)" in row
        assert "color:var(--ink)" in row
        assert "--code:" not in row
        for variable in (
            "source-muted",
            "source-link",
            "syntax-keyword",
            "syntax-string",
            "syntax-number",
            "syntax-comment",
        ):
            light_value = re.search(rf"--{variable}:([^;}}]+)", root).group(1)
            dark_value = re.search(rf"--{variable}:([^;}}]+)", dark).group(1)

            assert light_value != dark_value
            assert f"--{variable}:{dark_value}" in system
            assert f"var(--{variable})" in css
        assert ".source-row .tok-" not in css

    def test_symbol_links_are_colored_and_underlined(self, result):
        """Verify symbol links are colored and underlined.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        css = parse(result).find_all("style")[0].text()
        symbol = re.search(r"\.symbol\{([^}]+)\}", css).group(1)

        assert "color:var(--source-link)" in symbol
        assert "text-decoration:underline" in symbol
        assert "font-weight:600" in symbol

    @pytest.mark.parametrize("line_count", [1, 400])
    def test_source_scroll_region_keeps_title_and_count_outside(self, result, line_count):
        """Verify source scroll region keeps title and count outside.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = next(iter(result.sources.values()))
        result.sources[unit.id] = replace(
            unit, source="\n".join(f"value = {number}" for number in range(line_count))
        )
        page = parse(result).find_all("section", css="source-page")[0]
        header = page.find_all("div", css="source-header")[0]
        title = page.find_all("h1", css="path-title")[0]
        summary = header.find_all("div", css="source-summary")[0]
        count = summary.find_all("p", css="muted")[0]
        scroller = page.find_all("div", css="source-scroll")[0]

        assert scroller.attributes["tabindex"] == "0"
        assert scroller.attributes["role"] == "region"
        assert scroller.attributes["aria-labelledby"] == title.attributes["id"]
        assert header in page.children
        assert title in header.children
        assert summary in header.children
        assert count in summary.children
        assert count.text() == f"{line_count} lines"
        assert not scroller.find_all("h1")
        assert not scroller.find_all("p")
        assert len(scroller.find_all("table", css="source-table")) == 1
        assert len(source_rows(scroller)) == line_count

    @pytest.mark.parametrize(
        ("backend", "capabilities", "count_header"),
        [
            ("trace", BackendCapabilities(hit_counts=True), "Calls"),
            ("scalene", BackendCapabilities(sampled=True, sample_counts=True), "Samples"),
            ("tachyon", BackendCapabilities(sampled=True, sample_counts=True), "Samples"),
            ("custom", BackendCapabilities(sampled=True), "Samples"),
        ],
    )
    @pytest.mark.parametrize("count", [None, 0, 1234])
    def test_function_count_column_follows_collection_method(
        self,
        result: ProfileResult,
        backend: str,
        capabilities: BackendCapabilities,
        count_header: str,
        count: int | None,
    ) -> None:
        """Place the collector's function count before the definition link.

        Preserve unknown counts, measured zeroes, and descending self-time
        order for tracing, sampling, and custom collectors.

        Parameters
        ----------
        result : ProfileResult
            Controlled trace profile with two function definitions.

        backend : str
            Collector name displayed by the report.

        capabilities : BackendCapabilities
            Collection method and supported count measurements.

        count_header : str
            Expected label for the second column.

        count : int | None
            Recorded count, including unavailable and measured-zero cases.

        """
        result.backend = backend
        result.capabilities = capabilities
        for function in result.root_run.functions:
            function.calls = None if capabilities.sampled else count
            function.samples = count if capabilities.sampled else None
        result.root_run.functions.reverse()
        document = parse(result)
        table = document.find_all("section", id="functions")[0].find_all("table")[0]

        assert [header.text() for header in table.find_all("th")] == [
            "Self time",
            count_header,
            "Function",
        ]
        rows = table.find_all("tr")[1:]
        expected_count = "—" if count is None else f"{count:,}"
        assert [cell_values(row) for row in rows] == [
            ["70.00 ms", expected_count, "foo()"],
            ["50.00 ms", expected_count, "bar()"],
        ]
        for row, line in zip(rows, [1, 2], strict=True):
            link = row.find_all("a")[0]
            assert document.find_all("tr", id=link.attributes["href"][1:])[0].attributes[
                "data-line"
            ] == str(line)

    def test_mixed_function_counts_keep_traced_calls_unavailable(
        self, result: ProfileResult
    ) -> None:
        """Keep tracing calls out of the mixed report's Samples column.

        Use sampled child measurements to select Samples and retain unknown
        markers for the traced parent functions.

        Parameters
        ----------
        result : ProfileResult
            Controlled trace profile with two counted functions.

        """
        unit = SourceUnit("child", "child.py", "def sampled():\n    return 1\n")
        result.sources[unit.id] = unit
        result.root_run.children = [
            ProfileRun(
                source=unit,
                lines=[LineStats(SourceLocation(unit.id, 2), 90_000_000, samples=23)],
                functions=[FunctionStats(unit.id, "sampled", 1, 90_000_000, samples=23)],
                metadata={
                    "child_backend": "scalene",
                    "child_capabilities": {"sampled": True, "sample_counts": True},
                },
            )
        ]
        table = parse(result).find_all("section", id="functions")[0].find_all("table")[0]

        assert [header.text() for header in table.find_all("th")] == [
            "Self time",
            "Samples",
            "Function",
        ]
        assert [cell_values(row) for row in table.find_all("tr")[1:]] == [
            ["90.00 ms", "23", "sampled()"],
            ["70.00 ms", "—", "foo()"],
            ["50.00 ms", "—", "bar()"],
        ]
        assert result.root_run.functions[0].calls == 1

    def test_sampled_values_never_fabricate_hits(self, result):
        """Verify sampled values never fabricate hits.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.capabilities = BackendCapabilities(sampled=True, hit_counts=False)
        result.root_run.lines[0].hits = None
        document = parse(result)
        rows = source_rows(document)
        assert [cell.text() for cell in rows[2].find_all("td", css="metric")] == ["120.00 ms"]
        assert [cell.text() for cell in rows[3].find_all("td", css="metric")] == ["—"]
        table = document.find_all("table", css="source-table")[0]
        assert [header.text() for header in table.find_all("th")] == [
            "Line",
            "Estimated time",
            "Source",
        ]
        assert result.root_run.lines[0].hits is None
        assert "Estimated time" in document.text()
        header = document.find_all("header", css="topbar")[0]
        assert header.find_all("span", css="header-value")[-1].text() == "Sampling"
        assert "Observed lines" in document.text()

    @pytest.mark.parametrize("backend", ["scalene", "tachyon"])
    def test_sampling_columns_show_observations_without_hit_columns(self, result, backend):
        """Verify sampling columns show observations without hit columns.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.backend = backend
        result.capabilities = BackendCapabilities(sampled=True, sample_counts=True)
        result.root_run.lines[0].hits = None
        result.root_run.lines[0].samples = 3
        table = parse(result).find_all("table", css="source-table")[0]

        assert [header.text() for header in table.find_all("th")] == [
            "Line",
            "Estimated time",
            "Samples",
            "Source",
        ]
        rows = source_rows(table)
        assert cell_values(rows[2])[:3] == ["3", "120.00 ms", "3"]
        assert cell_values(rows[3])[:3] == ["4", "—", "0"]
        assert all(len(cell_values(row)) == 4 for row in rows)

    def test_trace_columns_show_hits_without_sample_columns(self, result):
        """Verify trace columns show hits without sample columns.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        table = parse(result).find_all("table", css="source-table")[0]

        assert [header.text() for header in table.find_all("th")] == [
            "Line",
            "Python time",
            "Hits",
            "Avg / hit",
            "Source",
        ]
        assert all(len(cell_values(row)) == 5 for row in source_rows(table))

    @pytest.mark.parametrize("context", ["none", "spark", "notebook", "both"])
    def test_context_columns_follow_links_in_each_source(self, result, context):
        """Verify context columns follow links in each source.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = next(iter(result.sources.values()))
        unrelated = SourceUnit("unrelated", "unrelated.py", "value = 1\n")
        result.sources[unrelated.id] = unrelated
        # Captured integrations alone should not create empty context columns.
        result.root_run.spark_executions = [SparkExecution("execution")]
        result.root_run.children = [ProfileRun(id="child", name="Child notebook")]
        if context in {"spark", "both"}:
            result.root_run.lines[0].spark_executions = ["execution"]
        if context in {"notebook", "both"}:
            result.root_run.lines[0].notebook_runs = ["child"]
        document = parse(result)
        tables = document.find_all("table", css="source-table")
        table = next(table for table in tables if unit.source.splitlines()[0] in table.text())
        other = next(table for table in tables if "value = 1" in table.text())
        headers = [header.text() for header in table.find_all("th")]

        assert ("Context" in headers) is (context != "none")
        assert len(table.find_all("td", css="references")) == (4 if context != "none" else 0)
        assert all(len(cell_values(row)) == len(headers) for row in source_rows(table))
        assert "Context" not in [header.text() for header in other.find_all("th")]
        assert not other.find_all("td", css="references")
        references = source_rows(table)[2].find_all("td", css="references")
        if context != "none":
            badges = references[0].find_all("a")
            assert [badge.text() for badge in badges] == {
                "spark": ["Spark #executio"],
                "notebook": ["Notebook"],
                "both": ["Spark #executio", "Notebook"],
            }[context]
            ids = {
                node.attributes["id"] for node in document.find_all() if "id" in node.attributes
            }
            assert all(badge.attributes["href"][1:] in ids for badge in badges)

    def test_missing_values_differ_from_measured_zero(self, result):
        """Verify missing values differ from measured zero.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.lines[0].wall_time_ns = 0
        rows = source_rows(parse(result))
        assert cell_values(rows[2])[1] == "0 µs"
        assert cell_values(rows[3])[1] == "—"

    @pytest.mark.parametrize("memory", [False, True])
    def test_line_order_controls_default_to_line_number(self, result, memory):
        """Verify line order controls default to line number.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.capabilities = BackendCapabilities(hit_counts=True, memory=memory)
        page = parse(result).find_all("section", css="source-page")[0]
        header = page.find_all("div", css="source-header")[0]
        summary = header.find_all("div", css="source-summary")[0]
        toolbars = summary.find_all("div", css="source-controls")[0]
        heat_toolbar, toolbar = toolbars.find_all("div", css="source-toolbar")
        group = toolbar.find_all("div", css="source-order-controls")[0]
        controls = group.find_all("button", css="source-order")

        assert header.children[0].tag == "h1"
        assert summary in header.children
        assert summary.children[0].text() == "4 lines"
        assert toolbars in summary.children
        assert heat_toolbar in toolbars.children
        assert toolbar in toolbars.children
        assert toolbar.find_all("span")[0].text() == "Order lines by"
        assert group.attributes["role"] == "group"
        assert group.attributes["aria-label"] == "Order source lines"
        assert [control.attributes["data-order"] for control in controls] == (
            ["line", "time", "memory"] if memory else ["line", "time"]
        )
        assert [control.attributes["aria-pressed"] for control in controls] == (
            ["true", "false", "false"] if memory else ["true", "false"]
        )
        assert [control.text() for control in controls] == (
            ["Line number", "Time", "Mem Growth"] if memory else ["Line number", "Time"]
        )
        heat_group = heat_toolbar.find_all("div", css="source-heat-controls")[0]
        heat_controls = heat_group.find_all("button", css="source-heat")
        assert heat_toolbar.find_all("span")[0].text() == "Heatmap by"
        assert heat_group.attributes["role"] == "group"
        assert heat_group.attributes["aria-label"] == "Color source lines"
        assert [control.attributes["data-heat"] for control in heat_controls] == (
            ["time", "memory"] if memory else ["time"]
        )
        assert [control.text() for control in heat_controls] == (
            ["Time", "Mem Growth"] if memory else ["Time"]
        )
        assert [control.attributes["aria-pressed"] for control in heat_controls] == (
            ["true", "false"] if memory else ["true"]
        )
        assert not page.find_all("div", css="source-scroll")[0].find_all("button")
        assert [row.attributes["data-line"] for row in source_rows(page)] == ["1", "2", "3", "4"]

    @pytest.mark.parametrize("duration", [None, 0, 999, 1_234_567_890])
    @pytest.mark.parametrize("delta", [None, 0, -1024, 2048])
    def test_sort_values_keep_raw_units_and_unknown_measurements(self, result, duration, delta):
        """Verify sort values keep raw units and unknown measurements.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.capabilities = BackendCapabilities(hit_counts=True, memory=True)
        result.root_run.lines[0].wall_time_ns = duration
        result.root_run.lines[0].ram = ProcessMemoryStats(4096, delta, 8192)
        rows = source_rows(parse(result))
        assert rows[2].attributes["data-time"] == ("" if duration is None else str(duration))
        assert rows[2].attributes["data-memory"] == ("" if delta is None else str(delta))
        assert rows[3].attributes["data-time"] == ""
        assert rows[3].attributes["data-memory"] == ""

    def test_driver_memory_optional_and_signed(self, result):
        """Verify driver memory optional and signed.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.lines[0].ram = ProcessMemoryStats(2 * 1024**2, -1024, 4 * 1024**2)
        assert "Mem Change" not in parse(result).text()
        result.capabilities = BackendCapabilities(memory=True, hit_counts=True)
        document = parse(result)
        assert "Mem Change" in document.text()
        assert cell_values(source_rows(document)[2])[4:6] == ["-1.0 KB", "4.2 MB"]
        assert cell_values(source_rows(document)[3])[4:6] == ["—", "—"]

    @pytest.mark.parametrize(
        ("value", "change", "peak"),
        [
            (None, "—", "—"),
            (0, "0 B", "0 B"),
            (999, "+999 B", "999 B"),
            (1_000, "+1.0 KB", "1.0 KB"),
            (999_000, "+999.0 KB", "999.0 KB"),
            (1_000_000, "+1.0 MB", "1.0 MB"),
            (999_000_000, "+999.0 MB", "999.0 MB"),
            (1_000_000_000, "+1.0 GB", "1.0 GB"),
            (-1_000, "-1.0 KB", "1.0 KB"),
            (-1_000_000, "-1.0 MB", "1.0 MB"),
            (-1_000_000_000, "-1.0 GB", "1.0 GB"),
        ],
    )
    def test_memory_columns_use_decimal_units_without_changing_raw_bytes(
        self,
        result,
        value,
        change,
        peak,
    ):
        """Display decimal memory units while preserving raw byte measurements.

        Check unit boundaries, signed decreases, zeroes, and unknown readings
        through the rendered source table and its sorting attributes.

        """
        result.capabilities = BackendCapabilities(memory=True, hit_counts=True)
        result.root_run.lines[0].ram = ProcessMemoryStats(
            delta_bytes=value, peak_bytes=abs(value) if value is not None else None
        )
        row = source_rows(parse(result))[2]
        assert cell_values(row)[4:6] == [change, peak]
        assert row.attributes["data-memory"] == ("" if value is None else str(value))
        assert result.root_run.lines[0].ram.delta_bytes == value

    def test_disabled_memory_is_not_exposed_for_sorting(self, result):
        """Verify disabled memory is not exposed for sorting.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.lines[0].ram = ProcessMemoryStats(2048, 1024, 4096)
        assert all(row.attributes["data-memory"] == "" for row in source_rows(parse(result)))
        assert all(
            row.attributes["data-heat-memory"] == "0.00000" for row in source_rows(parse(result))
        )

    def test_two_calls_have_distinct_link_targets(self, result):
        """Verify two calls have distinct link targets.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        rows = source_rows(document)
        links = rows[2].find_all("a", css="symbol")
        assert [link.text() for link in links] == ["foo", "bar"]
        assert links[0].attributes["href"] == "#" + rows[0].attributes["id"]
        assert links[1].attributes["href"] == "#" + rows[1].attributes["id"]
        assert links[0].attributes["href"] != links[1].attributes["href"]

    def test_function_and_class_links_target_exact_definitions_in_another_file(self, result):
        """Check the expected behavior in this regression case.

        Verify function and class links target exact definitions in another
        file.

        """
        definitions = SourceUnit(
            "definitions",
            "definitions.py",
            "# context\n" * 98 + "class Widget: pass\ndef build(): pass\n",
        )
        caller = SourceUnit("caller", "caller.py", "Widget(build())\n")
        result.sources = {caller.id: caller, definitions.id: definitions}
        result.root_run.lines = [
            LineStats(
                SourceLocation(caller.id, 1),
                calls=[
                    SymbolRef("Widget", 1, 0, 6, SourceLocation(definitions.id, 99)),
                    SymbolRef("build", 1, 7, 12, SourceLocation(definitions.id, 100)),
                ],
            )
        ]
        rows = source_rows(parse(result))
        links = rows[0].find_all("a", css="symbol")

        assert [link.text() for link in links] == ["Widget", "build"]
        assert [link.attributes["href"] for link in links] == [
            "#" + rows[99].attributes["id"],
            "#" + rows[100].attributes["id"],
        ]
        assert rows[99].find_all("td", css="source-code")[0].text() == "class Widget: pass"
        assert rows[100].find_all("td", css="source-code")[0].text() == "def build(): pass"

    def test_unicode_links_preserve_original_spelling(self, result):
        """Verify unicode links preserve original spelling.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = SourceUnit("unicode", "café.py", "def café(): pass\n'🍋'; café()\n")
        result.sources = {unit.id: unit}
        result.root_run.lines = [
            LineStats(
                SourceLocation(unit.id, 2),
                calls=[SymbolRef("café", 2, 5, 9, SourceLocation(unit.id, 1))],
            )
        ]
        result.root_run.functions = []
        rows = source_rows(parse(result))
        assert rows[1].find_all("a", css="symbol")[0].text() == "café"
        assert rows[1].find_all("td", css="source-code")[0].text() == "'🍋'; café()"

    @pytest.mark.parametrize(
        "source",
        [
            "",
            "# comment\n\n",
            "%%profile\nprint(1)\n",
            "def invalid(:\n  broken\n",
            "value = '''multi\nline'''\n",
        ],
    )
    def test_empty_notebook_and_invalid_python_sources(self, result, source):
        """Verify empty notebook and invalid python sources.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = SourceUnit("virtual", "notebook", source, "notebook")
        result.sources = {unit.id: unit}
        result.root_run.lines = []
        result.root_run.functions = []
        rows = source_rows(parse(result))
        assert len(rows) == max(1, len(source.splitlines()))
        assert [row.find_all("td", css="source-code")[0].text().rstrip() for row in rows] == [
            line.rstrip() for line in (source.splitlines() or [""])
        ]

    def test_snapshots_do_not_read_original_files(self, result, tmp_path):
        """Verify snapshots do not read original files.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        path = tmp_path / "captured.py"
        path.write_text("CHANGED_ON_DISK = True\n")
        unit = SourceUnit(str(path), str(path), "ORIGINAL_SNAPSHOT = True\n")
        result.sources = {unit.id: unit}
        result.root_run.lines = []
        result.root_run.functions = []
        rendered = render_html(result)
        assert "ORIGINAL_SNAPSHOT" in rendered
        assert "CHANGED_ON_DISK" not in rendered
        path.unlink()
        assert "ORIGINAL_SNAPSHOT" in render_html(result)

    @pytest.mark.parametrize(
        "path",
        ["/project/package/main.py", "../../package/main.py", r"C:\project\package\main.py"],
    )
    def test_python_labels_show_only_filename(self, result, tmp_path, path):
        """Verify python labels show only filename.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = next(iter(result.sources.values()))
        result.sources[unit.id] = SourceUnit(unit.id, path, unit.source)
        document = ReportDOM(render_html(result, root=tmp_path)).root
        assert document.find_all("h1", css="path-title")[0].text() == "main.py"
        source_item = document.find_all("a", css="source-item")[0]
        assert source_item.attributes["title"] == "main.py"
        files = document.find_all("section", id="files")[0]
        assert files.find_all("a")[0].text() == "main.py"
        assert result.sources[unit.id].path == path

    def test_same_filename_keeps_distinct_source_links(self, result):
        """Verify same filename keeps distinct source links.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        first = SourceUnit("first", "/project/first/main.py", "first = 1\n")
        second = SourceUnit("second", "/project/second/main.py", "second = 2\n")
        result.sources = {first.id: first, second.id: second}
        result.root_run.lines = []
        result.root_run.functions = []
        document = parse(result)
        links = document.find_all("a", css="source-item")
        assert [link.attributes["title"] for link in links] == ["main.py", "main.py"]
        targets = [link.attributes["href"] for link in links]
        assert targets[0] != targets[1]
        rows = source_rows(document)
        assert targets == [f"#{row.attributes['id']}" for row in rows]

    def test_empty_profile_remains_usable(self):
        """Verify empty profile remains usable.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        empty = ProfileResult(ProfileRun(), {}, "custom", BackendCapabilities())
        document = parse(empty)
        assert "No source captured" in document.text()
        assert document.find_all("section", id="overview")
        assert len(document.find_all("script")) == 1


class TestReportNavigation:
    """Check report navigation.

    Hash routes, source symbols, child notebooks, and Spark context.

    """

    def test_every_internal_link_resolves_and_ids_are_unique(self, result):
        """Verify every internal link resolves and ids are unique.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        nodes = document.find_all()
        ids = [node.attributes["id"] for node in nodes if "id" in node.attributes]
        assert len(ids) == len(set(ids))
        for link in document.find_all("a"):
            target = link.attributes.get("href", "")
            if "header-detail" in (link.attributes.get("class") or "").split():
                continue
            assert target.startswith("#")
            assert target[1:] in ids

    def test_top_level_views_and_source_anchor_routes(self, result):
        """Verify top level views and source anchor routes.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        sidebar = document.find_all("aside", css="sidebar")[0]
        assert sidebar.find_all("div", css="run-label")[0].text() == "REPORT NAVIGATION"
        assert {link.attributes["href"] for link in document.find_all("a", css="nav-link")} == {
            "#overview",
            "#files",
            "#functions",
        }
        assert not document.find_all("section", id="notebooks")
        assert not document.find_all("section", id="spark")
        assert "Spark executions" not in document.text()
        first_line = source_rows(document)[0].attributes["id"]
        assert document.find_all("a", css="source-item")[0].attributes["href"] == f"#{first_line}"

    def test_report_has_no_footer(self, result):
        """Verify report has no footer.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        assert not document.find_all("footer")
        assert "Python driver profiling · self-contained HTML" not in document.text()

    @pytest.mark.parametrize("collection", ["trace", "sampled", "mixed"])
    def test_overview_contains_only_summary_and_expensive_lines(self, result, collection):
        """Verify overview contains only summary and expensive lines.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.warnings = ["Trace instrumentation increases execution overhead."]
        if collection == "sampled":
            result.backend = "scalene"
            result.capabilities = BackendCapabilities(sampled=True)
        elif collection == "mixed":
            result.root_run.children = [ProfileRun(metadata={"child_backend": "scalene"})]

        document = parse(result)
        overview = document.find_all("section", id="overview")[0]

        assert overview.find_all("h1")[0].text() == "Overview"
        assert [heading.text() for heading in overview.find_all("h2")] == ["Most expensive lines"]
        assert overview.find_all("div", css="stats")
        assert len(overview.find_all("table")) == 1
        assert overview.find_all("table", css="hot-lines")
        assert not overview.find_all("p", css="semantics")
        assert not overview.find_all("details")
        assert result.warnings[0] not in overview.text()
        assert "View all functions" not in overview.text()
        functions = document.find_all("section", id="functions")[0]
        assert "foo()" in functions.text()
        assert "bar()" in functions.text()

    @pytest.mark.parametrize("notebook_context", ["none", "snapshot", "child"])
    @pytest.mark.parametrize("spark_context", ["none", "root", "nested"])
    def test_optional_views_follow_captured_context(self, result, notebook_context, spark_context):
        """Verify optional views follow captured context.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        if notebook_context == "snapshot":
            unit = SourceUnit("cell", "Cell 1", "value = 1\n", "notebook")
            result.sources[unit.id] = unit
        elif notebook_context == "child":
            # A child invocation can be captured without access to its source.
            result.root_run.children = [ProfileRun(name="Child notebook")]

        if spark_context == "root":
            result.root_run.spark_executions = [SparkExecution("execution")]
        elif spark_context == "nested":
            grandchild = ProfileRun(spark_executions=[SparkExecution("execution")])
            result.root_run.children.append(ProfileRun(children=[grandchild]))

        document = parse(result)
        views = {link.attributes["href"] for link in document.find_all("a", css="nav-link")}
        has_notebooks = notebook_context != "none" or spark_context == "nested"
        has_spark = spark_context != "none"
        assert ("#notebooks" in views) == has_notebooks
        assert bool(document.find_all("section", id="notebooks")) == has_notebooks
        assert ("#spark" in views) == has_spark
        assert bool(document.find_all("section", id="spark")) == has_spark
        assert ("Spark executions" in document.text()) == has_spark
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_nested_child_notebooks_have_reachable_views(self, result):
        """Verify nested child notebooks have reachable views.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        child_source = SourceUnit(
            "notebook://child", "/Workspace/child", "# child snapshot\n", "notebook"
        )
        grand_source = SourceUnit(
            "notebook://grand", "/Workspace/grand", "# grandchild snapshot\n", "notebook"
        )
        grand = ProfileRun(
            id="grand",
            parent_id="child",
            source=grand_source,
            name="Grandchild",
            elapsed_ns=20_000_000,
        )
        child = ProfileRun(
            id="child",
            parent_id="root",
            source=child_source,
            name="Child",
            elapsed_ns=70_000_000,
            children=[grand],
            metadata={"collection": "parent wait only"},
        )
        result.root_run.children = [child]
        result.root_run.lines[0].notebook_runs = [child.id]
        result.sources.update({child_source.id: child_source, grand_source.id: grand_source})
        document = parse(result)
        assert "Grandchild" in document.text()
        assert "Parent wait:" in document.text()
        assert "parent wait only" in document.text()
        badge = source_rows(document)[2].find_all("a", css="badge")[0]
        child_page = document.find_all("section", id=badge.attributes["href"][1:])[0]
        assert "Child" in child_page.text()
        assert child_page.find_all("a")[0].attributes["href"].endswith("-L1")
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_spark_plans_and_metrics_are_distinct(self, result):
        """Verify spark plans and metrics are distinct.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        line = result.root_run.lines[0]
        execution = SparkExecution(
            "spark-12",
            "Write parquet",
            line.location,
            SparkExecutionStats(
                wall_time_ns=2_000_000_000,
                executor_time_ns=12_000_000_000,
                rows=400,
                shuffle_read_bytes=2**20,
                peak_memory_bytes=2**30,
            ),
            operators=[
                SparkOperator(
                    "join",
                    "HashJoin",
                    "keys = id",
                    {"rows": 400},
                    children=[SparkOperator("scan", "Scan parquet", metrics={"bytes": 4096})],
                )
            ],
            executed_plan="FINAL AQE EXECUTED PLAN",
            initial_plan="INITIAL PHYSICAL PLAN",
            parsed_plan="PARSED LOGICAL PLAN",
            analyzed_plan="ANALYZED LOGICAL PLAN",
            optimized_plan="OPTIMIZED LOGICAL PLAN",
            stages=[{"id": 7, "tasks": 12, "duration_p50_ns": 100_000_000}],
            jobs=[1, 2],
        )
        result.root_run.spark_executions = [execution]
        line.spark_executions = [execution.id]
        document = parse(result)
        badge = source_rows(document)[2].find_all("a", css="spark")[0]
        page = document.find_all("section", id=badge.attributes["href"][1:])[0]
        text = page.text()
        assert "Cumulative executor time" in text
        assert "12.00 s" in text
        assert "Wall time" in text
        assert "2.00 s" in text
        assert "Executor peak memory" in text
        assert "1.1 GB" in text
        assert "HashJoin" in text
        assert "Scan parquet" in text
        assert "Stage 7" in text
        views = page.find_all("pre", css="plan-view")
        assert len(views) == 5
        assert views[0].text() == "FINAL AQE EXECUTED PLAN"
        assert "hidden" not in views[0].attributes
        assert all("hidden" in view.attributes for view in views[1:])
        plans = page.find_all("details", css="spark-plans")[0]
        assert "open" not in plans.attributes
        assert len(plans.find_all("pre", css="plan-view")) == 5
        assert text.index("Most expensive operators") < text.index("FINAL AQE EXECUTED PLAN")
        assert not document.find_all("p", css="semantics")
        assert not document.find_all("p", css="spark-metric-note")
        assert not document.find_all("p", css="spark-limitation")
        assert cell_values(source_rows(document)[2])[1] == "120.00 ms"
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_multiple_spark_references_per_line(self, result):
        """Verify multiple spark references per line.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.spark_executions = [SparkExecution("first"), SparkExecution("second")]
        result.root_run.lines[0].spark_executions = ["first", "second"]
        document = parse(result)
        badges = source_rows(document)[2].find_all("a", css="spark")
        assert len(badges) == 2
        assert badges[0].attributes["href"] != badges[1].attributes["href"]

    @pytest.mark.parametrize(
        ("path", "kind", "label"),
        [
            ("/project/jobs/main.py", "python", "main.py:3"),
            (r"C:\project\jobs\main.py", "python", "main.py:3"),
            ('/project/action<&".py', "python", 'action<&".py:3'),
            ("Cell 7", "notebook", "Cell 7:3"),
            ('/Workspace/ETL <&"/Cell 7', "notebook", '/Workspace/ETL <&"/Cell 7:3'),
        ],
    )
    def test_spark_list_and_detail_link_to_exact_trigger(self, result, path, kind, label):
        """Verify spark list and detail link to exact trigger.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        unit = next(iter(result.sources.values()))
        result.sources[unit.id] = replace(unit, path=path, kind=kind)
        execution = SparkExecution("action", "collect", SourceLocation(unit.id, 3))
        result.root_run.spark_executions = [execution]
        document = parse(result)
        spark = document.find_all("section", id="spark")[0]
        assert [header.text() for header in spark.find_all("th")] == [
            "Action",
            "Source",
            "Wall time",
            "Executor time",
            "Peak memory",
            "Spill",
        ]
        cells = spark.find_all("td")
        assert cell_values(spark.find_all("tbody")[0].find_all("tr")[0]) == [
            "collect #action",
            f"{label}result = foo(bar(3))",
            "—",
            "—",
            "—",
            "—",
        ]
        source_link = cells[1].find_all("a")[0]
        target = source_rows(document)[2]
        assert source_link.attributes["href"] == f"#{target.attributes['id']}"
        assert "foo" in target.find_all("td", css="source-code")[0].text()
        detail = document.find_all("section", id=cells[0].find_all("a")[0].attributes["href"][1:])[
            0
        ]
        assert detail.find_all("a")[0].text() == label
        assert detail.find_all("a")[0].attributes["href"] == source_link.attributes["href"]
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_spark_triggers_keep_same_named_files_and_child_runs_distinct(self, result):
        """Verify spark triggers keep same named files and child runs distinct.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        first = SourceUnit(
            "first", "/project/first/job.py", "frame.filter('id > 0')\nframe.collect()\n"
        )
        second = SourceUnit("second", "/project/second/job.py", "frame.collect()\n")
        result.sources.update({first.id: first, second.id: second})
        result.root_run.spark_executions = [
            SparkExecution("first-action", "collect", SourceLocation(first.id, 2)),
        ]
        result.root_run.children = [
            ProfileRun(
                spark_executions=[
                    SparkExecution("child-action", "collect", SourceLocation(second.id, 1))
                ]
            )
        ]
        # Transformation references cannot replace the recorded action trigger.
        result.root_run.lines.append(
            LineStats(SourceLocation(first.id, 1), spark_executions=["first-action"])
        )
        document = parse(result)
        spark = document.find_all("section", id="spark")[0]
        rows = spark.find_all("tbody")[0].find_all("tr")
        links = [row.find_all("td")[1].find_all("a")[0] for row in rows]
        assert [link.text() for link in links] == ["job.py:2", "job.py:1"]
        targets = [document.find_all("tr", id=link.attributes["href"][1:])[0] for link in links]
        assert targets[0].attributes["id"] != targets[1].attributes["id"]
        assert [target.find_all("td", css="source-code")[0].text() for target in targets] == [
            "frame.collect()",
            "frame.collect()",
        ]
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    @pytest.mark.parametrize(
        "location",
        [
            None,
            SourceLocation("missing", 1),
            SourceLocation("source:///project/main.py", 0),
            SourceLocation("source:///project/main.py", -1),
            SourceLocation("source:///project/main.py", 5),
        ],
    )
    def test_spark_trigger_unavailable_has_no_broken_or_inferred_link(self, result, location):
        """Verify spark trigger unavailable has no broken or inferred link.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.spark_executions = [SparkExecution("action", location=location)]
        result.root_run.lines[0].spark_executions = ["action"]
        document = parse(result)
        spark = document.find_all("section", id="spark")[0]
        cells = spark.find_all("td")
        assert cells[1].text() == "Trigger source unavailable"
        assert not cells[1].find_all("a")
        detail = document.find_all("section", id=cells[0].find_all("a")[0].attributes["href"][1:])[
            0
        ]
        assert "Trigger source unavailable" in detail.text()
        assert not detail.find_all("a")
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_missing_spark_metrics_are_explicitly_unavailable(self, result):
        """Verify missing spark metrics are explicitly unavailable.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.spark_executions = [SparkExecution("empty")]
        text = parse(result).text()
        assert "Executed plan unavailable" in text
        assert "Operator details unavailable" in text
        assert "Stage and task statistics unavailable" in text


class TestSparkMetricPresentation:
    """Check spark metric presentation.

    Spark plans retain their shape without leaking raw JVM metric structures.

    """

    def test_actions_rank_by_wall_time_with_memory_spill_and_source_visible(self, result):
        """Check the expected behavior in this regression case.

        Verify actions rank by wall time with memory spill and source visible.

        """
        source = next(iter(result.sources.values()))
        result.root_run.spark_executions = [
            SparkExecution("unknown", "unknown"),
            SparkExecution("zero", "zero", stats=SparkExecutionStats(wall_time_ns=0)),
            SparkExecution(
                "slow",
                "collect",
                SourceLocation(source.id, 3),
                SparkExecutionStats(
                    wall_time_ns=2_000_000_000,
                    executor_time_ns=8_000_000_000,
                    peak_memory_bytes=2**30,
                    spill_bytes=2**20,
                ),
            ),
        ]
        result.root_run.children = [
            ProfileRun(
                spark_executions=[
                    SparkExecution("child", "child", stats=SparkExecutionStats(wall_time_ns=1))
                ]
            )
        ]
        document = parse(result)
        table = document.find_all("table", css="spark-actions")[0]
        rows = table.find_all("tbody")[0].find_all("tr")
        assert [row.find_all("td")[0].text() for row in rows] == [
            "collect #slow",
            "child #child",
            "zero #zero",
            "unknown #unknown",
        ]
        assert cell_values(rows[0]) == [
            "collect #slow",
            "main.py:3result = foo(bar(3))",
            "2.00 s",
            "8.00 s",
            "1.1 GB",
            "1.0 MB",
        ]
        assert rows[0].attributes["data-memory"] == str(2**30)
        assert rows[0].attributes["data-spill"] == str(2**20)
        assert rows[-2].attributes["data-time"] == "0"
        assert rows[-1].attributes["data-time"] == ""

    def test_operator_ranking_normalizes_units_without_summing_overlapping_timings(self, result):
        """Check the expected behavior in this regression case.

        Verify operator ranking normalizes units without summing overlapping
        timings.

        """
        scan = SparkOperator(
            "scan",
            "Scan parquet",
            metrics={"scanTime": {"value": 10_000_000, "type": "nsTiming"}},
        )
        join = SparkOperator(
            "join",
            "HashJoin",
            metrics={
                "buildTime": {"value": 1250, "name": "build time", "type": "timing"},
                "probeTime": {"value": 500, "name": "probe time", "type": "timing"},
                "peakMemory": {"value": 2**30, "type": "size"},
                "spillSize": {"value": 2**20, "type": "size"},
            },
            children=[scan],
        )
        pipeline = SparkOperator(
            "pipeline",
            "WholeStageCodegen (1)",
            metrics={"pipelineTime": {"value": 3000, "type": "timing"}},
            children=[join],
        )
        result.root_run.spark_executions = [
            SparkExecution(
                "action",
                operators=[
                    SparkOperator("wrapper", "InputAdapter", children=[pipeline]),
                    SparkOperator("unknown", "Project"),
                    SparkOperator("zero", "Filter", metrics={"time_ns": 0}),
                ],
            )
        ]
        document = parse(result)
        tables = document.find_all("table", css="spark-operators")
        for table in tables:
            rows = table.find_all("tbody")[0].find_all("tr")
            assert [row.attributes["data-time"] for row in rows] == [
                "3000000000",
                "1250000000",
                "10000000",
                "0",
                "",
            ]
            assert "Fused pipeline" in rows[0].text()
            assert "1.25 s" in rows[1].text()
            assert "1.1 GB" in rows[1].text()
            assert "1.0 MB" in rows[1].text()
            assert "InputAdapter" not in table.text()
        assert tables[0].find_all("strong")[1].attributes["title"] == (
            "Largest reported timing: build time"
        )
        assert "pipelineTime" in document.find_all("ul", css="operator-tree")[0].text()
        # Operator timing never fills the unavailable execution total.
        assert document.find_all("table", css="spark-actions")[0].find_all("td")[3].text() == "—"

    def test_operator_memory_never_uses_shuffle_data_spill_or_untyped_sizes(self, result):
        """Check the expected behavior in this regression case.

        Verify operator memory never uses shuffle data spill or untyped sizes.

        """
        result.root_run.spark_executions = [
            SparkExecution(
                "action",
                operators=[
                    SparkOperator(
                        "exchange",
                        "Exchange",
                        metrics={
                            "dataSize": {"value": 2**30, "type": "size"},
                            "spillSize": {"value": 2048, "type": "size"},
                            "peakMemory": {"value": 1024, "type": "unknown"},
                            "time": {"value": 1000, "type": "unknown"},
                        },
                    ),
                    SparkOperator(
                        "sort",
                        "Sort",
                        metrics={
                            "peak_memory_bytes": 0,
                            "spill_bytes": 0,
                            "sortTime": {"value": None, "type": "timing"},
                            "boolTime": {"value": True, "type": "timing"},
                            "invalidTime": {"value": -1, "type": "timing"},
                            "nanTime": {"value": float("nan"), "type": "timing"},
                        },
                    ),
                ],
            )
        ]
        rows = (
            parse(result)
            .find_all("table", css="spark-operators")[0]
            .find_all("tbody")[0]
            .find_all("tr")
        )
        assert [row.attributes["data-memory"] for row in rows] == ["", "0"]
        assert [row.attributes["data-time"] for row in rows] == ["", ""]
        assert [row.attributes["data-spill"] for row in rows] == ["2048", "0"]

    def test_operator_links_target_exact_steps_across_actions_and_child_runs(self, result):
        """Link measured pipelines and nested steps to their owning actions.

        Reused Spark IDs must resolve independently in child notebook plans.

        """
        executions = []
        for action_id in ("parent", 'child"<action>'):
            step = SparkOperator("524", "HashAggregate", description=action_id)
            pipeline = SparkOperator(
                "536",
                "WholeStageCodegen (1)",
                metrics={"pipelineTime": {"value": 14, "type": "timing"}},
                children=[step],
            )
            executions.append(SparkExecution(action_id, operators=[pipeline]))

        result.root_run.spark_executions = executions[:1]
        result.root_run.children = [ProfileRun(spark_executions=executions[1:])]
        document = parse(result)
        targets = document.find_all("details", css="spark-operator")
        anchors = {target.attributes["id"]: target for target in targets}
        assert len(anchors) == len(targets) == 4

        overview = document.find_all("section", id="spark")[0]
        links = document.find_all("a", css="spark-operator-link")
        assert len(links) == 8
        assert len(overview.find_all("a", css="spark-operator-link")) == 4
        for link in links:
            href = link.attributes["href"]
            assert href is not None
            target = anchors[href[1:]]
            summary = target.find_all("summary")[0].text()
            assert link.find_all("small")[0].text() in summary
            name = str(link.children[0]).removeprefix("Fused pipeline · ")
            assert summary == f"{name} {link.find_all('small')[0].text()}"
            assert "open" not in target.attributes
            owner = next(
                page for page in document.find_all("section") if target in page.find_all()
            )
            assert href.startswith(f"#{owner.attributes['id']}-")

    def test_rankings_are_visible_and_controls_scope_to_each_table(self, result):
        """Verify rankings are visible and controls scope to each table.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.spark_executions = [
            SparkExecution("action", operators=[SparkOperator("sort", "Sort")])
        ]
        document = parse(result)
        sections = document.find_all("div", css="spark-cost-section")
        assert len(sections) == 3
        for section in sections:
            controls = section.find_all("button", css="spark-order")
            assert [control.text() for control in controls] == ["Time", "Memory", "Spill"]
            assert [control.attributes["aria-pressed"] for control in controls] == [
                "true",
                "false",
                "false",
            ]
            assert len(section.find_all("table")) == 1
            assert not section.find_all("details")

    def test_fallback_plan_is_selected_and_collection_limits_stay_accessible(self, result):
        """Check the expected behavior in this regression case.

        Verify fallback plan is selected and collection limits stay accessible.

        """
        note = "Input DataFrame plan only: query listener unavailable."
        result.root_run.spark_executions = [
            SparkExecution("fallback", initial_plan="INITIAL PLAN", warnings=[note])
        ]
        document = parse(result)
        plans = document.find_all("details", css="spark-plans")[0]
        views = plans.find_all("pre", css="plan-view")
        assert "hidden" in views[0].attributes
        assert "hidden" not in views[1].attributes
        assert views[1].text() == "INITIAL PLAN"
        assert plans.find_all("button", **{"aria-pressed": "true"})[0].text() == "Initial plan"
        collection = document.find_all("details", css="spark-collection-notes")[0]
        assert note in collection.text()
        assert "execution-details" in collection.attributes["class"].split()
        assert "diagnostics" not in collection.attributes["class"].split()
        page = next(page for page in document.find_all("section") if collection in page.find_all())
        panels = [
            child
            for child in page.children
            if isinstance(child, Element) and child.tag == "details"
        ]
        assert [panel.find_all("summary")[0].text() for panel in panels] == [
            "Query plans",
            "Physical operator tree and all metrics",
            "Action metrics",
            "Plan provenance and collection notes",
            "Stage and task details",
        ]
        assert all("execution-details" in panel.attributes["class"].split() for panel in panels)

    def test_internal_wrappers_flatten_and_logical_children_stay_visible(self, result):
        """Verify internal wrappers flatten and logical children stay visible.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        scan = SparkOperator("scan", "Scan parquet", metrics={"rows": 100})
        adapter = SparkOperator("adapter", "InputAdapter", children=[scan])
        stage = SparkOperator("shuffle", "ShuffleQueryStage 3", children=[adapter])
        join = SparkOperator("join", "SortMergeJoin", children=[stage])
        codegen = SparkOperator("codegen", "WholeStageCodegen (2)", children=[join])
        wrapper = SparkOperator("result", "ResultQueryStage 4", children=[codegen])
        result.root_run.spark_executions = [SparkExecution("compact", operators=[wrapper])]
        tree = parse(result).find_all("ul", css="operator-tree")[0]
        assert "SortMergeJoin" in tree.text()
        assert "Scan parquet" in tree.text()
        for name in ("WholeStageCodegen", "InputAdapter", "ResultQueryStage", "ShuffleQueryStage"):
            assert name not in tree.text()
        assert len(tree.find_all("li")) == 2
        assert all("open" not in details.attributes for details in tree.find_all("details"))
        parent = tree.find_all("li")[0]
        children = [child for child in parent.children if isinstance(child, Element)]
        assert [child.tag for child in children] == ["details", "ul"]
        assert "Scan parquet" in children[1].text()

    def test_spark_metric_descriptors_have_human_units_and_domain_labels(self, result):
        """Verify spark metric descriptors have human units and domain labels.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        metrics = {
            "numOutputRows": {"value": 12345, "name": "output rows", "type": "sum"},
            "dataSize": {"value": 3 * 2**20, "name": "shuffle data size", "type": "size"},
            "sortTime": {"value": 1250, "name": "sort time", "type": "timing"},
            "buildTime": {"value": 7_500_000, "name": "build time", "type": "nsTiming"},
            "metric123": {"value": 1024, "name": "buffer", "type": "size"},
            "numFiles": {"value": 4, "name": "files read", "type": "sum"},
            "numPartitions": {"value": 2, "name": "partitions read", "type": "sum"},
            "spillSize": {"value": 0, "name": "disk spill", "type": "size"},
        }
        result.root_run.spark_executions = [
            SparkExecution("metrics", operators=[SparkOperator("sort", "Sort", metrics=metrics)])
        ]
        detail = parse(result).find_all("div", css="operator-detail")[0]
        rows = [cell_values(row) for row in detail.find_all("tr") if row.find_all("td")]
        assert rows == [
            ["output rows", "12,345"],
            ["shuffle data size", "3.1 MB"],
            ["Cumulative operator time · sort time", "1.25 s"],
            ["Cumulative operator time · build time", "7.50 ms"],
            ["buffer", "1.0 KB"],
            ["files read", "4"],
            ["partitions read", "2"],
            ["disk spill", "0 B"],
        ]
        assert not any(marker in detail.text() for marker in ('"value"', '"type"', '"name"'))

    def test_low_level_counters_are_omitted_and_unknown_measurements_preserved(self, result):
        """Check the expected behavior in this regression case.

        Verify low level counters are omitted and unknown measurements
        preserved.

        """
        metrics = {
            "remoteMergedBlocksFetched": {
                "value": 99,
                "name": "remote merged blocks",
                "type": "sum",
            },
            "mergedChunksFetched": {"value": 55, "name": "merged chunks", "type": "sum"},
            "internalCounter": {"value": 123, "name": "internal counter", "type": "sum"},
            "peakMemory": {"value": None, "name": "peak memory", "type": "size"},
            "numOutputRows": {"value": 0, "name": "output rows", "type": "sum"},
            "scanTime": {"value": None, "name": "scan time", "type": "timing"},
            "customRows": {"value": 42, "name": "custom rows", "type": "future-type"},
            "bytes": 4096,
            "custom metric": 7,
        }
        result.root_run.spark_executions = [
            SparkExecution("curated", operators=[SparkOperator("scan", "Scan", metrics=metrics)])
        ]
        detail = parse(result).find_all("div", css="operator-detail")[0]
        rows = [cell_values(row) for row in detail.find_all("tr") if row.find_all("td")]
        assert rows == [
            ["peak memory", "—"],
            ["output rows", "0"],
            ["Cumulative operator time · scan time", "—"],
            ["custom rows", "42"],
            ["bytes", "4.1 KB"],
            ["custom metric", "7"],
        ]
        assert "merged blocks" not in detail.text()
        assert "merged chunks" not in detail.text()
        assert "internal counter" not in detail.text()

    def test_stage_time_distributions_and_bytes_have_units(self, result):
        """Verify stage time distributions and bytes have units.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.root_run.spark_executions = [
            SparkExecution(
                "stage",
                stages=[
                    {
                        "id": 7,
                        "wall_time_ns": 500_000_000,
                        "cumulative_executor_time_ns": 4_000_000_000,
                        "task_duration_ns": {
                            "p50": 200_000_000.0,
                            "p95": 450_000_000.0,
                            "max": None,
                        },
                        "shuffle_read_bytes": 2**20,
                        "disk_spill_bytes": 0,
                        "input_bytes": None,
                    }
                ],
            )
        ]
        stage = next(
            details
            for details in parse(result).find_all("details")
            if details.find_all("summary")[0].text() == "Stage 7"
        )
        rows = [cell_values(row) for row in stage.find_all("tr") if row.find_all("td")]
        assert ["Wall time", "500.00 ms"] in rows
        assert ["Cumulative executor time", "4.00 s"] in rows
        assert ["Task duration · p50", "200.00 ms"] in rows
        assert ["Task duration · p95", "450.00 ms"] in rows
        assert ["Task duration · max", "—"] in rows
        assert ["Shuffle read", "1.0 MB"] in rows
        assert ["Disk spill", "0 B"] in rows
        assert ["Input", "—"] in rows
        assert '{"p50"' not in stage.text()


class TestReportSafety:
    """Check report safety.

    Ensure data stays inert and the report has no network dependencies.

    """

    def test_source_paths_and_metadata_cannot_inject_markup(self, result):
        """Verify source paths and metadata cannot inject markup.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        hostile = (
            '<img src="https://attacker.invalid/pixel" onerror="alert(1)"></script>'
            "<script>alert(2)</script>"
        )
        unit = SourceUnit(hostile, hostile, f"value = {hostile!r}\n# {hostile}\n")
        result.sources = {unit.id: unit}
        result.root_run.lines = [LineStats(SourceLocation(unit.id, 1), 1)]
        result.root_run.functions = [FunctionStats(unit.id, hostile, 1)]
        result.root_run.children = [ProfileRun(name=hostile, metadata={hostile: hostile})]
        result.root_run.spark_executions = [
            SparkExecution(
                "malicious",
                name=hostile,
                executed_plan=hostile,
                operators=[SparkOperator(hostile, hostile, hostile, {hostile: hostile})],
                stages=[{"id": hostile}],
                warnings=[hostile],
            )
        ]
        result.warnings = [hostile]
        document = parse(result)
        assert len(document.find_all("script")) == 1
        images = document.find_all("img")
        assert len(images) == 1
        assert images[0].attributes["class"] == "brand-mark"
        assert images[0].attributes["src"].startswith("data:image/svg+xml;base64,")
        assert all(
            not key.startswith("on") for node in document.find_all() for key in node.attributes
        )
        assert hostile in document.text()

    def test_symbol_tooltip_is_escaped(self, result):
        """Verify symbol tooltip is escaped.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        name = 'foo" onmouseover="alert(1)'
        target = result.root_run.lines[0].calls[0].target
        result.root_run.lines[0].calls[0] = SymbolRef(name, 3, 9, 12, target)
        link = source_rows(parse(result))[2].find_all("a", css="symbol")[0]
        assert link.attributes["title"] == f"Go to {name}"
        assert "onmouseover" not in link.attributes

    def test_single_file_offline_assets_and_csp(self, result):
        """Verify single file offline assets and csp.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        assert len(document.find_all("style")) == 1
        assert len(document.find_all("script")) == 1
        assert not document.find_all("link")
        assert all("src" not in script.attributes for script in document.find_all("script"))
        external_links = [
            node
            for node in document.find_all("a")
            if (node.attributes.get("href") or "").startswith(("http:", "https:"))
        ]
        assert external_links == document.find_all("a", css="header-detail")
        assert len(external_links) == 3
        assert all(
            link.attributes["href"].startswith("https://tvdboom.github.io/linescope/user_guide/")
            for link in external_links
        )
        csp = next(
            node
            for node in document.find_all("meta")
            if node.attributes.get("http-equiv") == "Content-Security-Policy"
        )
        assert "default-src 'none'" in csp.attributes["content"]
        javascript = document.find_all("script")[0].text()
        assert "fetch(" not in javascript
        assert "XMLHttpRequest" not in javascript

    def test_accessible_navigation_and_search(self, result):
        """Verify accessible navigation and search.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        document = parse(result)
        assert document.find_all("a", css="skip-link")[0].attributes["href"] == "#main"
        assert document.find_all("nav")[0].attributes["aria-label"] == "Report views"
        assert document.find_all("input", id="source-search")[0].attributes["type"] == "search"
        assert document.find_all("button", id="theme-toggle")[0].attributes["aria-label"]
        assert all(
            link.attributes.get("aria-label")
            for row in source_rows(document)
            for link in row.find_all("td", css="line-number")[0].find_all("a")
        )

    @pytest.mark.parametrize(
        ("backend", "sampled", "method", "section"),
        [
            ("trace", False, "Tracing", "trace"),
            ("scalene", True, "Sampling", "scalene"),
            ("tachyon", True, "Sampling", "tachyon"),
            ('custom"<backend>', False, "Tracing", "custom-backends"),
        ],
    )
    def test_header_labels_link_to_user_guide(self, result, backend, sampled, method, section):
        """Verify header labels link to user guide.

        Inspect rendered report elements and attributes using the lightweight
        DOM rather than launching a browser.

        """
        result.backend = backend
        result.capabilities = replace(result.capabilities, sampled=sampled)
        header = parse(result).find_all("header", css="topbar")[0]
        links = header.find_all("a", css="header-detail")

        assert [link.find_all("span", css="header-label")[0].text() for link in links] == [
            "Run status",
            "Backend",
            "Collection method",
        ]
        assert [link.find_all("span", css="header-value")[0].text() for link in links] == [
            "Success",
            backend,
            method,
        ]
        assert [link.attributes["href"] for link in links] == [
            "https://tvdboom.github.io/linescope/user_guide/reports/#run-status",
            f"https://tvdboom.github.io/linescope/user_guide/backends/#{section}",
            "https://tvdboom.github.io/linescope/user_guide/reports/#collection-method",
        ]
        for link in links:
            assert link.attributes["target"] == "_blank"
            assert set(link.attributes["rel"].split()) == {"noopener", "noreferrer"}
            assert "opens in a new tab" in link.attributes["title"]
