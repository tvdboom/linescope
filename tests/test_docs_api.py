"""LineScope.

Author: Mavs
Description: Verify API references in the generated documentation HTML.

"""

from pathlib import Path
import re
import shutil

import pytest

import linescope.enums as profile_enums
import linescope.model as profile_models

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
        ("profiling/session", "ProfileResult", "/model/results/profileresult/"),
        ("model/measurements/linestats", "SymbolRef", "/source/symbolref/"),
        ("model/measurements/linestats", "ProcessMemoryStats", "/memory/processmemorystats/"),
        ("model/results/profilerun", "MemorySample", "/memory/memorysample/"),
        ("configuration/config", "DisplayMode", "/model/options/displaymode/"),
        ("backends/profilerbackend", "BackendCapabilities", "/measurements/backendcapabilities/"),
        ("backends/rawbackendresult", "RawLine", "../rawline/"),
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
        "Config",
        "profile",
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


@pytest.mark.parametrize("page", ["session", "profilecontroller", "profile", "profiler"])
def test_save_method_links_path_types(api_site: Path, page: str):
    """Link the saved report's parameter and return types to pathlib.

    Inspect the generated method table so both standalone classes and
    callable instances expose clickable Path types in the final HTML.

    Parameters
    ----------
    api_site : [Path]
        Temporary output directory containing the built API reference.

    page : str
        Profiling page name used by its local method anchors.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api/profiling" / page / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    method = rendered.select_one(f'a[id="{page}-save"]')
    assert method is not None
    table = method.find_next("table", class_="table_params")
    assert table is not None
    links = table.select(
        'strong a[href="https://docs.python.org/3/library/pathlib.html#pathlib.Path"]'
    )
    assert len(links) == 2
    assert all(link.get_text() == "Path" for link in links)


def test_api_navigation_groups_public_models_and_removes_implementation_pages(api_site):
    """Group model pages beneath subsections and omit implementation pages.

    Give every normalized record and enum a named page within its category.
    Keep backend implementation and optional integration details out of the
    published reference.

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
    expected = {
        "Results": ["ProfileResult", "ProfileRun"],
        "Measurements": ["BackendCapabilities", "LineStats", "FunctionStats"],
        "Memory and GPU measurements": [
            "MemoryStats",
            "ProcessMemoryStats",
            "MemorySample",
            "GPUStats",
        ],
        "Source snapshots": ["SourceUnit", "SourceLocation", "SymbolDefinition", "SymbolRef"],
        "Spark executions": ["SparkExecution", "SparkExecutionStats", "SparkOperator"],
        "Options and states": [
            "Backend",
            "DisplayMode",
            "SessionState",
            "RunStatus",
            "SourceKind",
            "SymbolKind",
            "NotebookCollection",
        ],
    }
    assert [next(iter(section)) for section in models] == list(expected)
    for section in models:
        name, pages = next(iter(section.items()))
        assert isinstance(pages, list)
        assert [next(iter(page)) for page in pages] == expected[name]

    documented_models = {name for names in expected.values() for name in names}
    defined_models = {
        name
        for module in (profile_models, profile_enums)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and obj.__module__ == module.__name__
    }
    assert documented_models == defined_models
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


def _reference_pages(sections: list[dict]) -> list[tuple[str, str]]:
    """Collect named reference pages from nested navigation sections.

    Parameters
    ----------
    sections : list[dict]
        Navigation mappings containing pages or nested section lists.

    Returns
    -------
    list[tuple[str, str]]
        API object labels and their documentation source paths.

    """
    pages = []
    for section in sections:
        for name, target in section.items():
            if isinstance(target, str):
                pages.append((name, target))
            else:
                pages.extend(_reference_pages(target))
    return pages


def test_each_api_object_has_a_dedicated_reference_page(api_site):
    """Keep each API object in one source file and one rendered page.

    Check all reference sections so classes, functions, callable aliases, and
    enums cannot be regrouped onto shared pages.

    """
    config = mkdocs_config.load_config(config_file=str(ROOT / "mkdocs.yml"))
    api = next(section["API"] for section in config.nav if "API" in section)
    pages = _reference_pages(api)
    assert len({path for _, path in pages}) == len(pages)
    assert len({name for name, _ in pages}) == len(pages)
    assert "configure" not in {name for name, _ in pages}
    assert {name for name, _ in pages} >= {
        "profile",
        "profiler",
        "ProfileController",
        "Session",
        "Config",
        "register_backend",
        "ProfilerBackend",
        "RawBackendResult",
        "RawLine",
    }
    for name, path in pages:
        source = (ROOT / "docs_sources" / path).read_text(encoding="utf-8")
        assert re.findall(r"^# (.+)$", source, re.MULTILINE) == [name]
        objects = re.findall(r"^:: linescope[^:]*:([^\s]+)$", source, re.MULTILINE)
        assert objects == [name]
        rendered = bs4.BeautifulSoup(
            (api_site / Path(path).with_suffix("") / "index.html").read_text(encoding="utf-8"),
            "html.parser",
        )
        assert rendered.select_one("h1").get_text().strip() == name


@pytest.mark.parametrize("name", ["profile", "profiler"])
def test_callable_pages_use_the_standard_reference_format(api_site, name):
    """Document callable instances with signatures, tables, and methods.

    Match the Session layout while identifying both exported names as the
    same object and keeping each page's method anchors local.

    """
    rendered = bs4.BeautifulSoup(
        (api_site / "api/profiling" / name / "index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    signature = rendered.select_one(".sign")
    assert signature is not None
    assert signature.em.get_text() == "callable instance"
    assert signature.strong.get_text() == name
    assert f"{name}(**options)" in signature.get_text()
    assert "profiler is profile" in rendered.get_text()
    titles = [title.get_text() for title in rendered.select(".table_params .td_title")]
    assert titles[:3] == ["Parameters", "Attributes", "Returns"]
    assert rendered.select_one(".admonition.info") is not None
    code = rendered.select_one(".highlight pre code")
    assert "True" in code.get_text()
    assert "(45, 'stopped', 'trace')" in code.get_text()
    assert "from linescope import profile, profiler\n\n>>> " in code.get_text()
    for method in ("start", "stop", "save", "show"):
        assert rendered.select_one(f'td a[href="#{name}-{method}"]')
        assert rendered.select_one(f'a[id="{name}-{method}"]')


@pytest.mark.parametrize(
    ("page", "names"),
    [
        ("session", ["config", "result", "state"]),
        ("profilecontroller", ["result"]),
        ("profile", ["result"]),
        ("profiler", ["result"]),
    ],
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
    assert "from linescope import Session\n\n>>> with Session(" in code.get_text()
    controller = bs4.BeautifulSoup(
        (api_site / "api/profiling/profilecontroller/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert controller.select_one(".admonition.example .highlight pre code") is not None
