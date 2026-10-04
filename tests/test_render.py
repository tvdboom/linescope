"""LineScope.

Author: Mavs
Description: Portable report content, escaping, metrics, and navigation
contracts.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

import pytest

from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    MemoryStats,
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

    """

    tag: str
    attributes: dict[str, str | None] = field(default_factory=dict)
    children: list[Element | str] = field(default_factory=list)

    def text(self):
        return "".join(
            child.text() if isinstance(child, Element) else child for child in self.children
        )

    def find_all(self, tag=None, *, css=None, **attributes):
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

    """

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attributes):
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
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
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
    return ReportDOM(render_html(result)).root


def source_rows(document):
    return document.find_all("tr", css="source-row")


def cell_values(row):
    return [cell.text() for cell in row.children if isinstance(cell, Element) and cell.tag == "td"]


class TestSourceReport:
    """Check source report.

    Full-source presentation and optional metric behavior.

    """

    def test_full_source_including_unexecuted_lines(self, result):
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
        rows = source_rows(parse(result))
        assert cell_values(rows[2])[1:4] == ["120.00 ms", "2", "60.00 ms"]
        assert rows[2].attributes["style"] == "--heat:1.00000"
        assert rows[3].attributes["style"] == "--heat:0.00000"

    def test_sampled_values_never_fabricate_hits(self, result):
        result.capabilities = BackendCapabilities(sampled=True, hit_counts=False)
        result.root_run.lines[0].hits = None
        document = parse(result)
        rows = source_rows(document)
        assert cell_values(rows[2])[1:4] == ["120.00 ms", "—", "—"]
        assert cell_values(rows[3])[1:4] == ["—", "—", "—"]
        assert "Estimated time" in document.text()
        assert "Sampled estimates" in document.text()
        assert "Observed lines" in document.text()

    def test_missing_values_differ_from_measured_zero(self, result):
        result.root_run.lines[0].wall_time_ns = 0
        rows = source_rows(parse(result))
        assert cell_values(rows[2])[1] == "0 µs"
        assert cell_values(rows[3])[1] == "—"

    def test_driver_memory_optional_and_signed(self, result):
        result.root_run.lines[0].memory = MemoryStats(-1024, 4 * 1024**2)
        assert "Driver memory Δ" not in parse(result).text()
        result.capabilities = BackendCapabilities(memory=True, hit_counts=True)
        document = parse(result)
        assert "Driver memory Δ" in document.text()
        assert cell_values(source_rows(document)[2])[4:6] == ["-1.0 KiB", "4.0 MiB"]
        assert cell_values(source_rows(document)[3])[4:6] == ["—", "—"]

    def test_two_calls_have_distinct_link_targets(self, result):
        document = parse(result)
        rows = source_rows(document)
        links = rows[2].find_all("a", css="symbol")
        assert [link.text() for link in links] == ["foo", "bar"]
        assert links[0].attributes["href"] == "#" + rows[0].attributes["id"]
        assert links[1].attributes["href"] == "#" + rows[1].attributes["id"]
        assert links[0].attributes["href"] != links[1].attributes["href"]

    def test_unicode_links_preserve_original_spelling(self, result):
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
        document = parse(result)
        nodes = document.find_all()
        ids = [node.attributes["id"] for node in nodes if "id" in node.attributes]
        assert len(ids) == len(set(ids))
        for link in document.find_all("a"):
            target = link.attributes.get("href", "")
            assert target.startswith("#")
            assert target[1:] in ids

    def test_top_level_views_and_source_anchor_routes(self, result):
        document = parse(result)
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
        document = parse(result)
        assert not document.find_all("footer")
        assert "Python driver profiling · self-contained HTML" not in document.text()

    @pytest.mark.parametrize("notebook_context", ["none", "snapshot", "child"])
    @pytest.mark.parametrize("spark_context", ["none", "root", "nested"])
    def test_optional_views_follow_captured_context(self, result, notebook_context, spark_context):
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
        assert "1.0 GiB" in text
        assert "HashJoin" in text
        assert "Scan parquet" in text
        assert "Stage 7" in text
        views = page.find_all("pre", css="plan-view")
        assert len(views) == 5
        assert views[0].text() == "FINAL AQE EXECUTED PLAN"
        assert "hidden" not in views[0].attributes
        assert all("hidden" in view.attributes for view in views[1:])
        assert "exact per-line Spark runtimes" in text
        assert "Executor Python workers are outside" in text
        assert cell_values(source_rows(document)[2])[1] == "120.00 ms"
        self.test_every_internal_link_resolves_and_ids_are_unique(result)

    def test_multiple_spark_references_per_line(self, result):
        result.root_run.spark_executions = [SparkExecution("first"), SparkExecution("second")]
        result.root_run.lines[0].spark_executions = ["first", "second"]
        document = parse(result)
        badges = source_rows(document)[2].find_all("a", css="spark")
        assert len(badges) == 2
        assert badges[0].attributes["href"] != badges[1].attributes["href"]

    def test_missing_spark_metrics_are_explicitly_unavailable(self, result):
        result.root_run.spark_executions = [SparkExecution("empty")]
        text = parse(result).text()
        assert "Executed plan unavailable" in text
        assert "Operator details unavailable" in text
        assert "Stage and task statistics unavailable" in text


class TestSparkMetricPresentation:
    """Check spark metric presentation.

    Spark plans retain their shape without leaking raw JVM metric structures.

    """

    def test_internal_wrappers_flatten_and_logical_children_stay_visible(self, result):
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
            ["shuffle data size", "3.0 MiB"],
            ["Cumulative operator time · sort time", "1.25 s"],
            ["Cumulative operator time · build time", "7.50 ms"],
            ["buffer", "1.0 KiB"],
            ["files read", "4"],
            ["partitions read", "2"],
            ["disk spill", "0 B"],
        ]
        assert not any(marker in detail.text() for marker in ('"value"', '"type"', '"name"'))

    def test_low_level_counters_are_omitted_and_unknown_measurements_preserved(self, result):
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
            ["bytes", "4.0 KiB"],
            ["custom metric", "7"],
        ]
        assert "merged blocks" not in detail.text()
        assert "merged chunks" not in detail.text()
        assert "internal counter" not in detail.text()

    def test_stage_time_distributions_and_bytes_have_units(self, result):
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
        assert ["Shuffle read", "1.0 MiB"] in rows
        assert ["Disk spill", "0 B"] in rows
        assert ["Input", "—"] in rows
        assert '{"p50"' not in stage.text()


class TestReportSafety:
    """Check report safety.

    Ensure data stays inert and the report has no network dependencies.

    """

    def test_source_paths_and_metadata_cannot_inject_markup(self, result):
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
        name = 'foo" onmouseover="alert(1)'
        target = result.root_run.lines[0].calls[0].target
        result.root_run.lines[0].calls[0] = SymbolRef(name, 3, 9, 12, target)
        link = source_rows(parse(result))[2].find_all("a", css="symbol")[0]
        assert link.attributes["title"] == f"Go to {name}"
        assert "onmouseover" not in link.attributes

    def test_single_file_offline_assets_and_csp(self, result):
        document = parse(result)
        assert len(document.find_all("style")) == 1
        assert len(document.find_all("script")) == 1
        assert not document.find_all("link")
        assert all("src" not in script.attributes for script in document.find_all("script"))
        assert all(
            not (node.attributes.get("href") or "").startswith(("http:", "https:"))
            for node in document.find_all()
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
