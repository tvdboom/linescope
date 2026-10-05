"""LineScope.

Author: Mavs
Description: Verify trace-event forwarding, frame ownership, and suspension.

"""

import dis
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from linescope.backends import trace
from linescope.backends.trace import TraceBackend


def frame(filename="main.py", *, parent=None, opcode="RETURN_VALUE", line=2):
    """Provide an interpreter frame with explicit parent and yield state.

    Replay events deterministically without replacing coverage's trace hooks.

    """
    return SimpleNamespace(
        f_code=SimpleNamespace(
            co_filename=filename,
            co_qualname="work",
            co_firstlineno=1,
            co_code=bytes([dis.opmap[opcode]]),
        ),
        f_lineno=line,
        f_lasti=0,
        f_back=parent,
        f_trace=None,
    )


@pytest.fixture
def collector(monkeypatch):
    """Record hook installation while replaying controlled frame events.

    Retain the real interpreter's coverage callback throughout each test.

    """
    monkeypatch.setattr(trace, "sys", SimpleNamespace(settrace=Mock()))
    backend = TraceBackend(
        accepts=lambda name: name == "main.py", on_source=Mock(), on_interval=Mock()
    )
    backend._running = True
    return backend


def test_parent_child_accounting_and_external_forwarding(collector):
    """Pause project callers through child execution and forward prior hooks.

    Charge external work to its project caller, preserve exact calls and hits,
    and restore the forwarded local callback when a frame returns.

    """
    local = Mock(return_value=None)
    collector._previous_trace = Mock(return_value=local)
    parent = frame()
    collector._trace(parent, "call", None)
    collector._trace(parent, "line", None)
    external = frame("library.py", parent=parent)
    child = frame(parent=external, line=3)
    collector._trace(child, "call", None)
    assert not collector._frames[id(parent)].active
    collector._trace(child, "line", None)
    collector._trace(child, "exception", (ValueError, ValueError(), None))
    collector._trace(child, "return", 1)
    assert collector._frames[id(parent)].active
    assert id(child) not in collector._traced_frames
    assert collector._trace(external, "call", None) is local
    collector._trace(parent, "return", 2)
    result = collector.result()
    assert result.function_calls == {("main.py", "work", 1): 2}
    assert {row.line: row.hits for row in result.lines} == {2: 1, 3: 1}
    assert all(row.wall_time_ns >= 0 for row in result.lines)
    assert not collector._frames
    trace.sys.settrace.assert_called()


@pytest.mark.parametrize("opcode", ["YIELD_VALUE", "RESUME"])
def test_suspension_resumption_counts_one_call(collector, opcode):
    """Count resumed generator frames once and release their suspended state.

    Support both interpreter yield representations without charging time
    while the generator has no active frame.

    """
    generator = frame(opcode=opcode)
    collector._trace(generator, "call", None)
    collector._trace(generator, "line", None)
    collector._trace(generator, "return", None)
    assert id(generator) in collector._suspended
    assert id(generator) not in collector._frames
    collector._trace(generator, "call", None)
    collector._trace(generator, "line", None)
    generator.f_lasti = -1
    collector._trace(generator, "return", None)
    assert not collector._suspended
    assert collector.result().function_calls == {("main.py", "work", 1): 1}
    assert collector.result().lines[0].hits == 2


def test_enrolled_frame_without_call_and_stopped_forwarding(collector):
    """Enroll existing frames without inventing calls and forward after stop.

    A stopped multiplexer must preserve the caller's local callback rather
    than accepting more measurements or reinstalling itself.

    """
    active = frame()
    assert collector._trace(active, "line", None) == collector._trace
    assert collector.result().function_calls == {}
    local = Mock(return_value="forwarded")
    collector._previous_locals[id(active)] = local
    collector._running = False
    assert collector._trace(active, "line", None) == "forwarded"
    assert collector.result().lines[0].hits == 1


def test_previous_hook_can_stop_collection_during_forwarding(collector):
    """Honor collector shutdown requested inside an existing trace callback.

    Avoid reinstalling the collector after callback ownership was released.

    """

    def previous(_frame, _event, _arg):
        """Stop collection and retain the previous local callback.

        Model a debugger disabling profiling during a forwarded call event.

        """
        collector._running = False
        return previous

    collector._previous_trace = previous
    assert collector._trace(frame(), "call", None) is previous
    trace.sys.settrace.assert_not_called()
    assert not collector.result().lines


def test_wrong_thread_cannot_release_hooks():
    """Reject direct trace release by a thread that does not own the hooks.

    Leave running state intact so the owner can still perform cleanup.

    """
    backend = TraceBackend(accepts=lambda _: False, on_source=lambda _: None)
    backend._running = True
    backend._owner = -1
    with pytest.raises(RuntimeError, match="thread that started"):
        backend._release_trace()
    assert backend._running
    backend._running = False
    backend._release_trace()


def test_existing_allocations_and_shutdown_errors(monkeypatch):
    """Snapshot accepted existing allocations and release hooks on failure.

    Preserve the caller's allocation tracer and surface finalization errors
    after trace cleanup.

    """
    observed = []
    backend = TraceBackend(accepts=lambda _: False, on_source=observed.append, memory=True)
    monkeypatch.setattr(backend, "_memory_totals", lambda: {("main.py", 2): 3})
    monkeypatch.setattr(trace, "tracemalloc", SimpleNamespace(is_tracing=lambda: True))
    backend.start()
    assert observed == ["main.py"]
    monkeypatch.setattr(backend, "_memory_changes", Mock(side_effect=ValueError("snapshot")))
    with pytest.raises(ValueError, match="snapshot"):
        backend.stop()
    assert not backend._running
    assert backend._memory_baseline is None
