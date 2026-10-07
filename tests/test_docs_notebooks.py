"""LineScope.

Author: Mavs
Description: Regression tests for rendered documentation examples and their
downloads.

"""

from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig

pytest.importorskip("mkdocs_jupyter")
mkdocs_config = pytest.importorskip("mkdocs.config")
mkdocs_build = pytest.importorskip("mkdocs.commands.build")
nbformat = pytest.importorskip("nbformat")
nbconvert_preprocessors = pytest.importorskip("nbconvert.preprocessors")
bs4 = pytest.importorskip("bs4")

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = [
    (
        "notebook_example",
        "Quick Start",
        'display="cell-summary"',
    ),
    (
        "spark_example",
        "Local Spark",
        'values.groupBy().sum("id").collect()',
    ),
    ("gpu_example", "GPU workload", "torch.cuda.synchronize()"),
]


@pytest.fixture(scope="module")
def notebook_site(tmp_path_factory):
    """Build notebook documentation without executing example cells.

    Build the documentation without executing notebook cells and inspect
    preserved source, output, and navigation.

    """
    build_dir = tmp_path_factory.mktemp("notebook-docs")
    docs_dir = build_dir / "sources"
    site_dir = build_dir / "site"
    shutil.copytree(REPO_ROOT / "docs_sources", docs_dir)

    notebook_path = docs_dir / "examples/notebooks/notebook_example.ipynb"
    notebook = nbformat.read(notebook_path, as_version=4)
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "raise RuntimeError('Notebook cells must not run while building docs')",
            outputs=[
                nbformat.v4.new_output(
                    "display_data",
                    data={
                        "text/html": "<strong>Saved notebook output</strong>",
                        "text/plain": "Saved notebook output",
                    },
                )
            ],
        )
    )
    nbformat.write(notebook, notebook_path)

    def reject_execution(*_args, **_kwargs):
        """Reject unexpected notebook execution during documentation builds.

        Build the documentation without executing notebook cells and inspect
        preserved source, output, and navigation.

        """
        pytest.fail("Documentation must not execute notebook cells")

    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(REPO_ROOT))
        config = mkdocs_config.load_config(
            config_file=str(REPO_ROOT / "mkdocs.yml"),
            docs_dir=str(docs_dir),
            site_dir=str(site_dir),
            exclude_docs="**/*.md\n!examples/*.md\n!getting_started.md",
            strict=True,
        )
        # Exercise the example navigation without importing unrelated API documentation.
        config.nav = [
            section
            for section in config.nav
            if "Examples" in section or "Getting started" in section
        ]
        config.plugins["mkdocs-jupyter"].config["cache"] = False
        # Source-only rendering stays available without Java or a running kernel.
        config.plugins["mkdocs-jupyter"].config["execute"] = False
        patch.setattr(nbconvert_preprocessors.ExecutePreprocessor, "preprocess", reject_execution)
        mkdocs_build.build(config)

    return docs_dir, site_dir


@pytest.mark.parametrize(("name", "heading", "source"), NOTEBOOKS)
def test_notebooks_render_in_navigation_with_source_downloads(
    notebook_site,
    name,
    heading,
    source,
):
    """Verify notebooks render in navigation with source downloads.

    Build the documentation without executing notebook cells and inspect
    preserved source, output, and navigation.

    """
    docs_dir, site_dir = notebook_site
    page_path = site_dir / "examples/notebooks" / name / "index.html"
    page = bs4.BeautifulSoup(page_path.read_text(encoding="utf-8"), "html.parser")
    content = page.select_one(".jupyter-wrapper")
    assert content is not None
    assert content.h1.get_text().rstrip("¶") == heading
    assert source in content.get_text()
    assert content.select(".jp-InputPrompt, .jp-OutputPrompt, .jp-OutputArea-prompt") == []
    assert page.select_one(".md-nav a[href$='.ipynb']") is None
    active_link = page.select_one(".md-nav a.md-nav__link--active")
    assert active_link is not None
    assert (page_path.parent / active_link["href"]).resolve() == page_path.parent

    download = page.select_one("a[title='Download notebook']")
    assert download is not None
    download_path = site_dir / urlsplit(download["href"]).path.removeprefix("/linescope/")
    assert (
        download_path.read_bytes()
        == (docs_dir / "examples/notebooks" / f"{name}.ipynb").read_bytes()
    )


def test_notebook_saved_outputs_and_heading_links_survive_build(notebook_site):
    """Verify notebook saved outputs and heading links survive build.

    Build the documentation without executing notebook cells and inspect
    preserved source, output, and navigation.

    """
    _, site_dir = notebook_site
    page = bs4.BeautifulSoup(
        (site_dir / "examples/notebooks/notebook_example/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert page.select_one(".jupyter-wrapper strong").get_text() == "Saved notebook output"
    assert page.select_one(".jupyter-wrapper h2#one-report-for-several-cells") is not None
    assert (
        page.select_one(".md-nav--secondary a[href='#one-report-for-several-cells']") is not None
    )


@pytest.mark.parametrize("name", [item[0] for item in NOTEBOOKS])
def test_documentation_notebooks_match_runnable_examples(name):
    """Verify documentation notebooks match runnable examples.

    Build the documentation without executing notebook cells and inspect
    preserved source, output, and navigation.

    """
    example = REPO_ROOT / "examples/notebooks" / f"{name}.ipynb"
    documentation = REPO_ROOT / "docs_sources/examples/notebooks" / f"{name}.ipynb"
    assert json.loads(example.read_text(encoding="utf-8")) == json.loads(
        documentation.read_text(encoding="utf-8")
    )


def test_examples_navigation_contains_only_three_notebooks(notebook_site):
    """Expose only the three output-bearing notebooks in example navigation.

    Reject guide pages and the old rendered-notebooks submenu so readers reach
    the executable code and its outputs directly.

    """
    config = mkdocs_config.load_config(config_file=str(REPO_ROOT / "mkdocs.yml"))
    examples = next(section["Examples"] for section in config.nav if "Examples" in section)
    assert examples == [
        {"Quick Start": "examples/notebooks/notebook_example.ipynb"},
        {"Local Spark": "examples/notebooks/spark_example.ipynb"},
        {"GPU workload": "examples/notebooks/gpu_example.ipynb"},
    ]
    _, site_dir = notebook_site
    assert not list((site_dir / "examples").glob("*/index.html"))
    assert {
        path.parent.name for path in (site_dir / "examples/notebooks").glob("*/index.html")
    } == {
        "notebook_example",
        "spark_example",
        "gpu_example",
    }


def test_gpu_notebook_preserves_real_outputs_without_build_hardware(notebook_site):
    """Render saved CUDA results and a report without a GPU build dependency.

    Keep the genuine device name, projection result, and interactive report
    together with the downloadable source. Builds execute portable notebooks
    while retaining measurements captured on CUDA hardware for this page.

    """
    config = mkdocs_config.load_config(config_file=str(REPO_ROOT / "mkdocs.yml"))
    assert config.plugins["mkdocs-jupyter"].config["execute_ignore"] == ["gpu_example.ipynb"]
    _, site_dir = notebook_site
    page = bs4.BeautifulSoup(
        (site_dir / "examples/notebooks/gpu_example/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    output = " ".join(area.get_text() for area in page.select(".jp-OutputArea"))
    assert "GPU: NVIDIA" in output
    assert "Projections:" in output
    report = page.select_one(".jp-OutputArea iframe[srcdoc]")
    assert report is not None
    assert "project_signals" in report["srcdoc"]
    profile = bs4.BeautifulSoup(report["srcdoc"], "html.parser")
    assert "Backend scalene" in profile.select_one(".header-details").get_text(" ", strip=True)
    assert "GPU time" in profile.get_text()
    assert "GPU peak memory" in profile.get_text()
    overview = profile.select_one("#overview")
    assert "Attributed GPU time" not in overview.get_text()
    assert "GPU peak memory" not in overview.get_text()
    assert "Target samples / sec" not in overview.get_text()
    assert "Measured samples / sec" in overview.get_text()
    assert overview.select_one('a[href="#gpu"]') is not None
    gpu_page = profile.select_one("#gpu")
    assert gpu_page is not None
    assert "Driver time" in gpu_page.get_text()
    assert "GPU measurements by source line" in gpu_page.get_text()
    assert "Attributed GPU time sums available source-line estimates" not in gpu_page.get_text()
    assert "Sampled GPU memory is unavailable" not in gpu_page.get_text()
    assert "Attributed GPU time" in gpu_page.get_text()
    memory_card = next(
        card
        for card in gpu_page.select(".stat")
        if card.select_one("span").get_text() == "GPU peak memory"
    )
    if memory_card.select_one("strong").get_text() == "—":
        reason = memory_card.select_one(".metric-unavailable").get_text()
        assert reason
        assert "Unavailable because" not in reason
        assert "GPU time sampling continues" not in reason
    assert "Attributed GPU time:" in output
    assert 'backend="scalene"' in page.get_text()
    assert "gpu=True" in page.get_text()


def _execution_config(tmp_path: Path, source: str, *, name: str = "execution") -> MkDocsConfig:
    """Configure a real build for one portable notebook execution probe.

    Keep Spark and workspace calls outside this regression test while using
    the production execution, download, and cleanup hooks.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory for documentation sources and rendered output.

    source : str
        Trusted notebook cell to execute during the documentation build.

    name : str, default="execution"
        Notebook filename stem used to exercise page-specific build behavior.

    Returns
    -------
    MkDocsConfig
        Strict configuration containing only the execution probe in navigation.

    """
    docs_dir = tmp_path / "sources"
    shutil.copytree(REPO_ROOT / "docs_sources", docs_dir)
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell("# Executed notebook"),
            nbformat.v4.new_code_cell(source),
        ],
        metadata={
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}
        },
    )
    notebook_uri = f"examples/notebooks/{name}.ipynb"
    nbformat.write(notebook, docs_dir / notebook_uri)
    config = mkdocs_config.load_config(
        config_file=str(REPO_ROOT / "mkdocs.yml"),
        docs_dir=str(docs_dir),
        site_dir=str(tmp_path / "site"),
        exclude_docs=(
            f"**/*.md\n**/*.ipynb\n!{notebook_uri}\n!examples/notebooks/execution.ipynb"
        ),
        strict=True,
    )
    config.nav = [{"Executed notebook": notebook_uri}]
    return config


def test_notebook_build_executes_cells_and_cleans_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Render real text and HTML outputs without changing source notebooks.

    Check that the executed notebook writes its report in an owned temporary
    directory and that successful builds remove that directory.

    Parameters
    ----------
    tmp_path : Path
        Isolated documentation source and output directory.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to expose the repository's documentation hooks.

    """
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from docs_sources.scripts import notebooks

    source = (
        "from pathlib import Path\n"
        "from IPython.display import HTML, display\n"
        "Path('execution-report.html').write_text('Generated report')\n"
        "print('Executed notebook text')\n"
        "display(HTML('<strong>Executed notebook HTML</strong>'))\n"
    )
    config = _execution_config(tmp_path, source)
    original = Path(config.docs_dir) / "examples/notebooks/execution.ipynb"
    original_bytes = original.read_bytes()
    assert config.plugins["mkdocs-jupyter"].config["execute"] is True
    mkdocs_build.build(config)

    page = bs4.BeautifulSoup(
        (Path(config.site_dir) / "examples/notebooks/execution/index.html").read_text(
            encoding="utf-8"
        ),
        "html.parser",
    )
    assert "Executed notebook text" in page.select_one(".jp-OutputArea").get_text()
    assert page.select_one(".jp-OutputArea strong").get_text() == "Executed notebook HTML"
    assert original.read_bytes() == original_bytes
    assert not (original.parent / "execution-report.html").exists()
    assert notebooks._workspace is None


def test_spark_docs_without_java_render_saved_content_and_execute_other_notebooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Build without Java and resume Spark execution on the next build.

    Keep downloads and saved content unchanged and execute independent
    notebooks. Verify that newly available Java restores Spark execution and
    that real cell errors still fail the build with resource cleanup.

    Parameters
    ----------
    tmp_path : Path
        Isolated documentation source and output directory.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to control Java discovery without a Java installation.

    """
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from docs_sources.scripts import notebooks

    config = _execution_config(
        tmp_path,
        "from pathlib import Path\n"
        "Path('spark-report.html').write_text('Generated report')\n"
        "raise RuntimeError('Spark execution failed')\n",
        name="spark_example",
    )
    source_dir = Path(config.docs_dir) / "examples/notebooks"
    original = source_dir / "spark_example.ipynb"
    notebook = nbformat.read(original, as_version=4)
    notebook.cells[1].outputs = [
        nbformat.v4.new_output("stream", name="stdout", text="Saved Spark output\n")
    ]
    nbformat.write(notebook, original)
    original_bytes = original.read_bytes()
    nbformat.write(
        nbformat.v4.new_notebook(
            cells=[nbformat.v4.new_code_cell("print('Independent notebook executed')")],
            metadata=notebook.metadata,
        ),
        source_dir / "execution.ipynb",
    )
    config.nav.append({"Independent notebook": "examples/notebooks/execution.ipynb"})
    ignored = config.plugins["mkdocs-jupyter"].config["execute_ignore"]
    monkeypatch.setattr(notebooks, "_java_available", lambda: False)
    mkdocs_build.build(config)

    site_dir = Path(config.site_dir)
    page = bs4.BeautifulSoup(
        (site_dir / "examples/notebooks/spark_example/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert page.select_one(".admonition") is None
    assert "Saved Spark output" in page.select_one(".jp-OutputArea").get_text()
    assert (site_dir / "examples/notebooks/spark_example/spark_example.ipynb").read_bytes() == (
        original_bytes
    )
    independent_page = bs4.BeautifulSoup(
        (site_dir / "examples/notebooks/execution/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert (
        "Independent notebook executed" in independent_page.select_one(".jp-OutputArea").get_text()
    )
    assert independent_page.select_one(".admonition") is None
    assert config.plugins["mkdocs-jupyter"].config["execute_ignore"] is ignored
    assert original.read_bytes() == original_bytes
    assert not (source_dir / "spark-report.html").exists()
    assert notebooks._workspace is None
    assert notebooks._spark_execution is None

    monkeypatch.setattr(notebooks, "_java_available", lambda: True)
    from nbclient.exceptions import CellExecutionError

    with pytest.raises(CellExecutionError, match="Spark execution failed"):
        mkdocs_build.build(config)

    assert config.plugins["mkdocs-jupyter"].config["execute_ignore"] is ignored
    assert original.read_bytes() == original_bytes
    assert not (source_dir / "spark-report.html").exists()
    assert notebooks._workspace is None
    assert notebooks._spark_execution is None


def test_spark_docs_staging_failure_restores_execution_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Restore temporary Spark exclusions after notebook staging fails.

    Reject a failed source copy before any kernel starts and release the
    temporary directory and build-owned execution settings.

    Parameters
    ----------
    tmp_path : Path
        Isolated documentation source and output directory.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to simulate missing Java and a source-copy error.

    """
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from docs_sources.scripts import notebooks

    config = _execution_config(tmp_path, "pass", name="spark_example")
    ignored = config.plugins["mkdocs-jupyter"].config["execute_ignore"]
    monkeypatch.setattr(notebooks, "_java_available", lambda: False)

    def reject_copy(*_args: object, **_kwargs: object):
        """Simulate an unreadable documentation notebook during staging.

        Parameters
        ----------
        *_args : object
            Source and destination paths passed to the copy operation.

        **_kwargs : object
            Additional copy operation options.

        """
        raise OSError("Notebook staging failed")

    monkeypatch.setattr(notebooks.shutil, "copyfile", reject_copy)
    with pytest.raises(OSError, match="Notebook staging failed"):
        mkdocs_build.build(config)

    assert config.plugins["mkdocs-jupyter"].config["execute_ignore"] is ignored
    assert notebooks._workspace is None
    assert notebooks._spark_execution is None


@pytest.mark.parametrize(
    ("home", "on_path", "expected"),
    [("valid", False, True), ("invalid", True, False), (None, True, True), (None, False, False)],
)
def test_docs_java_discovery_honors_java_home_before_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    home: str | None,
    *,
    on_path: bool,
    expected: bool,
):
    """Skip Spark for absent Java while honoring explicit environment settings.

    Detect executable paths without launching Java or requiring a system JDK.
    An invalid explicit home must not be rescued by an unrelated PATH entry.

    Parameters
    ----------
    tmp_path : Path
        Directory containing a fake Java installation.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to control platform and environment discovery.

    home : str | None
        Valid or invalid installation name, or no explicit Java home.

    on_path : bool
        Whether Java is discoverable through the process PATH.

    expected : bool
        Expected availability for notebook execution.

    """
    from docs_sources.scripts import notebooks

    executable = tmp_path / "valid/bin/java"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(notebooks.sys, "platform", "linux")
    monkeypatch.setattr(
        notebooks.shutil, "which", lambda _name: str(executable) if on_path else None
    )
    monkeypatch.delenv("JAVA_HOME", raising=False)
    if home is not None:
        monkeypatch.setenv("JAVA_HOME", str(tmp_path / home))
    assert notebooks._java_available() is expected


def test_docs_java_discovery_uses_saved_windows_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Find a saved Windows Java installation without mutating the environment.

    Match the Spark notebook's behavior for terminals that have not inherited
    recently configured Java settings.

    Parameters
    ----------
    tmp_path : Path
        Directory containing a fake Windows Java installation.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to provide Windows registry settings on any platform.

    """
    from docs_sources.scripts import notebooks

    executable = tmp_path / "bin/java.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(notebooks.shutil, "which", lambda _name: None)
    monkeypatch.setattr(notebooks.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "winreg",
        SimpleNamespace(
            HKEY_CURRENT_USER=1,
            HKEY_LOCAL_MACHINE=2,
            OpenKey=lambda *_args: nullcontext(),
            QueryValueEx=lambda *_args: (str(tmp_path), 1),
        ),
    )
    assert notebooks._java_available() is True
    assert "JAVA_HOME" not in notebooks.os.environ


def test_notebook_execution_failure_aborts_build_and_cleans_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Reject failed notebook cells and release their temporary reports.

    Exercise the production build-error hook after a notebook creates an
    artifact, without requiring Spark or a Databricks workspace.

    Parameters
    ----------
    tmp_path : Path
        Isolated documentation source and output directory.

    monkeypatch : pytest.MonkeyPatch
        Fixture used to expose the repository's documentation hooks.

    """
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from docs_sources.scripts import notebooks

    config = _execution_config(
        tmp_path,
        "from pathlib import Path\n"
        "Path('execution-report.html').write_text('Generated report')\n"
        "raise RuntimeError('Notebook execution failed')\n",
    )
    original = Path(config.docs_dir) / "examples/notebooks/execution.ipynb"
    original_bytes = original.read_bytes()
    from nbclient.exceptions import CellExecutionError

    with pytest.raises(CellExecutionError, match="Notebook execution failed"):
        mkdocs_build.build(config)

    assert original.read_bytes() == original_bytes
    assert not (original.parent / "execution-report.html").exists()
    assert notebooks._workspace is None
