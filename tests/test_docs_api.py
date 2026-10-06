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
        ("model/linestats", "SymbolRef", "sourceunit/#symbolref"),
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
        (api_site / "api/profiling/session/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    for method in ("html", "start", "stop"):
        link = rendered.select_one(f'td a[href="#session-{method}"]')
        assert link is not None
        assert link.get_text().strip() == method
        assert link.code is not None


def test_api_navigation_groups_public_models_and_removes_implementation_pages(api_site):
    """Expose public workflows and returned data through a compact reference.

    Keep enums with data models and omit integration hooks, discovery helpers,
    and built-in backend implementations from the published reference.

    """
    config = mkdocs_config.load_config(config_file=str(ROOT / "mkdocs.yml"))
    api = next(section["API"] for section in config.nav if "API" in section)
    assert [next(iter(section)) for section in api] == [
        "Profiling",
        "Configuration",
        "Data models",
        "Backend registration",
    ]
    models = next(section["Data models"] for section in api if "Data models" in section)
    assert {next(iter(section)) for section in models} == {
        "Results",
        "Measurements",
        "Memory and GPU measurements",
        "Source snapshots",
        "Spark executions",
        "Options and states",
    }
    for removed in (
        "source",
        "spark",
        "notebooks",
        "enums",
        "backends/tracebackend",
        "backends/scalenebackend",
        "backends/tachyonbackend",
    ):
        assert not (api_site / "api" / removed).exists()
    options = bs4.BeautifulSoup(
        (api_site / "api/model/options/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    for name in (
        "backend",
        "displaymode",
        "sessionstate",
        "runstatus",
        "sourcekind",
        "symbolkind",
    ):
        assert options.select_one(f"h2#{name}") is not None


@pytest.mark.parametrize(
    ("page", "names"),
    [("session", ["config", "result", "state"]), ("profilecontroller", ["result"])],
)
def test_profiling_attribute_tables_show_only_the_user_interface(api_site, page, names):
    """Exclude owned hooks and internal state from public attribute tables.

    Check the rendered descriptions as well as labels so private attributes
    cannot leak into the preceding public attribute's text.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api/profiling" / page / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    attribute_row = next(
        row
        for row in rendered.select(".table_params tr")
        if row.select_one(".td_title").get_text() == "Attributes"
    )
    fields = attribute_row.select(".td_params strong")
    assert [field.get_text().split(":")[0].strip() for field in fields] == names
    assert "_session" not in attribute_row.get_text()
    assert "_backend" not in attribute_row.get_text()
    assert "launch_root" not in attribute_row.get_text()


def test_api_examples_render_as_standard_code_with_visible_results(api_site):
    """Render class and method examples with ordinary code-block structure.

    Verify public examples retain continuation prompts and expression outputs
    inside the styled, copyable code element.

    """
    session = bs4.BeautifulSoup(
        (api_site / "api/profiling/session/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    code = session.select_one(".highlight pre code")
    assert code is not None
    assert "...     value = sum(range(10))" in code.get_text()
    assert "45" in code.get_text()
    assert "'stopped'" in code.get_text()
    controller = bs4.BeautifulSoup(
        (api_site / "api/profiling/profilecontroller/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert controller.select_one(".admonition.example .highlight pre code") is not None
