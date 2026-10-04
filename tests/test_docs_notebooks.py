"""LineScope.

Author: Mavs
Description: Regression tests for rendered documentation examples and their
downloads.

"""

import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

import pytest

pytest.importorskip("mkdocs_jupyter")
mkdocs_config = pytest.importorskip("mkdocs.config")
mkdocs_build = pytest.importorskip("mkdocs.commands.build")
nbformat = pytest.importorskip("nbformat")
nbconvert_preprocessors = pytest.importorskip("nbconvert.preprocessors")
bs4 = pytest.importorskip("bs4")

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = [
    ("quickstart", "LineScope notebook quickstart", "notebook", "%%profile --backend trace"),
    ("databricks", "LineScope in Databricks", "databricks", "%load_ext linescope"),
    ("local_spark", "LineScope with local Spark", "spark", 'values.groupBy().sum("id").collect()'),
]


@pytest.fixture(scope="module")
def notebook_site(tmp_path_factory):
    build_dir = tmp_path_factory.mktemp("notebook-docs")
    docs_dir = build_dir / "sources"
    site_dir = build_dir / "site"
    shutil.copytree(REPO_ROOT / "docs_sources", docs_dir)

    notebook_path = docs_dir / "examples/notebooks/quickstart.ipynb"
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
        patch.setattr(nbconvert_preprocessors.ExecutePreprocessor, "preprocess", reject_execution)
        mkdocs_build.build(config)

    return docs_dir, site_dir


@pytest.mark.parametrize(("name", "heading", "guide", "source"), NOTEBOOKS)
def test_notebooks_render_in_navigation_with_source_downloads(
    notebook_site,
    name,
    heading,
    guide,
    source,
):
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

    guide_page = bs4.BeautifulSoup(
        (site_dir / "examples" / guide / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    rendered_link = guide_page.select_one(f"article a[href='../notebooks/{name}/']")
    assert rendered_link is not None
    assert (site_dir / "examples" / guide / rendered_link["href"]).resolve() == page_path.parent
    guide_download = guide_page.select_one("a[title='Download notebook']")
    assert guide_download is not None
    assert (site_dir / "examples" / guide / guide_download["href"]).resolve() == download_path


def test_notebook_saved_outputs_and_heading_links_survive_build(notebook_site):
    _, site_dir = notebook_site
    page = bs4.BeautifulSoup(
        (site_dir / "examples/notebooks/quickstart/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert page.select_one(".jupyter-wrapper strong").get_text() == "Saved notebook output"
    assert page.select_one(".jupyter-wrapper h2#one-report-for-several-cells") is not None
    assert (
        page.select_one(".md-nav--secondary a[href='#one-report-for-several-cells']") is not None
    )


@pytest.mark.parametrize("name", [item[0] for item in NOTEBOOKS])
def test_documentation_notebooks_match_runnable_examples(name):
    example = REPO_ROOT / "examples/notebooks" / f"{name}.ipynb"
    documentation = REPO_ROOT / "docs_sources/examples/notebooks" / f"{name}.ipynb"
    assert json.loads(example.read_text(encoding="utf-8")) == json.loads(
        documentation.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("page", "example"),
    [
        ("getting_started", "script.py"),
        ("examples/script", "script.py"),
        ("examples/package", "sample_package/__main__.py"),
        ("examples/package", "sample_package/helpers.py"),
        ("examples/backend", "custom_backend.py"),
        ("examples/spark", "local_spark.py"),
        ("examples/databricks", "databricks_notebook.py"),
    ],
)
def test_documentation_displays_complete_runnable_source(notebook_site, page, example):
    _, site_dir = notebook_site
    rendered = bs4.BeautifulSoup(
        (site_dir / page / "index.html").read_text(encoding="utf-8"), "html.parser"
    )
    for line_number in rendered.select(".highlight .linenos"):
        line_number.decompose()
    source = (REPO_ROOT / "examples" / example).read_text(encoding="utf-8").strip()
    assert source in [code.get_text().strip() for code in rendered.select(".highlight code")]
