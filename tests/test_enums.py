"""LineScope.

Author: Mavs
Description: Verify enum normalization, string inputs, and session cleanup.

"""

from dataclasses import asdict
import json
import sys

import pytest

from linescope import (
    Backend,
    Config,
    DisplayMode,
    RunStatus,
    Session,
    SessionState,
    SourceKind,
    SymbolKind,
)
from linescope.backends.base import create_backend, register_backend
from linescope.backends.scalene import ScaleneBackend
from linescope.backends.tachyon import TachyonBackend
from linescope.backends.trace import TraceBackend
from linescope.config import default_backend, resolve_config
from linescope.model import ProfileRun, SourceUnit, SparkExecution, SymbolDefinition
from linescope.source import build_navigation


@pytest.mark.parametrize(
    ("member", "collector"),
    [
        (Backend.TRACE, TraceBackend),
        (Backend.SCALENE, ScaleneBackend),
        (Backend.TACHYON, TachyonBackend),
    ],
)
@pytest.mark.parametrize("use_string", [False, True])
def test_factory_accepts_enum_members_and_strings(member, collector, use_string):
    """Verify factory accepts enum members and strings.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    name = member.value if use_string else member
    backend = create_backend(name, accepts=lambda _: False, on_source=lambda _: None)
    assert isinstance(backend, collector)
    assert backend.name is member


@pytest.mark.parametrize("member", list(Backend))
def test_builtin_names_cannot_be_registered(member, monkeypatch):
    """Verify builtin names cannot be registered.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    monkeypatch.setattr("linescope.backends.base._factories", {})
    with pytest.raises(ValueError, match="already registered"):
        register_backend(member.value, TraceBackend)


def test_unknown_backend_lists_builtins_and_registered_collectors(monkeypatch):
    """Verify unknown backend lists builtins and registered collectors.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    monkeypatch.setattr("linescope.backends.base._factories", {})
    register_backend("custom", TraceBackend)
    with pytest.raises(ValueError, match="available: trace, scalene, tachyon, custom"):
        create_backend("missing")


def test_registered_custom_backend_remains_a_string(tmp_path, monkeypatch):
    """Verify registered custom backend remains a string.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """

    class CustomTrace(TraceBackend):
        """Expose a custom registry name through the tracing collector.

        Attributes
        ----------
        name : str
            Collector registry name used by this example or test subclass.

        """

        name = "custom"

    monkeypatch.setattr("linescope.backends.base._factories", {})
    register_backend("custom", CustomTrace)
    config = Config(
        backend="custom",
        root=str(tmp_path),
        display=DisplayMode.NONE,
        notebooks=False,
        spark=False,
    )
    assert config.backend == "custom"
    assert type(config.backend) is str
    with Session(config) as session:
        assert isinstance(session._backend, CustomTrace)
    assert session.result.backend == "custom"


@pytest.mark.parametrize("use_strings", [False, True])
def test_config_normalizes_fixed_choices(use_strings):
    """Verify config normalizes fixed choices.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    config = Config(
        backend="trace" if use_strings else Backend.TRACE,
        display="cell" if use_strings else DisplayMode.CELL,
    )
    assert config.backend is Backend.TRACE
    assert config.display is DisplayMode.CELL
    assert config.spark is False


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_spark_booleans_are_preserved(enabled):
    """Verify explicit spark booleans are preserved.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    assert Config(spark=enabled).spark is enabled


@pytest.mark.parametrize("version", [(3, minor) for minor in range(11, 16)])
def test_default_backend_is_trace_on_every_supported_runtime(version, monkeypatch):
    """Verify Trace is the default on every supported runtime.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    monkeypatch.setattr(sys, "version_info", version)
    assert default_backend() is Backend.TRACE
    assert Config().backend is Backend.TRACE


def test_project_and_explicit_config_normalize_strings(tmp_path):
    """Normalize fixed choices from TOML and explicit session arguments.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.linescope]\nbackend="trace"\ndisplay="none"\nspark=false\n', encoding="utf-8"
    )
    config = resolve_config(root=str(tmp_path))
    assert config.backend is Backend.TRACE
    assert config.display is DisplayMode.NONE
    assert config.spark is False
    explicit = resolve_config(root=str(tmp_path), display=DisplayMode.CELL)
    assert explicit.display is DisplayMode.CELL
    assert resolve_config(root=str(tmp_path)).display is DisplayMode.NONE


def test_session_states_and_failure_cleanup_use_enums(tmp_path):
    """Verify session states and failure cleanup use enums.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    config = Config(
        backend=Backend.TRACE,
        root=str(tmp_path),
        display=DisplayMode.NONE,
        notebooks=False,
        spark=False,
    )
    session = Session(config)
    previous_trace = sys.gettrace()
    assert session.state is SessionState.CREATED

    def workload():
        """Execute the controlled workload while collection is active.

        Inspect normalized enum members and their serialized values without
        starting optional runtimes.

        """
        assert session.state is SessionState.RUNNING
        raise ValueError("workload failed")

    with pytest.raises(ValueError, match="workload failed"), session:
        workload()
    assert session.state is SessionState.STOPPED
    assert session.result.root_run.status is RunStatus.FAILED
    assert session.result.backend is Backend.TRACE
    assert sys.gettrace() is previous_trace
    assert session.stop() is None
    with pytest.raises(RuntimeError, match="only start once"):
        session.start()
    with Session(config) as next_session:
        assert next_session.state is SessionState.RUNNING


def test_model_strings_normalize_and_serialize_as_string_values():
    """Verify model strings normalize and serialize as string values.

    Inspect normalized enum members and their serialized values without starting
    optional runtimes.

    """
    source = SourceUnit("cell", "cell.py", "def work(): pass\nwork()\n", "notebook")
    symbol = SymbolDefinition("function", "work", source.id, 1)
    run = ProfileRun(status="reference", source=source)
    execution = SparkExecution("action", status="failed")
    assert source.kind is SourceKind.NOTEBOOK
    assert symbol.kind is SymbolKind.FUNCTION
    assert run.status is RunStatus.REFERENCE
    assert execution.status is RunStatus.FAILED
    serialized = json.loads(json.dumps(asdict(run)))
    assert serialized["status"] == "reference"
    assert serialized["source"]["kind"] == "notebook"
    symbols, references = build_navigation({source.id: source})
    assert symbols[0].kind is SymbolKind.FUNCTION
    assert references[(source.id, 2)][0].target.line == 1
