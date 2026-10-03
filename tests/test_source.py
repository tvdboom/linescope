"""LineScope.

Author: Mavs
Description: Project filtering and immutable source capture tests.

"""

import linecache
import sys
import sysconfig
from pathlib import Path

import pytest

from linescope.model import SourceUnit
from linescope.source import SourceRegistry, discover_root


class TestDiscoverRoot:
    """Tests for nearest-project discovery."""

    def test_nearest_pyproject(self, tmp_path):
        """Prefer the nearest pyproject over an enclosing project."""
        (tmp_path / "pyproject.toml").touch()
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "pyproject.toml").touch()
        package = nested / "src" / "package"
        package.mkdir(parents=True)
        source = package / "module.py"
        source.touch()
        assert discover_root(source) == nested

    def test_no_pyproject(self, tmp_path):
        """An unconfigured directory remains its own default root."""
        assert discover_root(tmp_path) == tmp_path

    def test_working_directory(self, tmp_path, monkeypatch):
        """The working directory supplies the default starting point."""
        monkeypatch.chdir(tmp_path)
        assert SourceRegistry().root == tmp_path


class TestSourceRegistry:
    """Tests for own-code filtering and snapshots."""

    @pytest.mark.parametrize("suffix", [".py", ".pyw"])
    def test_project_python(self, tmp_path, suffix):
        """Python source below the root is accepted."""
        registry = SourceRegistry(tmp_path)
        assert registry.accepts(tmp_path / f"worker{suffix}")
        assert not registry.accepts(tmp_path / "image.png")
        assert not registry.accepts(tmp_path.parent / "elsewhere.py")

    @pytest.mark.parametrize(
        "directory", ["site-packages", "dist-packages", ".venv", "venv", ".tox"]
    )
    def test_automatic_exclusions(self, tmp_path, directory):
        """Local environments stay hidden even when included explicitly."""
        registry = SourceRegistry(tmp_path, include=["**/*.py"])
        assert not registry.accepts(tmp_path / directory / "dependency.py")

    def test_standard_library_and_profiler_excluded(self):
        """Broad roots never include the stdlib or profiler implementation."""
        path = Path(sysconfig.get_paths()["stdlib"])
        registry = SourceRegistry(path.anchor, include=["**/*.py"])
        assert not registry.accepts(path / "json" / "decoder.py")
        assert not registry.accepts(sys.modules["linescope.source.discovery"].__file__)

    @pytest.mark.parametrize(
        "rule", ["package", "src/package", "src/package/*.py", "**/package/*.py"]
    )
    def test_include_package_path_or_glob(self, tmp_path, rule):
        """Package names, relative paths, and globs restrict captured code."""
        registry = SourceRegistry(tmp_path, include=[rule])
        assert registry.accepts(tmp_path / "src" / "package" / "worker.py")
        assert not registry.accepts(tmp_path / "src" / "other" / "worker.py")

    def test_include_dotted_package(self, tmp_path):
        """Dotted package paths match complete components."""
        registry = SourceRegistry(tmp_path, include=["package.worker"])
        assert registry.accepts(tmp_path / "src" / "package" / "worker.py")
        assert not registry.accepts(tmp_path / "src" / "package" / "worker_extra.py")

    def test_include_external_package(self, tmp_path, monkeypatch):
        """Explicit local packages on sys.path can lie outside the root."""
        package = tmp_path / "external" / "package"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('must not import')")
        monkeypatch.syspath_prepend(str(package.parent))
        registry = SourceRegistry(tmp_path / "project", include=["package"])
        assert registry.accepts(package / "worker.py")

    @pytest.mark.parametrize("rule", ["tests", "tests/*.py", "**/test_*.py"])
    def test_exclusion_precedence(self, tmp_path, rule):
        """Exclusions win over broad inclusion patterns."""
        registry = SourceRegistry(tmp_path, include=["**/*.py"], exclude=[rule])
        assert not registry.accepts(tmp_path / "tests" / "test_worker.py")
        assert registry.accepts(tmp_path / "package" / "worker.py")

    def test_absolute_path_include(self, tmp_path):
        """Absolute directories include external source roots."""
        outside = tmp_path / "external"
        registry = SourceRegistry(tmp_path / "project", include=[str(outside)])
        assert registry.accepts(outside / "module.py")

    def test_snapshot_frozen_after_edit(self, tmp_path):
        """The first observed source survives edits and deletion."""
        path = tmp_path / "worker.py"
        original = "def work():\n    return 42\n"
        path.write_text(original)
        registry = SourceRegistry(tmp_path)
        first = registry.snapshot(path)
        path.write_text("raise RuntimeError('changed')\n")
        assert registry.snapshot(path) is first
        path.unlink()
        assert registry.snapshot(path).source == original
        assert registry.sources[first.id] is first

    def test_file_encoding(self, tmp_path):
        """PEP 263 encoding declarations are honored."""
        path = tmp_path / "worker.py"
        path.write_bytes(b"# coding: latin-1\nname = 'caf\xe9'\n")
        assert "café" in SourceRegistry(tmp_path).snapshot(path).source

    def test_missing_or_undecodable_source(self, tmp_path):
        """Unavailable source does not interrupt profiling."""
        registry = SourceRegistry(tmp_path)
        assert registry.snapshot(tmp_path / "missing.py") is None
        path = tmp_path / "invalid.py"
        path.write_bytes(b"\xff\xfe")
        assert registry.snapshot(path) is None
        assert registry.snapshot("<string>") is None

    def test_notebook_alias_and_freezing(self, tmp_path):
        """Notebook source maps runtime names while retaining the first text."""
        registry = SourceRegistry(tmp_path)
        unit = registry.register_notebook(
            "notebook://main#cell-1", "work()", path="/main", filename="<cell-1>"
        )
        assert registry.accepts("<cell-1>")
        assert registry.snapshot("<cell-1>") is unit
        registry.register_notebook(unit.id, "changed()", filename="<cell-2>")
        assert registry.snapshot("<cell-2>").source == "work()"
        assert unit.kind == "notebook"
        assert unit.path == "/main"

    def test_register_source_unit(self, tmp_path):
        """Externally captured sources can register their runtime alias."""
        registry = SourceRegistry(tmp_path)
        unit = SourceUnit("cell-1", "/Workspace/main", "x = 1", kind="notebook")
        assert registry.register(unit, filename="<runtime>") is unit
        assert registry.snapshot("<runtime>") is unit

    def test_cached_cell(self, tmp_path, monkeypatch):
        """IPython linecache source is frozen at first observation."""
        filename = "<ipython-input-4-abcdef>"
        monkeypatch.setitem(linecache.cache, filename, (12, None, ["print('hi')\n"], filename))
        registry = SourceRegistry(tmp_path)
        unit = registry.snapshot_cell(filename, path="/Workspace/main")
        assert unit.source == "print('hi')\n"
        assert registry.snapshot(filename) is unit
        assert registry.snapshot_cell("<unknown>") is None
