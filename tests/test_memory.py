"""LineScope.

Author: Mavs
Description: Verify process RAM intervals, spikes, timeline bounds, and cleanup.

"""

from dataclasses import asdict
import json
import sys
import threading

import psutil
import pytest

from linescope import Session
from linescope.memory import ProcessMemoryCollector, _Sample
from linescope.model import (
    BackendCapabilities,
    LineStats,
    MemorySample,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.notebooks.serialization import dumps_result, loads_result
from linescope.render import render_html
from tests.test_render import ReportDOM, cell_values


@pytest.fixture
def controlled_ram(monkeypatch):
    """Supply deterministic RSS readings without background scheduling.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    state = {"rss": 100}

    def read(_collector):
        """Read the workload's controlled process RAM value.

        Inspect controlled RAM observations, source intervals, and timeline
        snapshots without relying on exact timing.

        """
        return state["rss"]

    def wait_for_cleanup(collector):
        """Keep the sampler idle until cleanup releases its thread.

        Inspect controlled RAM observations, source intervals, and timeline
        snapshots without relying on exact timing.

        """
        collector._stop.wait()

    monkeypatch.setattr(ProcessMemoryCollector, "_read", read)
    monkeypatch.setattr(ProcessMemoryCollector, "_loop", wait_for_cleanup)
    return state


def execute_ram(tmp_path, source, state, **namespace):
    """Execute a controlled script with process RAM collection enabled.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    path = tmp_path / "memory_workload.py"
    path.write_text(source, encoding="utf-8")
    session = Session(
        backend="trace",
        memory=True,
        root=str(tmp_path),
        display="none",
        notebooks=False,
        spark=False,
    )
    with session:
        exec(compile(source, str(path), "exec"), {"state": state, **namespace})
    return session


def test_ram_readings_belong_to_completed_lines(tmp_path, controlled_ram):
    """Attribute each boundary reading to the line that just executed.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    session = execute_ram(
        tmp_path,
        "state['rss'] = 300\nstate['rss'] = 900\nstate['rss'] = 200\npass\n"
        "if False:\n    state['rss'] = 9000\n",
        controlled_ram,
    )
    rows = {line.location.line: line.ram for line in session.result.root_run.lines}
    assert rows[1] == ProcessMemoryStats(300, 200, 300)
    assert rows[2] == ProcessMemoryStats(900, 600, 900)
    assert rows[3] == ProcessMemoryStats(200, -700, 900)
    assert rows[4] == ProcessMemoryStats(200, 0, 200)
    assert rows.get(6) is None
    readings = session.result.root_run.memory_samples
    assert readings[0].rss_bytes == 100
    assert max(item.rss_bytes for item in readings) == 900
    assert [item.elapsed_ns for item in readings] == sorted(item.elapsed_ns for item in readings)


def test_repeated_lines_keep_last_reading_total_growth_and_peak(tmp_path, controlled_ram):
    """Retain loop growth without summing absolute RAM usage.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    session = execute_ram(
        tmp_path, "for _ in range(3):\n    state['rss'] += 100\n", controlled_ram
    )
    rows = {line.location.line: line.ram for line in session.result.root_run.lines}
    assert rows[2] == ProcessMemoryStats(400, 300, 400)
    observations = [
        item.rss_bytes
        for item in session.result.root_run.memory_samples
        if item.location is not None and item.location.line == 2
    ]
    assert observations == [200, 300, 400]


def test_long_call_spike_survives_a_lower_boundary_reading(tmp_path, controlled_ram):
    """Observe temporary RAM inside an external call without relying on time.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    path = tmp_path / "spike.py"
    path.write_text("external()\n", encoding="utf-8")
    session = Session(
        backend="trace",
        memory=True,
        root=str(tmp_path),
        display="none",
        spark=False,
        notebooks=False,
    )

    def external():
        """Expose a temporary process spike to a periodic observation.

        Inspect controlled RAM observations, source intervals, and timeline
        snapshots without relying on exact timing.

        """
        controlled_ram["rss"] = 1000
        session._memory._sample()
        controlled_ram["rss"] = 200

    with session:
        exec(compile(path.read_text(), str(path), "exec"), {"external": external})
    stats = next(line.ram for line in session.result.root_run.lines if line.location.line == 1)
    assert stats == ProcessMemoryStats(200, 100, 1000)
    assert any(sample.rss_bytes == 1000 for sample in session.result.root_run.memory_samples)


def test_nested_project_calls_do_not_double_charge_ram_growth(tmp_path, controlled_ram):
    """Keep process growth on the active project child interval.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    session = execute_ram(
        tmp_path,
        "def child():\n    state['rss'] += 400\nchild()\n",
        controlled_ram,
    )
    rows = {line.location.line: line.ram for line in session.result.root_run.lines}
    assert rows[2].delta_bytes == 400
    assert rows[3].delta_bytes == 0
    assert sum(stats.delta_bytes for stats in rows.values() if stats is not None) == 400


def test_unknown_readings_do_not_become_zero_or_bridge_a_gap(tmp_path, controlled_ram):
    """Keep missing RSS and changes unavailable until a fresh interval.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    session = execute_ram(
        tmp_path,
        "state['rss'] = None\nstate['rss'] = 200\npass\n",
        controlled_ram,
    )
    rows = {line.location.line: line.ram for line in session.result.root_run.lines}
    assert rows[1].rss_bytes is None
    assert rows[1].delta_bytes is None
    assert rows[2].rss_bytes == 200
    assert rows[2].delta_bytes is None
    assert rows[3].delta_bytes == 0
    assert any(item.rss_bytes is None for item in session.result.root_run.memory_samples)


def test_workload_failure_restores_hooks_and_joins_sampler(tmp_path, controlled_ram):
    """Release instrumentation and preserve the workload's original error.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    previous = sys.gettrace()
    with pytest.raises(ValueError, match="workload failed"):
        execute_ram(
            tmp_path, "state['rss'] = 300\nraise ValueError('workload failed')\n", controlled_ram
        )
    assert sys.gettrace() is previous
    assert not any(thread.name == "linescope-ram" for thread in threading.enumerate())
    execute_ram(tmp_path, "pass\n", controlled_ram)


def test_partial_startup_restores_hooks_and_session_lock(tmp_path, monkeypatch):
    """Unwind tracing when the RAM sampler thread cannot start.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    previous = sys.gettrace()

    def fail_start(_thread):
        """Fail before allocating a sampler worker.

        Inspect controlled RAM observations, source intervals, and timeline
        snapshots without relying on exact timing.

        """
        raise RuntimeError("sampler startup")

    session = Session(
        backend="trace",
        memory=True,
        root=str(tmp_path),
        display="none",
        notebooks=False,
        spark=False,
    )
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(RuntimeError, match="sampler startup"):
            session.start()
    assert sys.gettrace() is previous
    assert not session._memory._running
    Session(backend="trace", display="none", spark=False, notebooks=False).start().stop()


def test_permission_failure_records_diagnostics(monkeypatch):
    """Keep denied process readings unknown with a useful diagnostic.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    collector = ProcessMemoryCollector(lambda _: False, lambda _: None)

    def denied():
        """Emulate unavailable process memory access.

        Inspect controlled RAM observations, source intervals, and timeline
        snapshots without relying on exact timing.

        """
        raise psutil.AccessDenied

    monkeypatch.setattr(collector._process, "memory_info", denied)
    collector._record("work.py", 1, boundary=True)
    lines, samples, warnings, _compressed = collector.snapshot()
    assert lines[("work.py", 1)] == ProcessMemoryStats()
    assert samples[0].rss_bytes is None
    assert len(warnings) == 1
    assert "AccessDenied" in warnings[0]


def test_timeline_stays_bounded_and_retains_extrema(monkeypatch):
    """Preserve chronological peaks and gaps when the history is compressed.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    monkeypatch.setattr("linescope.memory._MAX_SAMPLES", 32)
    collector = ProcessMemoryCollector(lambda _: False, lambda _: None)
    for index in range(1000):
        rss = 9999 if index == 413 else (None if index == 600 else index % 10)
        collector._append(_Sample(index, rss, "work.py", index + 1))
    _lines, samples, _warnings, compressed = collector.snapshot()
    assert len(samples) <= 32
    assert samples[0].elapsed_ns == 0
    assert samples[-1].elapsed_ns == 999
    assert any(item.rss_bytes == 9999 and item.line == 414 for item in samples)
    assert any(item.rss_bytes is None for item in samples)
    assert [item.elapsed_ns for item in samples] == sorted(item.elapsed_ns for item in samples)
    assert compressed


def memory_result():
    """Construct a portable profile with RAM, a spike, and an unavailable gap.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    source = SourceUnit("ram-source", "work<script>.py", "first()\nsecond()\n")
    run = ProfileRun(
        name="Memory <run>",
        lines=[LineStats(SourceLocation(source.id, 1), ram=ProcessMemoryStats(200, 100, 900))],
        memory_samples=[
            MemorySample(0, 100),
            MemorySample(10, 900, SourceLocation(source.id, 1)),
            MemorySample(20, None),
            MemorySample(30, 200, SourceLocation(source.id, 2)),
        ],
    )
    return ProfileResult(run, {source.id: source}, "trace", BackendCapabilities(memory=True))


def test_memory_columns_show_only_change_and_peak_and_keep_unknowns():
    """Render process change and peak without extra memory columns.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    result = memory_result()
    result.root_run.lines[0].memory = MemoryStats(88888, 99999)
    document = ReportDOM(render_html(result)).root
    table = document.find_all("table", css="source-table")[0]
    assert [header.text() for header in table.find_all("th")] == [
        "Line",
        "Python time",
        "Mem Change",
        "Peak Mem",
        "Source",
    ]
    rows = table.find_all("tr", css="source-row")
    assert cell_values(rows[0]) == ["1", "—", "+100 B", "900 B", "first()"]
    assert cell_values(rows[1]) == ["2", "—", "—", "—", "second()"]
    assert "RAM after" not in document.text()
    assert "Python allocation Δ" not in document.text()
    assert "Driver memory" not in document.text()
    assert "Driver peak" not in document.text()
    assert rows[0].attributes["data-allocation"] == "88888"
    assert rows[1].attributes["data-allocation"] == ""


def test_timeline_has_valid_source_links_gaps_and_inspection_controls():
    """Render escaped, navigable observations without drawing through gaps.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    html = render_html(memory_result())
    document = ReportDOM(html).root
    page = document.find_all("section", id="memory")[0]
    assert len(page.find_all("svg", css="memory-chart")) == 1
    assert len(page.find_all("polyline")) == 2
    assert len(page.find_all("polygon")) == 2
    assert page.find_all("input", css="memory-cursor")[0].attributes["max"] == "3"
    assert "work<script>.py" in page.text()
    assert "<script>.py" not in html
    ids = {
        element.attributes["id"] for element in document.find_all() if "id" in element.attributes
    }
    for link in page.find_all("a"):
        assert link.attributes["href"][1:] in ids
    assert document.find_all("a", css="nav-link", href="#memory")


def test_memory_summary_precedes_chart_and_details():
    """Place source-linked summaries before the chart and remaining details.

    Move the measurement explanation into a collapsed disclosure and omit
    the single main run's redundant heading.

    """
    result = memory_result()
    page = ReportDOM(render_html(result)).root.find_all("section", id="memory")[0]
    run = page.find_all("section", css="memory-run")[0]
    assert [child.tag for child in run.children if not isinstance(child, str)] == [
        "dl",
        "div",
        "details",
        "h3",
        "div",
    ]
    badges = run.find_all("dl", css="memory-stats")[0]
    assert [label.text() for label in badges.find_all("dt")] == [
        "Peak memory",
        "Largest line change",
    ]
    assert [value.text() for value in badges.find_all("strong")] == ["900 B", "+100 B"]
    assert len(badges.find_all("a")) == 2
    assert badges.find_all("a")[0].attributes["href"] == badges.find_all("a")[1].attributes["href"]
    assert result.root_run.name not in page.text()
    assert not page.find_all("p", css="semantics")
    explanation = page.find_all("details", css="memory-explanation")[0]
    assert "open" not in explanation.attributes
    assert "RSS is resident RAM for the whole Python process" in explanation.text()
    assert "open" not in run.find_all("details", css="memory-readings")[0].attributes


@pytest.mark.parametrize(
    ("rss", "delta", "expected"),
    [
        (None, None, ["—", "—"]),
        (0, 0, ["0 B", "0 B"]),
        (200, -100, ["200 B", "-100 B"]),
    ],
)
def test_memory_badges_preserve_unknown_zero_and_negative_values(rss, delta, expected):
    """Distinguish unavailable badges from measured zero and signed changes.

    Parameters
    ----------
    rss : int | None
        Controlled RAM reading in bytes, including unavailable and zero.

    delta : int | None
        Accumulated process-memory change for the measured source line.

    expected : list[str]
        Expected peak and change badge values in display order.

    """
    result = memory_result()
    result.root_run.memory_samples = [MemorySample(0, rss)]
    result.root_run.lines[0].ram = ProcessMemoryStats(delta_bytes=delta)
    page = ReportDOM(render_html(result)).root.find_all("section", id="memory")[0]
    badges = page.find_all("dl", css="memory-stats")[0]
    assert [value.text() for value in badges.find_all("strong")] == expected
    assert bool(page.find_all("svg", css="memory-chart")) is (rss is not None)


def test_memory_badge_uses_largest_accumulated_line_change():
    """Select the largest measured line change independently of the RAM peak.

    Ignore unavailable line changes and do not derive line growth from the
    difference between retained timeline readings.

    """
    result = memory_result()
    result.root_run.lines.extend(
        [
            LineStats(SourceLocation("ram-source", 1), ram=ProcessMemoryStats()),
            LineStats(SourceLocation("ram-source", 2), ram=ProcessMemoryStats(delta_bytes=500)),
        ],
    )
    page = ReportDOM(render_html(result)).root.find_all("section", id="memory")[0]
    badges = page.find_all("dl", css="memory-stats")[0]
    assert [value.text() for value in badges.find_all("strong")] == ["900 B", "+500 B"]
    assert badges.find_all("a")[-1].text() == "work<script>.py:2"


def test_child_process_ram_is_not_added_to_parent():
    """Keep child RSS and its timeline separate from the parent's usage.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    result = memory_result()
    child = ProfileRun(
        name="Child process",
        lines=[LineStats(SourceLocation("ram-source", 1), ram=ProcessMemoryStats(400, 250, 1200))],
        memory_samples=[MemorySample(0, 400, SourceLocation("ram-source", 1))],
        metadata={"child_capabilities": asdict(result.capabilities)},
    )
    result.root_run.children.append(child)
    document = ReportDOM(render_html(result)).root
    assert len(document.find_all("svg", css="memory-chart")) == 2
    page = document.find_all("section", id="memory")[0]
    summaries = page.find_all("dl", css="memory-stats")
    assert [[value.text() for value in badge.find_all("strong")] for badge in summaries] == [
        ["900 B", "+100 B"],
        ["400 B", "+250 B"],
    ]
    assert [heading.text() for heading in page.find_all("h2")] == [
        "Main run",
        "Process RAM over time",
        "Child process",
        "Process RAM over time",
    ]
    gradients = page.find_all("lineargradient")
    assert len({gradient.attributes["id"] for gradient in gradients}) == 2
    row = document.find_all("tr", css="source-row")[0]
    assert cell_values(row)[2:4] == ["—", "1.2 KB"]
    assert row.attributes["data-memory"] == ""
    assert row.attributes["data-heat-memory"] == "0.00000"
    assert result.root_run.lines[0].ram.rss_bytes == 200


def test_growth_ranking_preserves_measured_changes_among_unknowns():
    """Keep measured changes visible when other line readings are unavailable.

    Filter unknown values before limiting the ranking so they cannot displace
    measured negative changes.

    """
    result = memory_result()
    result.root_run.lines = [
        LineStats(SourceLocation("ram-source", 1), ram=ProcessMemoryStats()) for _ in range(12)
    ] + [LineStats(SourceLocation("ram-source", 2), ram=ProcessMemoryStats(200, -100, 900))]
    document = ReportDOM(render_html(result)).root
    page = document.find_all("section", id="memory")[0]
    ranking = page.find_all("table")[-1]
    rows = ranking.find_all("tr")[1:]
    assert len(rows) == 1
    assert cell_values(rows[0]) == ["-100 B", "900 B", "work<script>.py:2"]


def test_ram_serialization_roundtrip_and_profiles_without_ram():
    """Transport RAM models and accept existing profiles without new fields.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    result = memory_result()
    assert loads_result(dumps_result(result)) == result
    encoded = json.loads(dumps_result(result))
    del encoded["result"]["root_run"]["memory_samples"]
    for line in encoded["result"]["root_run"]["lines"]:
        del line["ram"]
    restored = loads_result(json.dumps(encoded).encode())
    assert restored.root_run.memory_samples == []
    assert restored.root_run.lines[0].ram is None


def test_live_refresh_does_not_accumulate_ram(tmp_path, controlled_ram):
    """Refresh detached process readings without counting their changes twice.

    Inspect controlled RAM observations, source intervals, and timeline
    snapshots without relying on exact timing.

    """
    path = tmp_path / "live.py"
    path.write_text("state['rss'] = 300\n", encoding="utf-8")
    with Session(
        backend="trace",
        memory=True,
        root=str(tmp_path),
        display="none",
        spark=False,
        notebooks=False,
    ) as session:
        exec(compile(path.read_text(), str(path), "exec"), {"state": controlled_ram})
        session._refresh()
        first = session.result.root_run.lines[0].ram
        session._refresh()
        assert session.result.root_run.lines[0].ram == first
        assert session.result.root_run.lines[0].ram is not first
    assert first == ProcessMemoryStats(300, 200, 300)
