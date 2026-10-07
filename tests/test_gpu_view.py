"""LineScope.

Author: Mavs
Description: Expose GPU estimates separately from driver costs in report views.

"""

import pytest

from linescope.model import (
    BackendCapabilities,
    GPUStats,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.render import render_html
from tests.test_render import ReportDOM


def test_gpu_view_ranks_device_work_and_links_snapshotted_source():
    """Expose device summaries and GPU-ranked lines on the GPU page.

    Keep driver-dominated work separate from device-dominated work, include
    memory-only and sampled-zero observations, and escape source labels.

    """
    source = SourceUnit(
        "gpu-source",
        "<worker>.py",
        "cpu_work()\nlaunch_kernel()\nmemory_only()\nunknown()\nidle()\n"
        "<img src=x onerror=alert(1)>\n",
    )
    lines = [
        LineStats(SourceLocation(source.id, 1), 1_000_000_000, gpu=GPUStats(100_000_000)),
        LineStats(SourceLocation(source.id, 2), 1_000_000, gpu=GPUStats(2_000_000_000, 1024)),
        LineStats(SourceLocation(source.id, 3), gpu=GPUStats(peak_memory_bytes=4096)),
        LineStats(SourceLocation(source.id, 4), gpu=GPUStats()),
        LineStats(SourceLocation(source.id, 5), gpu=GPUStats(0, 0)),
        LineStats(SourceLocation(source.id, 6), gpu=GPUStats(1000)),
        LineStats(SourceLocation(source.id, 99), gpu=GPUStats(999_000_000_000)),
        LineStats(SourceLocation("missing", 1), gpu=GPUStats(999_000_000_000)),
    ]
    result = ProfileResult(
        ProfileRun(lines=lines, elapsed_ns=3_000_000_000),
        {source.id: source},
        "scalene",
        BackendCapabilities(sampled=True, gpu=True),
    )
    document = ReportDOM(render_html(result)).root
    overview = document.find_all("section", id="overview")[0]
    cards = {
        card.find_all("span")[0].text(): card.find_all("strong")[0].text()
        for card in overview.find_all("div", css="stat")
    }
    assert cards["Elapsed wall time"] == "3.00 s"
    assert "Attributed GPU time" not in cards
    assert "GPU peak memory" not in cards
    assert overview.find_all("a", href="#gpu")
    assert document.find_all("a", css="nav-link", href="#gpu")
    page = document.find_all("section", id="gpu")[0]
    gpu_cards = {
        card.find_all("span")[0].text(): card.find_all("strong")[0].text()
        for card in page.find_all("div", css="stat")
    }
    assert gpu_cards["Attributed GPU time"] == "2.10 s"
    assert gpu_cards["GPU peak memory"] == "4.1 KB"
    table = page.find_all("table")[0]
    assert [heading.text() for heading in table.find_all("th")] == [
        "GPU time",
        "GPU peak memory",
        "Driver time",
        "Location",
        "Source",
    ]
    rows = table.find_all("tbody")[0].find_all("tr")
    assert [row.attributes["data-time"] for row in rows] == [
        "2000000000",
        "100000000",
        "1000",
        "0",
        "",
    ]
    assert rows[0].find_all("td")[2].text() == "1.00 ms"
    assert rows[-1].find_all("td")[0].text() == "—"
    assert rows[-1].attributes["data-driver-time"] == ""
    assert not page.find_all("img")
    assert "<img src=x onerror=alert(1)>" in page.text()
    assert "Attributed GPU time sums available source-line estimates" not in page.text()
    assert "Sampled GPU memory is unavailable" not in page.text()
    ids = {
        element.attributes["id"] for element in document.find_all() if "id" in element.attributes
    }
    for link in page.find_all("a"):
        assert link.attributes["href"][1:] in ids


@pytest.mark.parametrize("metrics", [None, GPUStats(), GPUStats(0, 0)])
def test_gpu_view_distinguishes_missing_measurements_from_zero(metrics):
    """Retain unavailable card values separately from sampled zero.

    Show real sampled zero independently of a run with no usable GPU data.

    """
    source = SourceUnit("source", "workload.py", "compute()\n")
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(source.id, 1), gpu=metrics)]),
        {source.id: source},
        "scalene",
        BackendCapabilities(sampled=True, gpu=True),
    )
    document = ReportDOM(render_html(result)).root
    page = document.find_all("section", id="gpu")[0]
    card_values = [card.find_all("strong")[0].text() for card in page.find_all("div", css="stat")]
    if metrics is None or metrics.time_ns is None:
        assert card_values == ["—", "—"]
        assert "No measurements available" in page.text()
        assert "Sampled GPU memory is unavailable" not in page.text()
        assert "No GPU memory measurements were recorded" in page.text()
    else:
        assert card_values == ["0 µs", "0 B"]
        assert "No measurements available" not in page.text()
        assert not page.find_all("small", css="metric-unavailable")


@pytest.mark.parametrize("peak", [None, 0, 1024])
@pytest.mark.parametrize(
    "reason",
    [
        "Per-process GPU memory is unavailable with NVIDIA WDDM.",
        'GPU memory availability check failed: <img src=x onerror="alert(1)">',
    ],
)
def test_gpu_memory_widgets_show_escaped_recorded_reasons_only_when_unknown(peak, reason):
    """Attach collection reasons to unknown memory widgets without guessing.

    Escape diagnostic text on the GPU page. Keep known
    memory values, including zero, free of unavailable notifications even
    when another collection diagnostic remains in the result.

    """
    source = SourceUnit("source", "workload.py", "compute()\n")
    result = ProfileResult(
        ProfileRun(lines=[LineStats(SourceLocation(source.id, 1), gpu=GPUStats(1_000_000, peak))]),
        {source.id: source},
        "scalene",
        BackendCapabilities(sampled=True, gpu=True),
        warnings=["Spark executor metrics are unavailable.", reason],
    )
    document = ReportDOM(render_html(result)).root
    overview = document.find_all("section", id="overview")[0]
    assert "GPU peak memory" not in overview.text()
    assert not overview.find_all("small", css="metric-unavailable")
    page = document.find_all("section", id="gpu")[0]
    card = next(
        card
        for card in page.find_all("div", css="stat")
        if card.find_all("span")[0].text() == "GPU peak memory"
    )
    notices = card.find_all("small", css="metric-unavailable")
    if peak is None:
        assert card.find_all("strong")[0].text() == "—"
        assert len(notices) == 1
        assert notices[0].text() == reason
        assert not card.find_all("img")
    else:
        assert not notices
    assert "Spark executor metrics" not in card.text()


def test_gpu_view_includes_supported_child_runs_only():
    """Expose GPU collection from a child without changing parent driver scope.

    Resolve exact child source links and preserve the main run's wall time.

    """
    source = SourceUnit("child", "child.py", "device_work()\n")
    child = ProfileRun(
        lines=[LineStats(SourceLocation(source.id, 1), gpu=GPUStats(500_000_000))],
        metadata={
            "child_backend": "scalene",
            "child_capabilities": {"sampled": True, "gpu": True},
        },
    )
    result = ProfileResult(
        ProfileRun(children=[child], elapsed_ns=1_000_000_000),
        {source.id: source},
        "trace",
        BackendCapabilities(hit_counts=True),
    )
    document = ReportDOM(render_html(result)).root
    assert document.find_all("section", id="gpu")
    assert "500.00 ms" in document.find_all("section", id="gpu")[0].text()
    overview = document.find_all("section", id="overview")[0]
    assert "1.00 s" in overview.text()
    assert "Attributed GPU time" not in overview.text()
    assert "GPU peak memory" not in overview.text()


def test_gpu_view_stays_hidden_without_collector_support():
    """Keep the device view absent from an ordinary Python profiling report.

    Collector capabilities control GPU presentation independently of its name.

    """
    result = ProfileResult(ProfileRun(), {}, "scalene", BackendCapabilities(sampled=True))
    document = ReportDOM(render_html(result)).root
    assert not document.find_all("section", id="gpu")
    assert not document.find_all("a", css="nav-link", href="#gpu")
    assert "Attributed GPU time" not in document.find_all("section", id="overview")[0].text()
