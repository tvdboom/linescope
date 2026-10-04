"""LineScope.

Author: Mavs
Description: Scalene metric normalization, lifecycle, and real collector tests.

"""

from importlib.metadata import PackageNotFoundError
from importlib.util import find_spec
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from linescope.backends import scalene
from linescope.backends.scalene import ScaleneBackend, normalize_scalene


def payload(line, *, elapsed=2.0, filename="/project/main.py"):
    """Provide payload.

    Wrap a line in the upstream JSON format.

    """
    return {"elapsed_time_sec": elapsed, "files": {filename: {"lines": [{"lineno": 4, **line}]}}}


class TestNormalizeScalene:
    """Check normalize scalene.

    Tests for optional metrics and unit semantics.

    """

    def test_python_native_and_system_share(self):
        """Check python native and system share.

        All three cost domains stay charged to the source line.

        """
        result = normalize_scalene(
            payload({"n_cpu_percent_python": 10, "n_cpu_percent_c": 20, "n_sys_percent": 30})
        )
        assert result.lines[0].wall_time_ns == 1_200_000_000
        assert result.lines[0].hits is None
        assert "sampled" in result.warnings[0]

    def test_available_zero_does_not_become_missing(self):
        """Check available zero does not become missing.

        A real reported zero stays distinguishable from missing data.

        """
        assert normalize_scalene(payload({"n_cpu_percent_python": 0})).lines[0].wall_time_ns == 0
        assert normalize_scalene(payload({})).lines[0].wall_time_ns is None

    @pytest.mark.parametrize("elapsed", [None, -1, float("nan"), "invalid"])
    def test_invalid_elapsed_remains_unknown(self, elapsed):
        """Check invalid elapsed remains unknown.

        An absent or invalid run duration never produces invented time.

        """
        result = normalize_scalene(payload({"n_cpu_percent_python": 10}, elapsed=elapsed))
        assert result.lines[0].wall_time_ns is None

    @pytest.mark.parametrize("number", [-1, 0, 1.5, None, True, "invalid", float("inf")])
    def test_invalid_line_numbers_ignored(self, number):
        """Check invalid line numbers ignored.

        Only positive integer source line numbers can be rendered.

        """
        assert normalize_scalene(payload({"lineno": number})).lines == []

    def test_memory_conversion_and_negative_delta(self):
        """Check memory conversion and negative delta.

        Allocation minus free volume is a signed byte delta.

        """
        row = {"n_malloc_mb": 2, "n_free_mb": 3, "n_peak_mb": 8, "n_growth_mb": 900}
        memory = normalize_scalene(payload(row), memory=True).lines[0].memory
        assert memory.delta_bytes == -(1024**2)
        assert memory.peak_bytes == 8 * 1024**2

    def test_upstream_growth_is_not_delta(self):
        """Check upstream growth is not delta.

        Scalene's legacy peak alias must never masquerade as a delta.

        """
        row = {"n_growth_mb": 10, "n_peak_mb": 10, "n_malloc_mb": 20}
        memory = normalize_scalene(payload(row), memory=True).lines[0].memory
        assert memory.delta_bytes is None
        assert memory.peak_bytes == 10 * 1024**2

    def test_disabled_or_absent_memory(self):
        """Check disabled or absent memory.

        Memory is absent when disabled or unavailable.

        """
        assert normalize_scalene(payload({"n_peak_mb": 8})).lines[0].memory is None
        assert normalize_scalene(payload({}), memory=True).lines[0].memory is None

    def test_filter_third_party(self):
        """Check filter third party.

        Normalization can exclude files without touching their source.

        """
        result = normalize_scalene(
            payload({}, filename="/site-packages/pandas.py"), accepts=lambda _: False
        )
        assert result.lines == []

    def test_invalid_percentages(self):
        """Check invalid percentages.

        Noise and invalid upstream values cannot create negative durations.

        """
        row = {"n_cpu_percent_python": -5, "n_cpu_percent_c": 200, "n_sys_percent": float("nan")}
        assert normalize_scalene(payload(row)).lines[0].wall_time_ns == 2_000_000_000


class TestScaleneSetup:
    """Check scalene setup.

    Tests for optional imports and conflicting instrumentation.

    """

    @pytest.mark.parametrize("python_version", [(3, 11), (3, 12), (3, 13), (3, 14)])
    def test_dependency_missing(self, monkeypatch, python_version):
        """Check dependency missing.

        An unavailable default engine gives explicit installation guidance.

        """
        monkeypatch.setattr(scalene.sys, "version_info", python_version)

        def missing(_):
            raise PackageNotFoundError

        monkeypatch.setattr(scalene, "version", missing)
        with pytest.raises(ImportError, match=r"pip install linescope") as error:
            scalene._load_scalene()
        assert "optional dependency" not in str(error.value)
        assert "linescope[scalene]" not in str(error.value)

    def test_version_guard(self, monkeypatch):
        """Check version guard.

        Private upstream contracts are constrained to a tested minor series.

        """
        monkeypatch.setattr(scalene.sys, "version_info", (3, 14))
        monkeypatch.setattr(scalene, "version", lambda _: "9.0.0")
        with pytest.raises(RuntimeError, match="not supported"):
            scalene._load_scalene()

    @pytest.mark.parametrize("existing", [None, "en_US.UTF-8"])
    def test_import_preserves_locale_environment(self, monkeypatch, existing):
        """Check import preserves locale environment.

        Importing Scalene cannot change the user's locale settings.

        """
        if existing is None:
            monkeypatch.delenv("LC_ALL", raising=False)
        else:
            monkeypatch.setenv("LC_ALL", existing)

        def package(_):
            os.environ["LC_ALL"] = "POSIX"
            return SimpleNamespace()

        monkeypatch.setattr(scalene.sys, "version_info", (3, 14))
        monkeypatch.setattr(scalene, "version", lambda _: "2.3.0")
        monkeypatch.setattr(scalene.importlib, "import_module", package)
        scalene._load_scalene()
        assert os.environ.get("LC_ALL") == existing

    def test_source_snapshot_once(self):
        """Check source snapshot once.

        Accepted filenames invoke the source callback on first observation.

        """
        observed = []
        backend = ScaleneBackend(
            accepts=lambda filename: filename == "main.py", on_source=observed.append
        )
        assert backend._should_trace("main.py")
        assert backend._should_trace("main.py")
        assert not backend._should_trace("dependency.py")
        assert observed == ["main.py"]

    def test_existing_profile_hook(self, monkeypatch):
        """Check existing profile hook.

        Profiling never overwrites another active profile callback.

        """
        monkeypatch.setattr(scalene.sys, "getprofile", lambda: object())
        backend = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None)
        with pytest.raises(RuntimeError, match="hook is already installed"):
            backend.start()

    def test_worker_thread_rejected(self):
        """Check worker thread rejected.

        Main-thread requirements are checked before importing dependencies.

        """
        errors = []
        backend = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None)

        def start():
            try:
                backend.start()
            except RuntimeError as error:
                errors.append(str(error))

        worker = threading.Thread(target=start)
        worker.start()
        worker.join()
        assert "main Python thread" in errors[0]

    def test_overlapping_collectors(self, monkeypatch):
        """Check overlapping collectors.

        Concurrent sampler ownership is rejected explicitly.

        """
        monkeypatch.setattr(scalene, "_ACTIVE", object())
        with pytest.raises(RuntimeError, match="already running"):
            ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None).start()

    def test_unstarted_stop_is_safe(self):
        """Check unstarted stop is safe.

        Cleanup is idempotent before collection begins.

        """
        backend = ScaleneBackend(accepts=lambda _: True, on_source=lambda _: None)
        backend.stop()
        assert backend.result().lines == []


class TestMemoryBootstrap:
    """Check memory bootstrap.

    Tests for exact CLI argument propagation into the preloaded interpreter.

    """

    def test_bootstrap_preserves_args_and_environment(self, monkeypatch):
        """Check bootstrap preserves args and environment.

        The workload executes once in a child with native startup settings.

        """
        from linescope import cli

        monkeypatch.setattr(cli.sys, "platform", "linux")
        monkeypatch.delenv("LINESCOPE_MEMORY_BOOTSTRAPPED", raising=False)
        monkeypatch.setenv("PROJECT_SETTING", "preserved")
        monkeypatch.setattr(
            scalene, "memory_preload_environment", lambda: {"LD_PRELOAD": "libscalene.so"}
        )
        captured = {}

        def run(command, **kwargs):
            captured.update(command=command, **kwargs)
            return SimpleNamespace(returncode=7)

        monkeypatch.setattr(cli.subprocess, "run", run)
        args = ["--memory", "path with spaces.py", "--argument", "value"]
        assert cli._memory_bootstrap({"backend": "scalene", "memory": True}, args) == 7
        assert captured["command"] == [
            sys.executable,
            "-c",
            "from linescope.cli import main; raise SystemExit(main())",
            *args,
        ]
        assert captured["env"]["PROJECT_SETTING"] == "preserved"
        assert captured["env"]["LINESCOPE_MEMORY_BOOTSTRAPPED"] == "1"
        assert captured["env"]["LD_PRELOAD"] == "libscalene.so"

    def test_bootstrap_guard(self, monkeypatch):
        """Check bootstrap guard.

        The reexecuted interpreter cannot recursively relaunch itself.

        """
        from linescope import cli

        monkeypatch.setenv("LINESCOPE_MEMORY_BOOTSTRAPPED", "1")
        assert cli._memory_bootstrap({"backend": "scalene", "memory": True}, []) is None

    @pytest.mark.parametrize("status", [0, 9])
    def test_bootstrap_launches_cli_and_preserves_workload_exit(
        self, tmp_path, monkeypatch, status
    ):
        from linescope import cli

        monkeypatch.setattr(cli.sys, "platform", "linux")
        monkeypatch.delenv("LINESCOPE_MEMORY_BOOTSTRAPPED", raising=False)
        monkeypatch.setattr(scalene, "memory_preload_environment", dict)
        script = tmp_path / "workload.py"
        script.write_text(f"raise SystemExit({status})\n", encoding="utf-8")
        report = tmp_path / "profile.html"
        arguments = [
            "--backend",
            "trace",
            "--display",
            "none",
            "--no-spark",
            "--no-notebooks",
            "-o",
            str(report),
            str(script),
        ]
        assert cli._memory_bootstrap({"backend": "scalene", "memory": True}, arguments) == status
        assert report.is_file()

    @pytest.mark.parametrize("options", [{"memory": False}, {"backend": "trace", "memory": True}])
    def test_other_modes_do_not_bootstrap(self, options):
        """Check other modes do not bootstrap.

        Native preloading belongs exclusively to Scalene memory mode.

        """
        from linescope import cli

        assert cli._memory_bootstrap(options, []) is None


def run_probe(tmp_path, source, *, preload=False):
    """Provide run probe.

    Execute the real native engine in an isolated interpreter.

    """
    specification = find_spec("scalene")
    if specification is None:
        pytest.skip("Install LineScope with Scalene on a supported Python for real backend tests")
    if (
        preload
        and sys.platform == "win32"
        and not (Path(specification.origin or "").parent / "libscalene.dll").is_file()
    ):
        pytest.skip("This Scalene installation does not include the native Windows memory DLL")
    script = tmp_path / "workload.py"
    script.write_text(source, encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), environment.get("PYTHONPATH", "")]
    )
    if preload and sys.platform != "win32":
        environment.update(scalene.memory_preload_environment())
    result = subprocess.run(
        [sys.executable, str(script)], env=environment, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.scalene
class TestRealScalene:
    """Check real scalene.

    Optional real-collector tests; no mocked timing or allocation engine.

    """

    def test_cpu_repeated_sessions_and_cleanup(self, tmp_path):
        """Real samples attribute blocking calls and restore every installed
        hook.

        """
        result = run_probe(
            tmp_path,
            """
import json, os, sys, threading, time
from linescope.backends.scalene import ScaleneBackend
original = (
    os.execvp, threading.Thread.join, threading.Lock, sys.executable, os.environ.get("LC_ALL")
)
reports = []
for duration in (0.15, 0.25):
    backend = ScaleneBackend(accepts=lambda f: f == __file__, on_source=lambda f: None)
    backend.start()
    time.sleep(duration)
    backend.stop()
    report = backend.result()
    reports.append({
        "time": sum(line.wall_time_ns or 0 for line in report.lines),
        "hits": [line.hits for line in report.lines], "warnings": report.warnings,
    })
assert original == (
    os.execvp, threading.Thread.join, threading.Lock, sys.executable, os.environ.get("LC_ALL")
)
assert sys.getprofile() is None
assert not any(thread.name == "linescope-scalene" for thread in threading.enumerate())
print(json.dumps(reports))
""",
        )
        assert all(0 < report["time"] < 2_000_000_000 for report in result)
        assert all(all(hit is None for hit in report["hits"]) for report in result)
        assert all(
            not any("failed" in warning for warning in report["warnings"]) for report in result
        )

    def test_native_memory_repeated_sessions(self, tmp_path):
        """Check native memory repeated sessions.

        Native allocation samples remain distinct from timing and hit counts.

        """
        result = run_probe(
            tmp_path,
            """
import json, time
from linescope.backends.scalene import ScaleneBackend
reports = []
for iteration in range(2):
    backend = ScaleneBackend(
        accepts=lambda f: f == __file__, on_source=lambda f: None, memory=True
    )
    backend.start()
    values = [bytearray(2_000_000) for _ in range(40)]
    time.sleep(0.2)
    backend.stop()
    report = backend.result()
    reports.append({
        "memory": [line.memory.peak_bytes for line in report.lines if line.memory],
        "warnings": report.warnings,
    })
print(json.dumps(reports))
""",
            preload=True,
        )
        assert all(any(peak > 1_000_000 for peak in report["memory"]) for report in result)
        assert all(
            not any("failed" in warning for warning in report["warnings"]) for report in result
        )

    def test_source_snapshot_precedes_mutation(self, tmp_path):
        """Call observation freezes original code before the source edits
        itself.

        """
        result = run_probe(
            tmp_path,
            """
import json, pathlib, time
from linescope.backends.scalene import ScaleneBackend
from linescope.source import SourceRegistry
path = pathlib.Path(__file__).with_name("worker.py")
original = (
    "import pathlib, time\\ndef work():\\n"
    "    pathlib.Path(__file__).write_text('changed')\\n    time.sleep(0.1)\\n"
)
path.write_text(original)
namespace = {"__file__": str(path)}
exec(compile(original, str(path), "exec"), namespace)
registry = SourceRegistry(path.parent)
backend = ScaleneBackend(accepts=registry.accepts, on_source=registry.snapshot)
backend.start()
namespace["work"]()
backend.stop()
print(json.dumps({
    "snapshot": registry.snapshot(path).source, "original": original, "changed": path.read_text()
}))
""",
        )
        assert result["snapshot"] == result["original"]
        assert result["changed"] == "changed"

    def test_public_api_live_preview_and_final_report(self, tmp_path):
        """Check public api live preview and final report.

        A live preview leaves the real collector running until context exit.

        """
        result = run_probe(
            tmp_path,
            """
import json, pathlib, time
from linescope import profile
with profile(
    backend="scalene", root=str(pathlib.Path(__file__).parent), display="none",
    notebooks=False, spark=False,
) as session:
    time.sleep(0.15)
    preview = session.html()
    assert session.state == "running"
    time.sleep(0.15)
report = session.html()
print(json.dumps({
    "source": __file__ in str(session.result.sources), "preview": "Sampled estimates" in preview,
    "report": "time.sleep" in report,
    "time": sum(line.wall_time_ns or 0 for line in session.result.root_run.lines),
    "state": session.state,
}))
""",
        )
        assert result["preview"]
        assert result["report"]
        assert result["state"] == "stopped"
        assert result["time"] > 100_000_000
