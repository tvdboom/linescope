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
    SparkMode,
    SymbolKind,
)
from linescope.backends.base import create_backend, register_backend
from linescope.backends.scalene import ScaleneBackend
from linescope.backends.tachyon import TachyonBackend
from linescope.backends.trace import TraceBackend
from linescope.config import configure, default_backend, resolve_config
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
    name = member.value if use_string else member
    backend = create_backend(name, accepts=lambda _: False, on_source=lambda _: None)
    assert isinstance(backend, collector)
    assert backend.name is member


@pytest.mark.parametrize("member", list(Backend))
def test_builtin_names_cannot_be_registered(member, monkeypatch):
    monkeypatch.setattr("linescope.backends.base._factories", {})
    with pytest.raises(ValueError, match="already registered"):
        register_backend(member.value, TraceBackend)


def test_unknown_backend_lists_builtins_and_registered_collectors(monkeypatch):
    monkeypatch.setattr("linescope.backends.base._factories", {})
    register_backend("custom", TraceBackend)
    with pytest.raises(ValueError, match="available: trace, scalene, tachyon, custom"):
        create_backend("missing")


def test_registered_custom_backend_remains_a_string(tmp_path, monkeypatch):
    class CustomTrace(TraceBackend):
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
    config = Config(
        backend="trace" if use_strings else Backend.TRACE,
        display="cell" if use_strings else DisplayMode.CELL,
        spark="auto" if use_strings else SparkMode.AUTO,
    )
    assert config.backend is Backend.TRACE
    assert config.display is DisplayMode.CELL
    assert config.spark is SparkMode.AUTO


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_spark_booleans_are_preserved(enabled):
    assert Config(spark=enabled).spark is enabled


@pytest.mark.parametrize(
    ("version", "expected"), [((3, 14), Backend.SCALENE), ((3, 15), Backend.TRACE)]
)
def test_default_backend_is_an_enum(version, expected, monkeypatch):
    monkeypatch.setattr("linescope.config.sys.version_info", version)
    assert default_backend() is expected


def test_project_and_global_config_normalize_strings(tmp_path, monkeypatch):
    monkeypatch.setattr("linescope.config._overrides", {})
    (tmp_path / "pyproject.toml").write_text(
        '[tool.linescope]\nbackend="trace"\ndisplay="none"\nspark="auto"\n', encoding="utf-8"
    )
    config = resolve_config(root=str(tmp_path))
    assert config.backend is Backend.TRACE
    assert config.display is DisplayMode.NONE
    assert config.spark is SparkMode.AUTO
    configure(root=str(tmp_path), display=DisplayMode.CELL)
    assert resolve_config().display is DisplayMode.CELL


def test_session_states_and_failure_cleanup_use_enums(tmp_path):
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
        assert session.state is SessionState.RUNNING
        raise ValueError("workload failed")

    with pytest.raises(ValueError, match="workload failed"), session:
        workload()
    assert session.state is SessionState.STOPPED
    assert session.result.root_run.status is RunStatus.FAILED
    assert session.result.backend is Backend.TRACE
    assert sys.gettrace() is previous_trace
    assert session.stop() is session.result
    with pytest.raises(RuntimeError, match="only start once"):
        session.start()
    with Session(config) as next_session:
        assert next_session.state is SessionState.RUNNING


def test_model_strings_normalize_and_serialize_as_string_values():
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
