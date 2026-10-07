"""LineScope.

Author: Mavs
Description: Exercise sampler ownership and native cleanup with offline engines.

"""

from collections import defaultdict
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope.backends import scalene
from linescope.backends.scalene import ScaleneBackend


def metric_map():
    """Provide sparse upstream metrics without inventing report values.

    Mirror Scalene's nested counters so absent sampling locations remain empty.

    """
    return defaultdict(lambda: defaultdict(float))


@pytest.fixture
def engine(monkeypatch):
    """Supply an offline engine with observable clocks, hooks, and queues.

    Replace operating-system integrations before starting any collector, and
    restore the module's original ownership state after each test.

    """
    stats = SimpleNamespace(
        start_clock=Mock(),
        stop_clock=Mock(),
        elapsed_time=0,
        cpu_stats=SimpleNamespace(cpu_samples_python=metric_map(), cpu_samples_c=metric_map()),
        memory_stats=SimpleNamespace(
            memory_malloc_samples=metric_map(),
            memory_free_samples=metric_map(),
            memory_max_footprint=metric_map(),
        ),
        gpu_stats=SimpleNamespace(n_gpu_samples=metric_map(), gpu_samples=metric_map()),
    )
    native = SimpleNamespace(
        _Scalene__initialized=False,
        _Scalene__args="original arguments",
        _alloc_sigqueue_processor=Mock(),
        _memcpy_sigqueue_processor=Mock(),
        malloc_signal_handler=Mock(),
        free_signal_handler=Mock(),
        memcpy_signal_handler=Mock(),
    )
    queues = [SimpleNamespace(start=Mock(), stop=Mock(), lock=threading.RLock()) for _ in range(2)]
    pywhere = SimpleNamespace(
        **{
            name: Mock()
            for name in (
                "populate_struct",
                "depopulate_struct",
                "register_files_to_profile",
                "set_scalene_done_false",
                "set_scalene_done_true",
            )
        }
    )
    tracer = SimpleNamespace(disable_tracing=Mock(), cleanup_tracer=Mock())
    hooks = SimpleNamespace(profile=None)
    runtime = SimpleNamespace(
        platform="win32",
        getprofile=lambda: hooks.profile,
        gettrace=lambda: None,
        _getframe=lambda _: SimpleNamespace(
            f_code=SimpleNamespace(co_filename="main.py"), f_back=None
        ),
    )

    def setprofile(callback):
        """Retain the installed hook without changing interpreter tracing.

        Make hook ownership observable while keeping coverage instrumentation.

        """
        hooks.profile = callback

    runtime.setprofile = Mock(side_effect=setprofile)
    signals = SimpleNamespace(
        SIGALRM=1,
        SIGXCPU=2,
        SIGXFSZ=3,
        SIGPROF=4,
        ITIMER_REAL=0,
        getsignal=Mock(return_value="original signal"),
        signal=Mock(),
        getitimer=Mock(return_value=(0, 0)),
        setitimer=Mock(),
    )
    processor = SimpleNamespace(process_cpu_sample=Mock())
    memory = SimpleNamespace(set_mapfiles=Mock())
    windows = SimpleNamespace(load_dll=Mock(return_value=True), initialize=Mock(return_value=True))
    json = SimpleNamespace(
        output_profile_line=Mock(
            side_effect=lambda **kwargs: {
                "lineno": kwargs["line_no"],
                "n_cpu_percent_python": 25,
                "n_malloc_mb": 3,
                "n_peak_mb": 4,
                "n_gpu_peak_memory_mb": 5,
            }
        )
    )
    components = {
        "ScaleneStatistics": Mock(return_value=stats),
        "ScaleneCPUProfiler": Mock(return_value=processor),
        "ScaleneJSON": Mock(return_value=json),
        "compute_frames_to_record": Mock(return_value=[]),
        "TimeInfo": SimpleNamespace,
        "get_times": Mock(return_value=(1, 2)),
        "clear_intern_caches": Mock(),
        "ScaleneArguments": SimpleNamespace,
        "ScaleneMapFile": Mock(side_effect=lambda kind: kind),
        "ScaleneMemoryProfiler": Mock(return_value=memory),
        "Scalene": native,
        "ScaleneSigQueue": Mock(side_effect=queues),
        "initialize_tracer": Mock(),
        "get_windows_profiler": Mock(return_value=windows),
        "pynvml": SimpleNamespace(
            NVML_DRIVER_WDDM=0,
            nvmlDeviceGetCount=Mock(return_value=1),
            nvmlDeviceGetHandleByIndex=Mock(return_value="device"),
            nvmlDeviceGetCurrentDriverModel=Mock(return_value=1),
        ),
    }
    modules = {
        "scalene": SimpleNamespace(__file__="/engine/scalene/__init__.py"),
        "scalene.pywhere": pywhere,
        "scalene.scalene_tracer": tracer,
    }
    thread = SimpleNamespace(start=Mock(), join=Mock())
    monkeypatch.setattr(scalene, "sys", runtime)
    monkeypatch.setattr(scalene, "signal", signals)
    monkeypatch.setattr(scalene, "_ACTIVE", None)
    monkeypatch.setattr(scalene, "_NATIVE_MAPS", None)
    monkeypatch.setattr(scalene, "_load_scalene", Mock())
    monkeypatch.setattr(scalene, "_component", lambda _module, name: components[name])
    monkeypatch.setattr(
        scalene, "importlib", SimpleNamespace(import_module=lambda name: modules[name])
    )
    monkeypatch.setattr(
        scalene,
        "threading",
        SimpleNamespace(
            Thread=Mock(return_value=thread),
            Event=threading.Event,
            RLock=threading.RLock,
            Lock=threading.Lock,
            current_thread=lambda: "main",
            main_thread=lambda: "main",
        ),
    )
    return SimpleNamespace(
        stats=stats,
        native=native,
        queues=queues,
        pywhere=pywhere,
        tracer=tracer,
        runtime=runtime,
        signals=signals,
        hooks=hooks,
        components=components,
        processor=processor,
        windows=windows,
        thread=thread,
    )


def collector(**options):
    """Create a collector restricted to two controlled project files.

    Keep external libraries outside the normalized report source.

    """
    return ScaleneBackend(
        accepts=lambda name: name in {"main.py", "other.py"}, on_source=Mock(), **options
    )


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("memory", [False, True])
def test_start_sample_live_snapshot_and_stop(engine, platform, memory):
    """Restore owned hooks and native state after collecting a live snapshot.

    Verify source registration, detached metrics, and repeated-stop behavior
    on both signal and worker-thread sampling paths.

    """
    engine.runtime.platform = platform
    backend = collector(memory=memory)
    backend.start()
    assert scalene._ACTIVE is backend
    assert engine.hooks.profile == backend._source_event
    backend._source_event(
        SimpleNamespace(f_code=SimpleNamespace(co_filename="other.py")), "call", None
    )
    engine.stats.cpu_stats.cpu_samples_python["main.py"][2] = 1
    if memory:
        engine.stats.memory_stats.memory_malloc_samples["main.py"][2] = 3
        engine.stats.memory_stats.memory_free_samples["main.py"][2] = 1
    backend._sample()
    live = backend.result()
    assert live.lines[0].hits is None
    assert live.lines[0].samples == 0
    assert (live.lines[0].memory is not None) is memory
    if memory:
        assert live.lines[0].memory.delta_bytes == 2 * 1024**2
        engine.pywhere.register_files_to_profile.assert_called()
    live.lines.clear()
    assert backend.result().lines
    backend.stop()
    backend.stop()
    assert scalene._ACTIVE is None
    assert engine.hooks.profile is None
    assert backend._sampler is None
    assert backend._native is None
    assert engine.native._Scalene__args == "original arguments"
    assert not hasattr(engine.native, "_Scalene__stats")
    engine.stats.stop_clock.assert_called_once()
    if memory:
        engine.tracer.cleanup_tracer.assert_called_once()
        engine.pywhere.depopulate_struct.assert_called_once()
        assert all(queue.stop.call_count == 1 for queue in engine.queues)


@pytest.mark.parametrize("failure", ["trace", "dll", "initialize", "map", "active", "tracer"])
def test_memory_startup_failure_unwinds_owned_state(engine, failure):
    """Unwind native replacements and active ownership on startup errors.

    Reject conflicting instrumentation and unavailable allocators without
    leaving project hooks or partially installed native state behind.

    """
    if failure == "trace":
        engine.runtime.gettrace = lambda: object()
    elif failure == "dll":
        engine.windows.load_dll.return_value = False
    elif failure == "initialize":
        engine.windows.initialize.return_value = False
    elif failure == "map":
        engine.components["ScaleneMapFile"].side_effect = OSError("allocator unavailable")
    elif failure == "active":
        engine.native._Scalene__initialized = True
    else:
        engine.components["initialize_tracer"].side_effect = RuntimeError("tracer failed")
    backend = collector(memory=True)
    with pytest.raises(RuntimeError):
        backend.start()
    assert scalene._ACTIVE is None
    assert engine.hooks.profile is None
    assert backend._native is None
    assert engine.native._Scalene__args == "original arguments"
    assert not hasattr(engine.native, "_Scalene__stats")
    if failure == "tracer":
        engine.tracer.cleanup_tracer.assert_called_once()


def test_native_shutdown_error_still_restores_replacements(engine):
    """Restore native attributes even when the upstream tracer cannot stop.

    Release native structures and active ownership before propagating cleanup
    failure to the caller.

    """
    backend = collector(memory=True)
    backend.start()
    engine.tracer.disable_tracing.side_effect = RuntimeError("disable failed")
    with pytest.raises(RuntimeError, match="disable failed"):
        backend.stop()
    assert scalene._ACTIVE is None
    assert backend._native is None
    assert engine.native._Scalene__args == "original arguments"
    engine.tracer.cleanup_tracer.assert_called_once()
    engine.pywhere.depopulate_struct.assert_called_once()


def test_signal_timer_and_stop_thread_conflicts(engine, monkeypatch):
    """Reject conflicting timers and shutdown from a different thread.

    Keep the running collector owned by the original thread after a rejected
    stop, then finish normally on that thread.

    """
    engine.runtime.platform = "linux"
    engine.signals.getitimer.return_value = (1, 0)
    backend = collector()
    with pytest.raises(RuntimeError, match="active timer"):
        backend.start()
    engine.signals.getitimer.return_value = (0, 0)
    backend.start()
    monkeypatch.setattr(scalene.threading, "current_thread", lambda: "worker")
    with pytest.raises(RuntimeError, match="main Python thread"):
        backend.stop()
    assert backend._running
    monkeypatch.setattr(scalene.threading, "current_thread", lambda: "main")
    backend.stop()
    engine.signals.signal.assert_any_call(engine.signals.SIGALRM, "original signal")


def test_sampling_failures_reentrancy_and_source_counts(engine):
    """Keep sampling failures diagnostic and omit sleeping-thread counts.

    Count successfully processed project frames without fabricating execution
    counts or interrupting the workload when the engine fails.

    """
    backend = collector()
    backend._sample()
    backend.start()
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename="main.py"), f_lineno=2)
    engine.components["compute_frames_to_record"].return_value = [(frame, 1, None)]
    backend._sample()
    assert backend.result().lines[0].samples == 1
    backend._sleeping[1] = True
    backend._sample()
    assert backend.result().lines[0].samples == 1
    engine.runtime.platform = "linux"
    backend._sampling = True
    backend._sample()
    assert engine.processor.process_cpu_sample.call_count == 2
    backend._sampling = False
    engine.processor.process_cpu_sample.side_effect = ValueError("bad sample")
    backend._sample()
    assert not backend._sampling
    assert any("bad sample" in warning for warning in backend.result().warnings)
    backend.stop()


def test_worker_loop_stops_without_extra_sample(engine):
    """Wake the sampler promptly and avoid sampling after stop is requested.

    Use a controlled wait sequence so the test depends on no scheduler timing.

    """
    engine.thread.start.assert_not_called()
    backend = collector()
    backend._stop_event = SimpleNamespace(wait=Mock(side_effect=[False, True]))
    backend._sample = Mock()
    backend._sample_loop()
    backend._sample.assert_called_once_with()


def test_cleanup_preserves_replaced_hook_and_empty_memory_diagnostic(engine):
    """Preserve a newer profile hook and report missing native allocations.

    Ownership cleanup must not remove a callback installed by another caller.

    """
    backend = collector(memory=True)
    backend.start()
    replacement = object()
    engine.hooks.profile = replacement
    backend.stop()
    assert engine.hooks.profile is replacement
    assert any("No native allocation samples" in item for item in backend.result().warnings)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_gpu_device_absence_and_initialization_failure(engine, platform):
    """Keep unsupported or failing GPU collection unavailable.

    Surface the platform or initialization reason as an honest diagnostic.

    """
    engine.runtime.platform = platform
    engine.components["ScaleneNVIDIAGPU"] = Mock(side_effect=OSError("driver missing"))
    backend = collector(gpu=True)
    backend.start()
    backend.stop()
    assert not backend.capabilities.gpu
    assert backend.result().warnings
    assert (
        "Apple" in backend._gpu_error
        if platform == "darwin"
        else "driver missing" in backend._gpu_error
    )


@pytest.mark.parametrize("supported", [False, True])
def test_gpu_fallback_and_sample_failure(engine, supported):
    """Try the alternate accelerator and detach failing GPU measurements.

    Preserve CPU sampling after an invalid device reading and expose its
    diagnostic without inventing GPU zeros.

    """
    device = SimpleNamespace(
        has_gpu=Mock(return_value=supported), get_stats=Mock(return_value=(float("nan"), 10))
    )
    engine.components["ScaleneNVIDIAGPU"] = Mock(
        return_value=SimpleNamespace(has_gpu=lambda: False)
    )
    engine.components["ScaleneNeuron"] = Mock(return_value=device)
    backend = collector(gpu=True)
    backend.start()
    engine.stats.cpu_stats.cpu_samples_python["main.py"][2] = 1
    backend._sample()
    backend.stop()
    assert backend.capabilities.gpu is supported
    assert backend.result().lines[0].gpu is None
    assert (
        "unavailable" in backend._gpu_error
        if not supported
        else "non-finite" in backend._gpu_error
    )


def test_successful_gpu_export_filters_sources_and_detaches_results(engine):
    """Export available device metrics only at sampled project locations.

    Reject dependency files and invalid line numbers, and keep returned
    metrics independent of subsequent caller mutations.

    """
    device = SimpleNamespace(has_gpu=lambda: True, get_stats=lambda: (2, -1))
    engine.components["ScaleneNVIDIAGPU"] = Mock(return_value=device)
    backend = collector(gpu=True)
    backend.start()
    engine.stats.gpu_stats.n_gpu_samples["main.py"][2] = 1
    engine.stats.gpu_stats.gpu_samples["main.py"][2] = 0.5
    engine.stats.cpu_stats.cpu_samples_c["dependency.py"][2] = 1
    engine.stats.cpu_stats.cpu_samples_c["main.py"][0] = 1
    engine.stats.cpu_stats.cpu_samples_c["main.py"][3] = 1
    backend._sample()
    engine.processor.process_cpu_sample.assert_called_once()
    assert engine.processor.process_cpu_sample.call_args.args[2:4] == (1, 0)
    backend.stop()
    result = backend.result()
    assert [(row.filename, row.line) for row in result.lines] == [("main.py", 2), ("main.py", 3)]
    assert result.lines[0].gpu.time_ns == 500_000_000
    assert result.lines[1].gpu is None
    result.lines[0].gpu.time_ns = 0
    assert backend.result().lines[0].gpu.time_ns == 500_000_000


@pytest.mark.parametrize("driver_models", [[1], [0], [1, 0]])
def test_windows_gpu_memory_availability_preserves_time_samples(engine, driver_models):
    """Suppress unavailable WDDM memory while retaining device time estimates.

    Keep supported process-memory readings, including sampled zero, and avoid
    presenting incomplete multi-device memory as a complete measurement.

    """
    nvml = engine.components["pynvml"]
    nvml.nvmlDeviceGetCount.return_value = len(driver_models)
    nvml.nvmlDeviceGetCurrentDriverModel.side_effect = driver_models
    engine.components["ScaleneNVIDIAGPU"] = Mock(
        return_value=SimpleNamespace(has_gpu=lambda: True)
    )
    backend = collector(gpu=True)
    backend.start()
    engine.stats.gpu_stats.n_gpu_samples["main.py"][2] = 1
    engine.stats.gpu_stats.gpu_samples["main.py"][2] = 0.5
    backend.stop()
    result = backend.result()
    assert backend.capabilities.gpu
    assert result.lines[0].gpu.time_ns == 500_000_000
    if 0 in driver_models:
        assert result.lines[0].gpu.peak_memory_bytes is None
        assert "Per-process GPU memory is unavailable with NVIDIA WDDM." in result.warnings
    else:
        assert result.lines[0].gpu.peak_memory_bytes == 5 * 1024**2
        assert backend._gpu_memory_error is None


def test_windows_gpu_memory_check_failure_keeps_time_available(engine):
    """Limit an unavailable driver-model query to GPU memory diagnostics.

    Continue exporting utilization-based time and leave memory unknown rather
    than trusting the upstream default zero.

    """
    engine.components["pynvml"].nvmlDeviceGetCurrentDriverModel.side_effect = OSError(
        "driver query unavailable"
    )
    engine.components["ScaleneNVIDIAGPU"] = Mock(
        return_value=SimpleNamespace(has_gpu=lambda: True)
    )
    backend = collector(gpu=True)
    backend.start()
    engine.stats.gpu_stats.n_gpu_samples["main.py"][2] = 1
    engine.stats.gpu_stats.gpu_samples["main.py"][2] = 0.5
    backend.stop()
    result = backend.result()
    assert backend.capabilities.gpu
    assert result.lines[0].gpu.time_ns == 500_000_000
    assert result.lines[0].gpu.peak_memory_bytes is None
    assert any("driver query unavailable" in warning for warning in result.warnings)
