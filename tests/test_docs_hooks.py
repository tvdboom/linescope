"""LineScope.

Author: Mavs
Description: Regression tests for executable documentation and API source links.

"""

from pathlib import Path
import sys

import pytest

pytest.importorskip("mkdocs")
pytest.importorskip("regex")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docs_sources.scripts import render
from docs_sources.scripts.autodocs import (
    CUSTOM_URLS,
    AutoDocs,
    custom_autorefs,
    types_conversion,
)
from docs_sources.scripts.autorun import execute


def test_autorun_collects_expressions_and_stdout():
    """Verify autorun collects expressions and stdout.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    blocks, figures = execute('value = 3\nprint("ready")\nvalue * 2')
    assert blocks == [[">>> value = 3", '>>> print("ready")', "ready", ">>> value * 2", "6"]]
    assert figures == []


def test_autorun_restores_output_hooks_after_failure():
    """Verify autorun restores output hooks after failure.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    stdout, displayhook = sys.stdout, sys.displayhook
    with pytest.raises(ValueError, match="example failure"):
        execute('raise ValueError("example failure")')
    assert sys.stdout is stdout
    assert sys.displayhook is displayhook


def test_autorun_hidden_skipped_and_fresh_namespace():
    """Verify autorun hidden skipped and fresh namespace.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    blocks, _ = execute('value = 10  # hide\nvalue\nraise RuntimeError("skip")  # norun')
    assert blocks[0][:2] == [">>> value", "10"]
    assert "hide" not in "\n".join(blocks[0])
    assert "norun" not in "\n".join(blocks[0])
    with pytest.raises(NameError):
        execute("value")


def test_autorun_accepts_numpydoc_console_examples():
    """Verify autorun accepts numpydoc console examples.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    blocks, _ = execute(">>> value = 5\n>>> value * 2\n10\n")
    assert blocks[0][-1] == "10"


@pytest.mark.parametrize("prompted", [False, True])
def test_autorun_preserves_blank_lines_and_separates_imports(*, prompted: bool):
    """Keep import groups and example steps separated in rendered transcripts.

    Parameters
    ----------
    prompted : bool
        Whether to supply ordinary Python or a console transcript.

    """
    lines = [
        "from math import factorial",
        "from pathlib import Path",
        "",
        "value = factorial(3)",
        "",
        "value",
    ]
    if prompted:
        lines = [f">>> {line}" if line else "" for line in lines]
        lines.append("6")
    blocks, _ = execute("\n".join(lines))
    assert blocks == [
        [
            ">>> from math import factorial",
            ">>> from pathlib import Path",
            "",
            ">>> value = factorial(3)",
            "",
            ">>> value",
            "6",
        ]
    ]


def test_autorun_separates_imports_when_source_omits_the_blank_line():
    """Insert an import separator without exposing hidden example statements.

    Keep adjacent visible imports together while hiding implementation-only
    setup and retaining the blank line before the actual example.

    """
    blocks, _ = execute("import math\nimport sys\nhidden = 3  # hide\nmath.factorial(hidden)\n")
    assert blocks == [[">>> import math", ">>> import sys", "", ">>> math.factorial(hidden)", "6"]]


def test_api_signature_links_to_src_layout():
    """Verify api signature links to src layout.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    rendered = AutoDocs.get_obj("linescope.config:Config").get_signature()
    assert "blob/main/src/linescope/config.py#L" in rendered
    assert "Config" in rendered


def test_cli_reference_documents_command_and_options():
    """Verify cli reference documents command and options.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    command = AutoDocs.get_obj("linescope.cli:main")
    signature = command.get_signature()
    assert "<em>command</em>" in signature
    assert "blob/main/src/linescope/cli.py#L" in signature
    assert "linescope" in signature
    assert "--backend" in command.get_table(["parameters"])
    assert "--spark/--no-spark" in command.get_table(["parameters"])
    assert "argv" not in command.get_table(["parameters"])


def test_directive_inside_a_fence_stays_literal():
    """Verify directive inside a fence stays literal.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    source = "```text\n:: missing.module:example\n    :: signature\n```"
    assert render(source) == source


def test_dataclass_fields_render_descriptions_and_fallback_metadata(monkeypatch):
    """Verify explicit field descriptions and generated fallback metadata.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    from linescope.model import LineStats

    rendered = AutoDocs(LineStats).get_table(["attributes"])
    assert "wall_time_ns" in rendered
    assert "Measured driver wall time in nanoseconds" in rendered
    assert "Exact execution count" in rendered

    # Simulate sparse third-party docstrings without removing model documentation.
    monkeypatch.setattr(LineStats, "__doc__", "Carry visible source line measurements.\n\n")
    rendered = AutoDocs(LineStats).get_table(["attributes"])
    assert "Default: None" in rendered
    assert "Required constructor argument" in rendered


def test_example_fence_is_separated_from_following_method_table():
    """Verify example fence is separated from following method table.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    rendered = render(":: linescope.source:SourceRegistry\n    :: examples\n    :: methods")
    assert "```<table" not in rendered
    assert "```\n\n<table" in rendered


@pytest.mark.parametrize("name", ["profile", "profiler"])
def test_callable_controller_keeps_its_exported_name(name):
    """Generate named callable references from their call and class docstrings.

    Retain instance parameters and returns along with class attributes,
    source links, and method anchors.

    """
    controller = AutoDocs.get_obj(f"linescope:{name}")
    assert controller.name == name
    signature = controller.get_signature()
    assert "<em>callable instance</em>" in signature
    assert f"{name}</strong>(**options)" in signature
    assert "blob/main/src/linescope/api.py#L" in signature
    assert "**options" in controller.get_block("Parameters")
    assert "[Session]" in controller.get_block("Returns")
    assert "result : [ProfileResult]" in controller.get_block("Attributes")
    assert f"#{name}-start" in controller.get_methods({"include": ["start"]})
    assert "Config" in AutoDocs.get_obj("linescope:Session").get_see_also()


@pytest.mark.parametrize(
    ("reference", "parent", "method_name"),
    [
        ("linescope.api:Session.stop", "session", "stop"),
        ("linescope:profile.start", "profile", "start"),
        ("linescope:profiler.stop", "profiler", "stop"),
    ],
)
def test_method_reference_keeps_its_parent_anchor(reference, parent, method_name):
    """Verify method reference keeps its parent anchor.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    method = AutoDocs.get_obj(reference)
    assert method._parent_anchor == f"{parent}-"
    assert f"#{parent}-{method_name}" in method.get_signature()


def test_nested_type_references_leave_container_syntax_intact():
    """Verify nested type references leave container syntax intact.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    annotation = "dict[str, list[[SourceLocation]]] | [ProfileResult]"
    rendered = custom_autorefs(annotation)
    assert rendered == (
        "dict[str, list[[SourceLocation][sourcelocation]]] | [ProfileResult][profileresult]"
    )
    assert types_conversion("list[linescope.model.SourceLocation]") == ("list[[SourceLocation]]")
    assert types_conversion("pathlib.Path | None") == "[Path] | None"
    assert custom_autorefs("Callable[[str], bool]") == "Callable[[str], bool]"


@pytest.mark.parametrize(
    "name",
    [
        "DataFrame",
        "SparkSession",
        "InteractiveShell",
        "DBUtils",
        "DBUtils.notebook",
        "ApiClient",
        "ExecutionInfo",
        "Path",
    ],
)
def test_external_classes_link_to_their_official_reference(name):
    """Verify external classes link to their official reference.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    url = CUSTOM_URLS[name.lower().replace(".", "")]
    assert custom_autorefs(f"[{name}]") == f"[{name}]({url})"
    assert custom_autorefs(f"list[[{name}]]") == f"list[[{name}]({url})]"


def test_autorefs_preserves_literal_code_and_explicit_markdown_links():
    """Verify autorefs preserves literal code and explicit markdown links.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    source = (
        "`[ProfileResult]`\n\n"
        "```python\nvalue = list[ProfileResult]\n```\n\n"
        "[API](https://example.com/with/[brackets])\n\n"
        "![image](picture.png)\n\n"
        "[ref]: https://example.com/\n"
    )
    assert custom_autorefs(source) == source


def test_generated_dataclass_attribute_types_are_clickable():
    """Verify generated dataclass attribute types are clickable.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    rendered = render(":: linescope.model:LineStats\n    :: table:\n        - attributes")
    assert "[SourceLocation][sourcelocation]" in rendered
    assert "[MemoryStats][memorystats]" in rendered
    assert "list[[SymbolRef][symbolref]]" in rendered


def test_methods_render_related_objects_without_error_sections():
    """Verify methods render related objects without error sections.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """

    class RelatedAPI:
        """Describe a sample API.

        Exercise related-object rendering without importing optional
        backends.

        """

        def result(self):
            """Return a collected result.

            Returns
            -------
            [ProfileResult]
                Completed measurements.

            See Also
            --------
            - linescope.api:Session.stop

            """

    rendered = AutoDocs(RelatedAPI).get_methods({"include": ["result"]})
    assert 'info "See Also"' in rendered
    assert "[stop][session-stop]" in rendered
    assert "Raises" not in rendered


def test_method_overview_renders_summary_references_as_links():
    """Resolve API references inside method summary table cells.

    Exercise Markdown's HTML-table handling so transformed reference text
    becomes a link rather than appearing literally in the built page.

    """
    from bs4 import BeautifulSoup
    from markdown import Markdown
    from mkdocs_autorefs import AutorefsExtension

    source = render(
        ":: linescope.api:ProfileController\n"
        "    :: methods:\n"
        "        toc_only: true\n"
        "        include: [start]\n"
    )
    html = Markdown(extensions=["md_in_html", AutorefsExtension()]).convert(source)
    page = BeautifulSoup(html, "html.parser")
    reference = page.select_one('td autoref[identifier="config"]')
    assert reference is not None
    assert reference.get_text() == "Config"
    assert "[Config][config]" not in page.get_text()


@pytest.mark.parametrize("config", [{}, {"include": ["__call__", "start", "stop"]}])
def test_method_overview_omits_magic_methods(config: dict):
    """Hide magic methods in automatic and explicitly selected method lists.

    Keep ordinary lifecycle methods available in the overview and detail
    blocks even when an include list names the callable protocol method.

    Parameters
    ----------
    config : dict
        Method selection options passed to the documentation renderer.

    """
    source = AutoDocs.get_obj("linescope.api:ProfileController").get_methods(config)
    assert "__call__" not in source
    assert "profilecontroller-call" not in source
    assert "#profilecontroller-start" in source
    assert "#profilecontroller-stop" in source


@pytest.mark.parametrize(
    ("relative_path", "expected_output"),
    [
        ("configuration/config.md", "False"),
        ("profiling/profile.md", "(45, 'stopped', 'trace')"),
        ("profiling/profiler.md", "(45, 'stopped', 'trace')"),
        ("profiling/profilecontroller.md", "(45, 'stopped', 'trace')"),
        ("profiling/session.md", "'stopped'"),
        ("backends/profilerbackend.md", "('trace', True, [])"),
    ],
)
def test_api_classes_display_executable_examples(relative_path: str, expected_output: str):
    """Display working examples for API entry points and non-model classes.

    Execute the actual page through its Markdown renderer and verify a
    copyable transcript appears beneath its Example heading. Profiling
    examples must restore tracing and Python's output hooks.

    Parameters
    ----------
    relative_path : str
        API page path relative to the documentation API directory.

    expected_output : str
        Deterministic expression output from that page's example.

    """
    from bs4 import BeautifulSoup
    from markdown import Markdown

    from docs_sources.scripts.autorun import formatter

    trace, stdout, displayhook = sys.gettrace(), sys.stdout, sys.displayhook
    path = Path(__file__).resolve().parents[1] / "docs_sources" / "api" / relative_path
    source = render(path.read_text(encoding="utf-8"))
    markdown = Markdown(
        extensions=["admonition", "md_in_html", "pymdownx.superfences", "pymdownx.highlight"],
        extension_configs={
            "pymdownx.superfences": {
                "custom_fences": [{"name": "pycon", "class": "pycon", "format": formatter}]
            }
        },
    )
    page = BeautifulSoup(markdown.convert(source), "html.parser")
    heading = page.find("h2", string="Example")
    assert heading is not None
    code = heading.find_next("code")
    assert code is not None
    assert ">>> " in code.get_text()
    assert expected_output in code.get_text()
    assert sys.gettrace() is trace
    assert sys.stdout is stdout
    assert sys.displayhook is displayhook


def test_default_factory_is_validated_using_its_documented_expression():
    """Verify default factory is validated using its documented expression.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """
    rendered = AutoDocs.get_obj("linescope.config:Config").get_table(["parameters"])
    assert "default=default_backend()" in rendered


def test_incorrect_documented_defaults_fail_the_build():
    """Verify incorrect documented defaults fail the build.

    Render trusted documentation with the repository hooks and inspect the
    resulting links, tables, or captured example output.

    """

    def example(value=1):
        """Describe a default that disagrees with the signature.

        Parameters
        ----------
        value : int, default=2
            Invalid documented default.

        """

    with pytest.raises(ValueError, match="doesn't match"):
        AutoDocs(example).get_table(["parameters"])


def test_attribute_selection_excludes_internal_fields_and_descriptions():
    """Select user-facing fields without folding private state into their text.

    Exercise includes, exclusions, and remaining directive configuration so
    documentation can retain complete library docstrings while presenting the
    public interface alone.

    """
    rendered = render(
        ":: linescope:Session\n"
        "    :: signature\n"
        "    :: table:\n"
        "        - attributes:\n"
        "            include: [config, result, state]\n"
    )
    assert "session-config" in rendered
    assert "session-result" in rendered
    assert "session-state" in rendered
    assert "_backend" not in rendered
    assert "_resources" not in rendered
    assert "launch_root" not in rendered
    assert "SourceRegistry" not in rendered
    selected = AutoDocs.get_obj("linescope:ProfileController").get_table(
        [{"attributes": {"exclude": ["_.*"]}}]
    )
    assert "_session" not in selected
    assert "profilecontroller-result" in selected


def test_api_console_examples_use_standard_copyable_code_blocks():
    """Render the reported multiline example with standard fenced-code markup.

    Preserve continuation indentation and actual expression outputs while
    supplying the code container required by Material styling and copying.

    """
    from bs4 import BeautifulSoup
    from markdown import Markdown

    from docs_sources.scripts.autorun import formatter

    source = render(":: linescope.backends.tachyon:TachyonBackend\n    :: examples")
    markdown = Markdown(
        extensions=["pymdownx.superfences", "pymdownx.highlight"],
        extension_configs={
            "pymdownx.superfences": {
                "custom_fences": [{"name": "pycon", "class": "pycon", "format": formatter}]
            }
        },
    )
    page = BeautifulSoup(markdown.convert(source), "html.parser")
    code = page.select_one(".highlight pre code")
    assert code is not None
    assert (
        "...     accepts=lambda filename: True, on_source=lambda filename: None" in code.get_text()
    )
    assert "(True, False)" in code.get_text()
    assert code.select_one(".k").get_text() == "lambda"
