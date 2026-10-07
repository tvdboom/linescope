"""LineScope.

Author: Mavs
Description: Validate configurable sampling and portable observation counts.

"""

from collections import defaultdict
from dataclasses import asdict
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from linescope import Config, Session, cli, profile
from linescope.backends import tachyon
from linescope.backends.base import RawBackendResult, RawLine
from linescope.backends.scalene import ScaleneBackend, normalize_scalene
from linescope.backends.tachyon import TachyonBackend
from linescope.config import resolve_config
from linescope.enums import Backend, SourceKind
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
)
from linescope.notebooks.serialization import dumps_result, loads_result
from linescope.render import render_html
from tests.test_render import ReportDOM, cell_values
from tests.test_tachyon import FakeProcess


@pytest.mark.parametrize("value", [True, False, 1.5, "250", float("inf")])
def test_non_integer_sampling_rate_rejected(value):
    """Verify non integer sampling rate rejected.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    with pytest.raises(TypeError, match="sample_rate"):
        Config(sample_rate=value)


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_sampling_rate_rejected(value):
    """Verify non positive sampling rate rejected.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    for factory in (Config, ScaleneBackend, TachyonBackend):
        options = (
            {} if factory is Config else {"accepts": lambda _: True, "on_source": lambda _: None}
        )
        with pytest.raises(ValueError, match="sample_rate"):
            factory(sample_rate=value, **options)


def test_sampling_rate_project_and_profile_precedence(tmp_path):
    """Keep sampling overrides local to each profile and reread project TOML.

    Preserve existing session options when later sessions read edited defaults.

    """
    project = tmp_path / "pyproject.toml"
    project.write_text("[tool.linescope]\nsample_rate = 200\n")
    assert Config().sample_rate is None
    assert resolve_config(root=tmp_path).sample_rate == 200
    session = profile(root=tmp_path, sample_rate=300)
    assert session.config.sample_rate == 300
    assert profile(root=tmp_path).config.sample_rate == 200
    with pytest.raises(ValueError, match="sample_rate"):
        profile(root=tmp_path, sample_rate=0)
    assert profile(root=tmp_path).config.sample_rate == 200
    assert profile(root=tmp_path, sample_rate=None).config.sample_rate is None
    project.write_text("[tool.linescope]\nsample_rate = 400\n")
    assert profile(root=tmp_path).config.sample_rate == 400
    assert session.config.sample_rate == 300


@pytest.mark.parametrize("value", ["0", "-5", "1.5", "invalid"])
def test_cli_rejects_invalid_sampling_rate(value):
    """Verify cli rejects invalid sampling rate.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    result = CliRunner().invoke(cli.main, ["--sample-rate", value, "worker.py"])
    assert result.exit_code == 2
    assert "--sample-rate" in result.stderr


@pytest.mark.parametrize("backend", [Backend.SCALENE, Backend.TACHYON])
def test_sampling_backends_default_to_1000_samples_per_second(backend):
    """Use the same default sampling frequency for both sampling collectors.

    Inspect construction without starting optional profiler runtimes.

    """
    collector_type = ScaleneBackend if backend == Backend.SCALENE else TachyonBackend
    collector = collector_type(accepts=lambda _: True, on_source=lambda _: None)
    assert collector.sample_rate == 1000
    if isinstance(collector, ScaleneBackend):
        assert collector._interval == pytest.approx(0.001)


@pytest.mark.parametrize("backend", [Backend.SCALENE, Backend.TACHYON])
def test_session_and_cli_forward_rate_and_aggregate_own_samples(tmp_path, monkeypatch, backend):
    """Verify session and cli forward rate and aggregate own samples.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    path = tmp_path / "worker.py"
    path.write_text(
        "def outer():\n"
        "    value = 1\n"
        "    def inner():\n"
        "        return 2\n"
        "    return value\n"
        "def untouched():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    options = []
    stopped = []

    def factory(name, **kwargs):
        """Return a controlled collector and retain forwarded factory options.

        Use controlled collector samples and worker messages to inspect sampling
        rates, observation counts, and unknown measurements.

        """
        options.append(kwargs)
        return SimpleNamespace(
            name=name,
            sample_rate=kwargs["sample_rate"],
            capabilities=BackendCapabilities(sampled=True, sample_counts=True),
            start=lambda: kwargs["on_source"](str(path)),
            stop=lambda: stopped.append(True),
            result=lambda: RawBackendResult(
                [
                    RawLine(str(path), 2, 100, samples=2),
                    RawLine(str(path), 2, 200, samples=3),
                    RawLine(str(path), 4, 300, samples=7),
                    RawLine(str(path), 5, 400, samples=11),
                ]
            ),
        )

    monkeypatch.setattr("linescope.api.create_backend", factory)
    session = Session(
        backend=backend,
        sample_rate=250,
        root=tmp_path,
        display="none",
        spark=False,
        notebooks=False,
    )

    def workload():
        """Execute the controlled workload while collection is active.

        Use controlled collector samples and worker messages to inspect sampling
        rates, observation counts, and unknown measurements.

        """
        with session:
            session._refresh()
            session._refresh()
            raise RuntimeError("workload")

    with pytest.raises(RuntimeError, match="workload"):
        workload()
    assert stopped == [True]
    rows = {line.location.line: line for line in session.result.root_run.lines}
    assert rows[2].samples == 5
    assert rows[2].hits is None
    functions = {
        function.qualified_name: function for function in session.result.root_run.functions
    }
    assert functions["outer"].samples == 16
    assert functions["outer.inner"].samples == 7
    assert functions["untouched"].samples == 0
    assert {name: function.line_count for name, function in functions.items()} == {
        "outer": 5,
        "outer.inner": 2,
        "untouched": 2,
    }
    assert all(function.calls is None for function in functions.values())
    assert session.result.root_run.metadata["sample_rate"] == 250

    result = CliRunner().invoke(
        cli.main,
        [
            "--backend",
            backend,
            "--sample-rate",
            "500",
            "--display",
            "none",
            "--no-spark",
            "--no-notebooks",
            "-o",
            str(tmp_path / "report.html"),
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert [option["sample_rate"] for option in options] == [250, 500]


def test_trace_ignores_rate_and_keeps_samples_unknown(tmp_path):
    """Verify trace ignores rate and keeps samples unknown.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    path = tmp_path / "worker.py"
    path.write_text("def work():\n    return 1\nwork()\n", encoding="utf-8")
    with Session(
        backend="trace",
        sample_rate=500,
        root=tmp_path,
        spark=False,
        notebooks=False,
        display="none",
    ) as session:
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), {})
    assert all(line.samples is None for line in session.result.root_run.lines)
    assert all(function.samples is None for function in session.result.root_run.functions)
    assert "sample_rate" not in session.result.root_run.metadata
    assert "Samples" not in ReportDOM(render_html(session.result)).root.text()


def test_sampling_option_preserves_custom_factory_contract(monkeypatch):
    """Verify sampling option preserves custom factory contract.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """

    def factory(*, accepts, on_source, memory, root):
        """Return a controlled collector and retain forwarded factory options.

        Use controlled collector samples and worker messages to inspect sampling
        rates, observation counts, and unknown measurements.

        """
        del accepts, on_source, memory, root
        return SimpleNamespace(
            name="custom",
            capabilities=BackendCapabilities(),
            start=lambda: None,
            stop=lambda: None,
            result=RawBackendResult,
        )

    monkeypatch.setattr("linescope.backends.base._factories", {"custom": factory})
    with Session(backend="custom", sample_rate=250, spark=False, notebooks=False, display="none"):
        pass


def test_tachyon_worker_uses_configured_microsecond_and_wait_intervals(monkeypatch):
    """Verify tachyon worker uses configured microsecond and wait intervals.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    captured = {}
    frame = SimpleNamespace(filename="project.py", location=SimpleNamespace(lineno=3))
    thread = SimpleNamespace(thread_id=456, frame_info=[frame])

    class Sampler:
        """Expose controlled stack snapshots without attaching to a process.

        Read configured thread and frame data without process-memory access.

        """

        def __init__(self, pid, interval, **options):
            """Initialize the controlled test state and recorded observations.

            Retain only the state needed to observe arguments, results, and
            cleanup in the surrounding test.

            """
            captured.update(pid=pid, interval=interval, **options)

        def dump_stack(self):
            """Return a controlled sampled stack for worker interval assertions.

            Preserve the configured thread and frame data for attribution
            assertions.

            """
            return [SimpleNamespace(threads=[thread])]

    class StopEvent:
        """Stop a simulated sampling loop after its recorded wait intervals.

        Retain wait intervals so configured sampling rates can be checked.

        """

        def wait(self, interval):
            """Simulate process completion or a configured termination timeout.

            Record completion or the configured wait behavior for shutdown
            assertions.

            """
            waits = captured.setdefault("waits", [])
            waits.append(interval)
            return len(waits) > 1

    monkeypatch.setattr(
        tachyon.importlib, "import_module", lambda _: SimpleNamespace(SampleProfiler=Sampler)
    )
    monkeypatch.setattr(tachyon.threading, "Event", StopEvent)
    monkeypatch.setattr(
        tachyon.threading, "Thread", lambda **_options: SimpleNamespace(start=lambda: None)
    )
    messages = []
    monkeypatch.setattr(tachyon, "_send", messages.append)
    times = iter([100, 300])
    monkeypatch.setattr(tachyon, "perf_counter_ns", lambda: next(times))
    tachyon._run(123, 456, 250)
    assert captured == {"pid": 123, "interval": 4000, "all_threads": True, "waits": [0.004, 0.004]}
    assert messages[1]["frames"] == [["project.py", 3]]
    assert messages[1]["duration_ns"] == 200


def test_tachyon_passes_rate_to_worker_and_counts_external_attribution(monkeypatch):
    """Verify tachyon passes rate to worker and counts external attribution.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    process = FakeProcess([{"type": "ready"}])
    commands = []

    def popen(command, **kwargs):
        """Record the external sampler command and return its fake process.

        Use controlled collector samples and worker messages to inspect sampling
        rates, observation counts, and unknown measurements.

        """
        del kwargs
        commands.append(command)
        return process

    monkeypatch.setattr("linescope.backends.tachyon.sys.version_info", (3, 15))
    monkeypatch.setattr("linescope.backends.tachyon.subprocess.Popen", popen)
    collector = TachyonBackend(
        accepts=lambda name: name == "project.py", on_source=lambda _: None, sample_rate=250
    )
    collector.start()
    assert commands[0][-1] == "250"
    collector._collect([["dependency.py", 9], ["project.py", 3]], 123)
    collector._collect([["project.py", 3]], 456)
    collector.stop()
    line = collector.result().lines[0]
    assert (line.samples, line.hits, line.wall_time_ns) == (2, None, 579)
    line.samples = 99
    assert collector.result().lines[0].samples == 2


def test_scalene_counts_thread_observations_before_upstream_consumes_frames(monkeypatch):
    """Check the expected behavior in this regression case.

    Verify scalene counts thread observations before upstream consumes frames.

    """
    monkeypatch.setattr("linescope.backends.scalene.sys.platform", "win32")
    collector = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None, sample_rate=250)
    assert collector._interval == pytest.approx(0.004)
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename="project.py"), f_lineno=3)
    collector._frames = lambda _: [(frame, 1, frame), (frame, 2, frame), (frame, 3, frame)]
    collector._sleeping = {1: False, 2: False, 3: True}
    collector._time = lambda: "current"
    collector._previous = "previous"
    collector._processor = SimpleNamespace(
        process_cpu_sample=lambda frames, *_args: frames.clear()
    )
    collector._collect_sample()
    assert collector._sample_counts == {("project.py", 3): 2}
    assert collector._sampling_error is None
    collector._processor.process_cpu_sample = lambda *_args: (_ for _ in ()).throw(
        ValueError("bad")
    )
    collector._collect_sample()
    assert collector._sample_counts == {("project.py", 3): 2}
    assert "bad" in collector._sampling_error
    assert not collector._sampling


def test_scalene_exports_integer_counts_without_deriving_them_from_time():
    """Verify scalene exports integer counts without deriving them from time.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    collector = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None)
    mapping = lambda: defaultdict(lambda: defaultdict(float))
    collector._stats = SimpleNamespace(
        cpu_stats=SimpleNamespace(cpu_samples_python=mapping(), cpu_samples_c=mapping()),
        memory_stats=SimpleNamespace(),
        gpu_stats=SimpleNamespace(),
    )
    collector._stats.cpu_stats.cpu_samples_python["project.py"][4] = 0.5
    collector._sample_counts[("project.py", 3)] = 7
    collector._json = SimpleNamespace(
        output_profile_line=lambda **kwargs: {"lineno": kwargs["line_no"]}
    )
    lines = collector._export(1).lines
    assert [(line.line, line.samples) for line in lines] == [(3, 7), (4, 0)]
    assert all(line.hits is None for line in lines)


def test_scalene_posix_reschedules_using_configured_rate(monkeypatch):
    """Verify scalene posix reschedules using configured rate.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    monkeypatch.setattr("linescope.backends.scalene.sys.platform", "linux")
    collector = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None, sample_rate=250)
    collector._running = True
    collector._time = lambda: "current"
    collector._previous = "previous"
    collector._frames = lambda _: []
    collector._sleeping = {}
    collector._processor = SimpleNamespace(process_cpu_sample=lambda *_args: None)
    rates, timers = [], []

    def next_interval(rate):
        """Record the configured sampling rate and return a controlled delay.

        Use controlled collector samples and worker messages to inspect sampling
        rates, observation counts, and unknown measurements.

        """
        rates.append(rate)
        return 0.003

    collector._random.expovariate = next_interval
    monkeypatch.setattr("linescope.backends.scalene.signal.ITIMER_REAL", 0, raising=False)
    monkeypatch.setattr(
        "linescope.backends.scalene.signal.setitimer",
        lambda *args: timers.append(args),
        raising=False,
    )
    collector._collect_sample()
    assert rates == [250]
    assert timers[0][1] == 0.003


@pytest.mark.parametrize("samples", [None, -1, 1.5, True, float("inf")])
def test_scalene_missing_or_invalid_counts_remain_unknown(samples):
    """Verify scalene missing or invalid counts remain unknown.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    result = normalize_scalene(
        {"files": {"project.py": {"lines": [{"lineno": 3, "samples": samples}]}}}
    )
    assert result.lines[0].samples is None


@pytest.mark.parametrize("known", [False, True])
def test_sampling_counts_in_all_report_views_and_serialization(known):
    """Verify sampling counts in all report views and serialization.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    unit = SourceUnit(
        "source", "cell", "def work():\n    return 1\n# unobserved", SourceKind.NOTEBOOK
    )
    line = LineStats(SourceLocation(unit.id, 2), 10_000_000, samples=1234 if known else None)
    run = ProfileRun(
        lines=[line],
        functions=[
            FunctionStats(unit.id, "work", 1, 10_000_000, samples=line.samples, line_count=2)
        ],
        elapsed_ns=2_000_000_000,
        metadata={"sample_rate": 250},
    )
    result = ProfileResult(
        run, {unit.id: unit}, "scalene", BackendCapabilities(sampled=True, sample_counts=known)
    )
    restored = loads_result(dumps_result(result))
    assert restored.root_run.lines[0].samples == line.samples
    assert restored.root_run.functions[0].samples == line.samples
    assert restored.root_run.functions[0].line_count == 2
    assert restored.capabilities.sample_counts is known
    assert restored.root_run.metadata["sample_rate"] == 250
    document = ReportDOM(render_html(restored)).root
    expected = "1,234" if known else "—"
    for page_id in ("overview", "files"):
        page = document.find_all("section", id=page_id)[0]
        table = page.find_all("table")[0]
        assert table.find_all("th")[2].text() == "Samples"
        assert cell_values(table.find_all("tr")[-1])[2] == expected
    function_table = document.find_all("section", id="functions")[0].find_all("table")[0]
    assert [header.text() for header in function_table.find_all("th")] == [
        "Time",
        "Location",
        "Samples",
        "Source",
        "Lines",
    ]
    assert cell_values(function_table.find_all("tr")[-1]) == [
        "10.00 ms",
        "cell:1",
        expected,
        "work()",
        "2",
    ]
    rows = document.find_all("tr", css="source-row")
    table = document.find_all("table", css="source-table")[0]
    assert [header.text() for header in table.find_all("thead")[0].find_all("th")] == (
        ["", "Time", "Samples", "Source"] if known else ["", "Time", "Source"]
    )
    assert cell_values(rows[1])[1] == "10.00 ms"
    assert cell_values(rows[2])[1] == "—"
    if known:
        assert cell_values(rows[1])[2] == expected
        assert cell_values(rows[2])[2] == "0"
    assert "Target samples / sec" not in document.text()
    assert f"Measured samples / sec{'617.0' if known else '—'}" in document.text()


def test_merged_sampling_counts_add_without_mutating_originals():
    """Verify merged sampling counts add without mutating originals.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    unit = SourceUnit("source", "worker.py", "work()")
    parent = LineStats(SourceLocation(unit.id, 1), 1000, samples=3)
    child = LineStats(SourceLocation(unit.id, 1), 2000, samples=5)
    capabilities = BackendCapabilities(sampled=True, sample_counts=True)
    child_run = ProfileRun(lines=[child], metadata={"child_capabilities": asdict(capabilities)})
    result = ProfileResult(
        ProfileRun(lines=[parent], children=[child_run]), {unit.id: unit}, "scalene", capabilities
    )
    document = ReportDOM(render_html(result)).root
    assert cell_values(document.find_all("tr", css="source-row")[0])[2] == "8"
    assert parent.samples == 3
    assert child.samples == 5
    child.samples = None
    child_run.metadata["child_capabilities"] = asdict(BackendCapabilities(sampled=True))
    document = ReportDOM(render_html(result)).root
    table = document.find_all("table", css="source-table")[0]
    assert [header.text() for header in table.find_all("th")] == [
        "",
        "Time",
        "Source",
    ]
    assert cell_values(document.find_all("tr", css="source-row")[0]) == ["1", "3.0 µs", "work()"]


@pytest.mark.parametrize("known", [False, True])
def test_empty_main_run_preserves_child_sample_count_availability(known):
    """Verify empty main run preserves child sample count availability.

    Use controlled collector samples and worker messages to inspect sampling
    rates, observation counts, and unknown measurements.

    """
    unit = SourceUnit("child", "child.py", "work()")
    child = ProfileRun(
        source=unit,
        metadata={
            "child_capabilities": asdict(BackendCapabilities(sampled=True, sample_counts=known))
        },
    )
    result = ProfileResult(
        ProfileRun(children=[child]),
        {unit.id: unit},
        "scalene",
        BackendCapabilities(sampled=True, sample_counts=True),
    )
    document = ReportDOM(render_html(result)).root
    card = next(
        card
        for card in document.find_all("div", css="stat")
        if card.find_all("span")[0].text() == "Samples"
    )
    assert card.find_all("strong")[0].text() == ("0" if known else "—")


@pytest.mark.parametrize(
    ("samples", "counts_known", "elapsed_ns", "expected"),
    [
        ([205], True, 3_590_000_000, "57.1"),
        ([1234], True, 1_000_000_000, "1,234.0"),
        ([0], True, 1_000_000_000, "0.0"),
        ([], True, 1_000_000_000, "0.0"),
        ([], False, 1_000_000_000, "—"),
        ([None], True, 1_000_000_000, "—"),
        ([4, None], True, 1_000_000_000, "—"),
        ([4], True, 0, "—"),
    ],
)
def test_measured_sampling_rate_preserves_zero_and_unknown_counts(
    samples, counts_known, elapsed_ns, expected
):
    """Calculate observed frequency without needing target-rate metadata.

    Leave missing counts, incomplete counts, and zero elapsed time unavailable.
    An empty run has zero observations only when its collector reports counts.

    """
    source = SourceUnit("source", "worker.py", "work()\nother()\n")
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(source.id, index), wall_time_ns=1000, samples=count)
                for index, count in enumerate(samples, start=1)
            ],
            elapsed_ns=elapsed_ns,
        ),
        {source.id: source},
        "scalene",
        BackendCapabilities(sampled=True, sample_counts=counts_known),
    )
    overview = ReportDOM(render_html(result)).root.find_all("section", id="overview")[0]
    cards = {
        card.find_all("span")[0].text(): card.find_all("strong")[0].text()
        for card in overview.find_all("div", css="stat")
    }
    assert cards["Measured samples / sec"] == expected


@pytest.mark.parametrize("target", [250, 1000, '<img src="x" onerror="alert(1)">'])
def test_measured_sampling_rate_uses_main_run_observations_only(target):
    """Keep overlapping child observations and target settings out of the rate.

    Use the main run's own counts and wall time, even when a child shares
    source locations or the configured target contains untrusted markup.

    """
    source = SourceUnit("source", "worker.py", "work()\n# unobserved\n")
    capabilities = BackendCapabilities(sampled=True, sample_counts=True)
    child = ProfileRun(
        lines=[LineStats(SourceLocation(source.id, 1), samples=9000)],
        metadata={"child_capabilities": asdict(capabilities)},
    )
    result = ProfileResult(
        ProfileRun(
            lines=[
                LineStats(SourceLocation(source.id, 1), samples=10),
                LineStats(SourceLocation(source.id, 2)),
            ],
            children=[child],
            elapsed_ns=2_000_000_000,
            metadata={"sample_rate": target},
        ),
        {source.id: source},
        "scalene",
        capabilities,
    )
    overview = ReportDOM(render_html(result)).root.find_all("section", id="overview")[0]
    assert "Samples9,010" in overview.text()
    assert "Measured samples / sec5.0" in overview.text()
    assert "Target samples / sec" not in overview.text()
    assert not overview.find_all("img")
