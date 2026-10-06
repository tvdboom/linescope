"""LineScope.

Author: Mavs
Description: Regression tests for rendered documentation examples and their
downloads.

"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
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
        "Notebook Quick Start",
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


def test_getting_started_displays_complete_script_source(notebook_site):
    """Verify documentation displays complete runnable source.

    Build the documentation without executing notebook cells and inspect
    preserved source, output, and navigation.

    """
    _, site_dir = notebook_site
    rendered = bs4.BeautifulSoup(
        (site_dir / "getting_started/index.html").read_text(encoding="utf-8"), "html.parser"
    )
    for line_number in rendered.select(".highlight .linenos"):
        line_number.decompose()
    source = (REPO_ROOT / "examples/script_example.py").read_text(encoding="utf-8").strip()
    assert source in [code.get_text().strip() for code in rendered.select(".highlight code")]


def test_examples_navigation_contains_only_three_notebooks(notebook_site):
    """Expose only the three output-bearing notebooks in example navigation.

    Reject guide pages and the old rendered-notebooks submenu so readers reach
    the executable code and its outputs directly.

    """
    config = mkdocs_config.load_config(config_file=str(REPO_ROOT / "mkdocs.yml"))
    examples = next(section["Examples"] for section in config.nav if "Examples" in section)
    assert examples == [
        {"Notebook Quick Start": "examples/notebooks/notebook_example.ipynb"},
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


def _execution_config(tmp_path: Path, source: str) -> MkDocsConfig:
    """Configure a real build for one portable notebook execution probe.

    Keep Spark and workspace calls outside this regression test while using
    the production execution, download, and cleanup hooks.

    Parameters
    ----------
    tmp_path : Path
        Isolated directory for documentation sources and rendered output.

    source : str
        Trusted notebook cell to execute during the documentation build.

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
    nbformat.write(notebook, docs_dir / "examples/notebooks/execution.ipynb")
    config = mkdocs_config.load_config(
        config_file=str(REPO_ROOT / "mkdocs.yml"),
        docs_dir=str(docs_dir),
        site_dir=str(tmp_path / "site"),
        exclude_docs="**/*.md\n**/*.ipynb\n!examples/notebooks/execution.ipynb",
        strict=True,
    )
    config.nav = [{"Executed notebook": "examples/notebooks/execution.ipynb"}]
    return config


def test_notebook_build_executes_cells_and_cleans_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_notebook_execution_failure_aborts_build_and_cleans_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
