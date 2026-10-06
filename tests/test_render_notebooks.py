"""LineScope.

Author: Mavs
Description: Verify grouped notebook source, cell boundaries, and exact links.

"""

from copy import deepcopy
from dataclasses import asdict, replace

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
    SymbolRef,
)
from linescope.notebooks.remote import notebook_sources
from linescope.render import render_html
from tests.test_render import ReportDOM, cell_values, source_rows


@pytest.fixture
def notebook_profile() -> ProfileResult:
    """Build a notebook with measured and unexecuted cells beside a Python file.

    Use exported source to exercise the same cell identities as Databricks
    capture without requiring a notebook runtime.

    Returns
    -------
    ProfileResult
        Trace profile containing three notebook cells and one Python file.

    """
    cells = notebook_sources(
        "/Workspace/report.ipynb",
        "def build():\n    return 1\n# COMMAND ----------\n"
        "value = build()\n# second cell\n# COMMAND ----------\n# unexecuted cell\n",
    )
    helper = SourceUnit("helper", "/project/helper.py", "value = 2\n")
    run = ProfileRun(
        source=cells[0],
        lines=[
            LineStats(SourceLocation(cells[0].id, 2), 1000, hits=1),
            LineStats(
                SourceLocation(cells[1].id, 1),
                2000,
                hits=2,
                calls=[SymbolRef("build", 1, 8, 13, SourceLocation(cells[0].id, 1))],
            ),
        ],
        functions=[FunctionStats(cells[0].id, "build", 1, 1000, 1, line_count=2)],
    )
    return ProfileResult(
        run,
        {unit.id: unit for unit in [*cells, helper]},
        "trace",
        BackendCapabilities(hit_counts=True),
    )


@pytest.mark.parametrize("sampled", [False, True])
def test_notebook_overviews_group_cells_and_sum_metrics(
    notebook_profile: ProfileResult, *, sampled: bool
) -> None:
    """List each notebook once and retain its complete cell source in order.

    Parameters
    ----------
    notebook_profile : ProfileResult
        Three-cell notebook profile with an independent Python file.

    sampled : bool
        Whether to expose sampled counts instead of trace hits.

    """
    if sampled:
        notebook_profile.capabilities = BackendCapabilities(sampled=True, sample_counts=True)
        for count, line in enumerate(notebook_profile.root_run.lines, 1):
            line.samples = count
            line.hits = None
    original = deepcopy(notebook_profile)
    document = ReportDOM(render_html(notebook_profile)).root
    items = document.find_all("a", css="source-item")
    assert [item.attributes["title"] for item in items] == [
        "report.ipynb",
        "helper.py",
    ]
    card = next(
        card for card in document.find_all("div", css="stat") if "Source snapshots" in card.text()
    )
    assert card.find_all("strong")[0].text() == "2"
    files = document.find_all("section", id="files")[0].find_all("tbody")[0]
    rows = files.find_all("tr")
    assert len(rows) == 2
    notebook_row = next(row for row in rows if "Notebook" in row.text())
    assert cell_values(notebook_row) == [
        "3.0 µs",
        "report.ipynb",
        *(["3"] if sampled else []),
        "Notebook",
        "5",
    ]
    assert not document.find_all("section", id="notebooks")
    assert not document.find_all("a", href="#notebooks")
    assert not document.find_all("table", css="notebook-invocations")

    pages = document.find_all("section", css="source-page")
    assert len(pages) == 2
    page = pages[0]
    assert page.find_all("h1")[0].text() == "report.ipynb"
    assert (
        page.find_all("div", css="source-summary")[0].find_all("p")[0].text()
        == "3 cells · 5 lines · Total time: 3.0 µs"
    )
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == [
        "Cell 1",
        "Cell 2",
        "Cell 3",
    ]
    assert [row.attributes["data-line"] for row in source_rows(page)] == ["1", "2", "1", "2", "1"]
    assert [row.find_all("td", css="source-code")[0].text() for row in source_rows(page)] == [
        text
        for unit in list(notebook_profile.sources.values())[:3]
        for text in unit.source.splitlines()
    ]
    assert source_rows(page)[-1].attributes["data-time"] == ""
    assert source_rows(page)[-1].attributes["data-samples" if sampled else "data-hits"] == "0"
    assert notebook_profile == original


@pytest.mark.parametrize("captured", [False, True])
def test_files_keeps_distinct_invocations_and_unavailable_waits(*, captured: bool) -> None:
    """Keep child details reachable from Files with or without captured source.

    List repeated invocations separately while sharing their source snapshot.
    Preserve failed outcomes, unknown waits, and escaped notebook names.

    Parameters
    ----------
    captured : bool
        Whether the repeated child invocations have a captured source snapshot.

    """
    unit = SourceUnit("notebook://child", "/Workspace/child", "value = 1\n", "notebook")
    children = [
        ProfileRun(
            id="first",
            name="<Child & one>",
            source=unit if captured else None,
            elapsed_ns=20_000_000,
            metadata={"parent_wait_time_ns": 50_000_000},
        ),
        ProfileRun(
            id="second",
            name="<Child & one>",
            source=unit if captured else None,
            status="failed",
            metadata={"parent_wait_time_ns": None},
        ),
        ProfileRun(id="missing", name="Uncaptured", elapsed_ns=10_000_000),
    ]
    profile = ProfileResult(
        ProfileRun(children=children),
        {unit.id: unit} if captured else {},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    html = render_html(profile)
    document = ReportDOM(html).root
    files = document.find_all("section", id="files")[0]
    assert len(files.find_all("tr", css="heat-row")) == int(captured)
    table = files.find_all("table", css="notebook-invocations")[0]
    assert [heading.text() for heading in table.find_all("th")] == [
        "Notebook",
        "Parent wait",
        "Status",
    ]
    assert [cell_values(row) for row in table.find_all("tbody")[0].find_all("tr")] == [
        ["<Child & one>", "50.00 ms", "success"],
        ["<Child & one>", "—", "failed"],
        ["Uncaptured", "10.00 ms", "success"],
    ]
    links = table.find_all("a")
    assert len({link.attributes["href"] for link in links}) == len(children)
    for index, link in enumerate(links):
        page = document.find_all("section", id=link.attributes["href"][1:])[0]
        assert page.find_all("h1")[0].text() == children[index].name
        source_links = page.find_all("a")
        assert bool(source_links) == (captured and index < 2)
        if source_links:
            assert document.find_all("tr", id=source_links[0].attributes["href"][1:])
    assert "&lt;Child &amp; one&gt;" in html
    assert not document.find_all("section", id="notebooks")
    assert not document.find_all("a", href="#notebooks")


def test_links_reach_exact_lines_in_later_cells(notebook_profile: ProfileResult) -> None:
    """Keep function, Spark, memory, and child links valid in grouped source.

    Parameters
    ----------
    notebook_profile : ProfileResult
        Notebook profile whose second cell receives extra navigation context.

    """
    cells = list(notebook_profile.sources.values())[:3]
    run = notebook_profile.root_run
    run.spark_executions = [SparkExecution("action", location=SourceLocation(cells[1].id, 1))]
    run.lines[1].spark_executions = ["action"]
    run.memory_samples = [MemorySample(0, 1000, SourceLocation(cells[2].id, 1))]
    run.children = [ProfileRun(id="child", name="Child", source=cells[1])]
    run.lines[1].notebook_runs = ["child"]
    document = ReportDOM(render_html(notebook_profile)).root
    page = document.find_all("section", css="source-page")[0]
    ids = [node.attributes["id"] for node in document.find_all() if "id" in node.attributes]
    assert len(ids) == len(set(ids))
    for link in document.find_all("a"):
        href = link.attributes.get("href") or ""
        if href.startswith("#"):
            assert href[1:] in ids
    first_rows = [source_rows(body)[0] for body in page.find_all("tbody")]
    targets = ["#" + row.attributes["id"] for row in first_rows]
    assert len(set(targets)) == 3
    assert source_rows(page)[2].find_all("a", css="symbol")[0].attributes["href"] == targets[0]
    assert (
        document.find_all("section", id="functions")[0].find_all("a")[0].attributes["href"]
        == targets[0]
    )
    for section in ("spark", "memory"):
        links = document.find_all("section", id=section)[0].find_all("a")
        assert any(
            link.attributes.get("href") == targets[1 if section == "spark" else 2]
            for link in links
        )
    child_page = next(
        section
        for section in document.find_all("section")
        if (section.attributes.get("id") or "").startswith("run-")
    )
    assert child_page.find_all("a")[0].attributes["href"] == targets[1]


@pytest.mark.parametrize(
    ("path", "filename"),
    [
        ("/project/notebooks/example.ipynb", "example.ipynb"),
        (r"C:\project\notebooks\example.ipynb", "example.ipynb"),
        ('/project/notebooks/example<&".ipynb', 'example<&".ipynb'),
    ],
)
def test_notebook_filename_labels_keep_cell_links_and_full_snapshot_paths(
    path: str, filename: str
) -> None:
    """Show only notebook filenames across reports on either path platform.

    Keep cell and line context on exact source links, safely escape filenames,
    and retain the full path in the original snapshot identity. Include line
    growth so the memory view links from its largest-change badge.

    Parameters
    ----------
    path : str
        Full notebook path using POSIX or Windows separators.

    filename : str
        Filename and extension displayed without the notebook's directory.

    """
    unit = notebook_sources(path, "def build():\n    return 42\n")[0]
    location = SourceLocation(unit.id, 2)
    run = ProfileRun(
        lines=[LineStats(location, 1000, hits=1, ram=ProcessMemoryStats(delta_bytes=100))],
        functions=[FunctionStats(unit.id, "build", 1, 1000, 1)],
        memory_samples=[MemorySample(0, 1000, location)],
        spark_executions=[SparkExecution("action", location=location)],
    )
    profile = ProfileResult(
        run, {unit.id: unit}, "trace", BackendCapabilities(hit_counts=True, memory=True)
    )
    original = deepcopy(profile)
    document = ReportDOM(render_html(profile)).root
    source_page = document.find_all("section", css="source-page")[0]
    assert source_page.find_all("h1")[0].text() == filename
    assert document.find_all("a", css="source-item")[0].attributes["title"] == filename
    row_ids = {row.attributes["id"] for row in source_rows(source_page)}
    for page_id in ("overview", "files", "functions", "memory", "spark"):
        page = document.find_all("section", id=page_id)[0]
        links = [link for link in page.find_all("a") if filename in link.text()]
        assert links
        for link in links:
            assert link.text().startswith(filename)
            assert path not in link.text()
            assert link.attributes["href"][1:] in row_ids
    assert profile == original


def test_notebooks_with_the_same_filename_keep_separate_groups(
    notebook_profile: ProfileResult,
) -> None:
    """Use canonical notebook paths even when cell display labels collide.

    Parameters
    ----------
    notebook_profile : ProfileResult
        Existing notebook and Python file retained beside a second notebook.

    """
    cells = notebook_sources("/Other/report.ipynb", "other = 1\n# COMMAND ----------\nother += 1")
    for cell in cells:
        notebook_profile.sources[cell.id] = replace(cell, path="/Workspace/report.ipynb · cell 1")
    document = ReportDOM(render_html(notebook_profile)).root
    assert [item.attributes["title"] for item in document.find_all("a", css="source-item")] == [
        "report.ipynb",
        "helper.py",
        "report.ipynb",
    ]
    assert [
        page.find_all("h1")[0].text() for page in document.find_all("section", css="source-page")
    ] == ["report.ipynb", "helper.py", "report.ipynb"]
    items = document.find_all("a", css="source-item")
    assert len({item.attributes["href"] for item in items}) == 3
    for item, page in zip(items, document.find_all("section", css="source-page"), strict=True):
        assert item.attributes["href"] == "#" + source_rows(page)[0].attributes["id"]


def test_grouped_cells_keep_their_own_collector_capabilities(
    notebook_profile: ProfileResult,
) -> None:
    """Show supported columns without fabricating metrics for other cells.

    Parameters
    ----------
    notebook_profile : ProfileResult
        Trace notebook whose last cell is measured by a sampled child run.

    """
    cell = list(notebook_profile.sources.values())[2]
    notebook_profile.root_run.children = [
        ProfileRun(
            source=cell,
            lines=[
                LineStats(
                    SourceLocation(cell.id, 1),
                    3000,
                    samples=4,
                    ram=ProcessMemoryStats(delta_bytes=1000, peak_bytes=2000),
                    gpu=GPUStats(time_ns=5000, peak_memory_bytes=6000),
                )
            ],
            metadata={
                "child_backend": "scalene",
                "child_capabilities": asdict(
                    BackendCapabilities(sampled=True, sample_counts=True, memory=True, gpu=True)
                ),
            },
        )
    ]
    document = ReportDOM(render_html(notebook_profile)).root
    page = document.find_all("section", css="source-page")[0]
    table = page.find_all("table")[0]
    headers = table.find_all("thead")[0].find_all("th")
    for body in table.find_all("tbody"):
        assert body.find_all("th")[0].attributes["colspan"] == str(len(headers))
        assert all(len(cell_values(row)) == len(headers) for row in source_rows(body))
    first, last = source_rows(page)[0], source_rows(page)[-1]
    assert [first.attributes[key] for key in ("data-samples", "data-memory", "data-gpu-time")] == [
        ""
    ] * 3
    assert last.attributes["data-hits"] == ""
    assert [last.attributes[key] for key in ("data-samples", "data-memory", "data-gpu-time")] == [
        "4",
        "1000",
        "5000",
    ]
    assert cell_values(last)[3:7] == ["—", "—", "+1.0 KB", "2.0 KB"]


def test_revised_cells_keep_both_snapshots(notebook_profile: ProfileResult) -> None:
    """Retain changed cell text and distinguish its captured revisions.

    Parameters
    ----------
    notebook_profile : ProfileResult
        Notebook whose first cell is captured again with different text.

    """
    cell = next(iter(notebook_profile.sources.values()))
    revised = replace(cell, id=cell.id.rsplit("-", 1)[0] + "-123456789abc", source="updated = 1")
    notebook_profile.sources[revised.id] = revised
    page = ReportDOM(render_html(notebook_profile)).root.find_all("section", css="source-page")[0]
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == [
        "Cell 1 · snapshot 1",
        "Cell 2",
        "Cell 3",
        "Cell 1 · snapshot 2",
    ]
    assert source_rows(page)[-1].find_all("td", css="source-code")[0].text() == "updated = 1"


@pytest.mark.parametrize("source", ["", "%sql\nselect 1", 'value = "</script><img onerror=bad>"'])
def test_notebook_grouping_escapes_paths_and_preserves_magic_or_empty_cells(source: str) -> None:
    """Preserve inert source and path characters across grouped cell boundaries.

    Parameters
    ----------
    source : str
        Empty, magic, or hostile source text preserved in the second cell.

    """
    path = '/Workspace/report#part<&".ipynb'
    cells = notebook_sources(path, f"first = 1\n# COMMAND ----------\n{source}")
    profile = ProfileResult(
        ProfileRun(), {cell.id: cell for cell in cells}, "trace", BackendCapabilities()
    )
    document = ReportDOM(render_html(profile)).root
    page = document.find_all("section", css="source-page")[0]
    assert page.find_all("h1")[0].text() == 'report#part<&".ipynb'
    assert (
        document.find_all("a", css="source-item")[0].attributes["title"] == 'report#part<&".ipynb'
    )
    second = page.find_all("tbody")[1]
    assert [row.find_all("td", css="source-code")[0].text() for row in source_rows(second)] == (
        source.splitlines() or [" "]
    )
    assert len(document.find_all("script")) == 1
    assert len(document.find_all("img")) == 1
    assert all(not key.startswith("on") for node in document.find_all() for key in node.attributes)


@pytest.mark.parametrize(
    ("identities", "paths", "labels"),
    [
        (
            [
                "notebook://interactive#cell-front-end-id-123456789abc",
                "notebook://interactive#cell-8",
            ],
            ["interactive · cell front-end-id", "interactive"],
            ["Cell front-end-id", "Cell 8"],
        ),
        (
            [
                "notebook://interactive#snapshot-123456789abc",
                "notebook://interactive#snapshot-abcdef123456",
            ],
            ["interactive", "interactive"],
            ["Snapshot 1", "Snapshot 2"],
        ),
        (
            ["first", "second"],
            ["interactive · cell 1", "interactive · cell 2"],
            ["Cell 1", "Cell 2"],
        ),
    ],
)
def test_interactive_and_unsplit_notebook_snapshots_share_their_source_view(
    identities: list[str], paths: list[str], labels: list[str]
) -> None:
    """Group frontend cell labels and unsplit notebook snapshots conservatively.

    Parameters
    ----------
    identities : list[str]
        Distinct source identities referring to the same notebook.

    paths : list[str]
        Display paths with or without frontend cell labels.

    labels : list[str]
        Expected boundary labels without opaque source digests.

    """
    cells = [
        SourceUnit(identity, path, "value = 1", "notebook")
        for identity, path in zip(identities, paths, strict=True)
    ]
    profile = ProfileResult(
        ProfileRun(), {cell.id: cell for cell in cells}, "trace", BackendCapabilities()
    )
    document = ReportDOM(render_html(profile)).root
    assert len(document.find_all("a", css="source-item")) == 1
    page = document.find_all("section", css="source-page")[0]
    assert page.find_all("h1")[0].text() == "interactive"
    assert [heading.text() for heading in page.find_all("tr", css="source-cell-heading")] == labels
