"""LineScope.

Author: Mavs
Description: Verify API references in the generated documentation HTML.

"""

from pathlib import Path
import shutil

import pytest

import linescope.config as profile_config

mkdocs_config = pytest.importorskip("mkdocs.config")
mkdocs_build = pytest.importorskip("mkdocs.commands.build")
bs4 = pytest.importorskip("bs4")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def api_site(tmp_path_factory):
    """Build the API documentation site in a temporary output directory.

    Build documentation in a temporary directory and inspect the generated API
    links and method labels.

    """
    build_dir = tmp_path_factory.mktemp("api-docs")
    docs_dir = build_dir / "sources"
    site_dir = build_dir / "site"
    shutil.copytree(ROOT / "docs_sources", docs_dir)
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT))
        # The configure example changes process defaults. Give the build its
        # own mapping so later workload tests keep their original settings.
        patch.setattr(profile_config, "_overrides", {})
        config = mkdocs_config.load_config(
            config_file=str(ROOT / "mkdocs.yml"),
            docs_dir=str(docs_dir),
            site_dir=str(site_dir),
            exclude_docs="**/*.md\n!api/**/*.md\n**/*.ipynb",
            strict=True,
        )
        config.nav = [section for section in config.nav if "API" in section]
        mkdocs_build.build(config)
    return site_dir


@pytest.mark.parametrize(
    ("page", "name", "target"),
    [
        ("profiling/session", "ProfileResult", "/model/profileresult/"),
        ("model/linestats", "SymbolRef", "symbolref/#symbolref"),
        (
            "spark/sparkintegration",
            "DataFrame",
            (
                "https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/"
                "api/pyspark.sql.DataFrame.html"
            ),
        ),
        (
            "spark/sparkintegration",
            "SparkSession",
            (
                "https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/"
                "api/pyspark.sql.SparkSession.html"
            ),
        ),
    ],
)
def test_api_type_references_render_as_clickable_links(api_site, page, name, target):
    """Verify api type references render as clickable links.

    Build documentation in a temporary directory and inspect the generated API
    links and method labels.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api" / page / "index.html").read_text(encoding="utf-8"), "html.parser"
    )
    links = [link for link in rendered.select(".table_params a") if link.get_text() == name]
    assert links
    assert any(target in link["href"] for link in links)


def test_see_also_renders_with_links_and_descriptions(api_site):
    """Verify see also renders with links and descriptions.

    Build documentation in a temporary directory and inspect the generated API
    links and method labels.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api/profiling/session/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    related = rendered.select_one(".admonition.info")
    assert related is not None
    assert related.select_one(".admonition-title").get_text() == "See Also"
    assert {link.get_text() for link in related.select("a")} >= {
        "configure",
        "ProfileController",
        "ProfileResult",
    }
    assert "Combine portable source snapshots" in related.get_text()


def test_method_table_preserves_code_labels_and_targets(api_site):
    """Verify method table preserves code labels and targets.

    Build documentation in a temporary directory and inspect the generated API
    links and method labels.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api/backends/tachyonbackend/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    for method in ("result", "start", "stop"):
        link = rendered.select_one(f'td a[href="#tachyonbackend-{method}"]')
        assert link is not None
        assert link.get_text().strip() == method
        assert link.code is not None
