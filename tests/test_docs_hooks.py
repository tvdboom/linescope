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
from docs_sources.scripts.autodocs import (
    CUSTOM_URLS,
    AutoDocs,
    custom_autorefs,
    render,
    types_conversion,
)
from docs_sources.scripts.autorun import execute


def test_autorun_collects_expressions_and_stdout():
    blocks, figures = execute('value = 3\nprint("ready")\nvalue * 2')
    assert blocks == [[">>> value = 3", '>>> print("ready")', "ready", ">>> value * 2", "6"]]
    assert figures == []


def test_autorun_restores_output_hooks_after_failure():
    stdout, displayhook = sys.stdout, sys.displayhook
    with pytest.raises(ValueError, match="example failure"):
        execute('raise ValueError("example failure")')
    assert sys.stdout is stdout
    assert sys.displayhook is displayhook


def test_autorun_hidden_skipped_and_fresh_namespace():
    blocks, _ = execute('value = 10  # hide\nvalue\nraise RuntimeError("skip")  # norun')
    assert blocks[0][:2] == [">>> value", "10"]
    assert "hide" not in "\n".join(blocks[0])
    assert "norun" not in "\n".join(blocks[0])
    with pytest.raises(NameError):
        execute("value")


def test_autorun_accepts_numpydoc_console_examples():
    blocks, _ = execute(">>> value = 5\n>>> value * 2\n10\n")
    assert blocks[0][-1] == "10"


def test_api_signature_links_to_src_layout():
    rendered = AutoDocs.get_obj("linescope.config:configure").get_signature()
    assert "blob/main/src/linescope/config.py#L" in rendered
    assert "configure" in rendered


def test_directive_inside_a_fence_stays_literal():
    source = "```text\n:: missing.module:example\n    :: signature\n```"
    assert render(source) == source


def test_example_directive_includes_current_source_without_executing(tmp_path, monkeypatch):
    from docs_sources.scripts import examples
    from docs_sources.scripts import render as render_page

    monkeypatch.setattr(examples, "EXAMPLES_DIR", tmp_path)
    example = tmp_path / "workload.py"
    source = "marker = ':: missing.module:example'\nraise RuntimeError('must not execute')\n"
    example.write_text(source, encoding="utf-8")
    assert render_page(":: example: workload.py") == f"```python\n{source}```"

    example.write_text("updated = 42\n", encoding="utf-8")
    assert render_page("    :: example: workload.py") == (
        "    ```python\n    updated = 42\n    ```"
    )


@pytest.mark.parametrize(
    ("filename", "error"), [("missing.py", FileNotFoundError), ("../outside.py", ValueError)]
)
def test_example_directive_rejects_missing_or_external_files(
    tmp_path, monkeypatch, filename, error
):
    from docs_sources.scripts import examples
    from docs_sources.scripts import render as render_page

    monkeypatch.setattr(examples, "EXAMPLES_DIR", tmp_path)
    with pytest.raises(error):
        render_page(f":: example: {filename}")


def test_example_directive_inside_a_fence_stays_literal():
    from docs_sources.scripts import render as render_page

    source = "```text\n:: example: missing.py\n```"
    assert render_page(source) == source


def test_dataclass_fields_are_documented_when_not_repeated_in_docstring():
    rendered = AutoDocs.get_obj("linescope.model:LineStats").get_table(["attributes"])
    assert "wall_time_ns" in rendered
    assert "Default: None" in rendered
    assert "Required constructor argument" in rendered


def test_example_fence_is_separated_from_following_method_table():
    rendered = render(":: linescope.source:SourceRegistry\n    :: examples\n    :: methods")
    assert "```<table" not in rendered
    assert "```\n\n<table" in rendered


def test_callable_controller_resolves_to_its_documented_class():
    controller = AutoDocs.get_obj("linescope:profile")
    assert controller.name == "ProfileController"
    assert "configure" in AutoDocs.get_obj("linescope:Session").get_see_also()


def test_method_reference_keeps_its_parent_anchor():
    method = AutoDocs.get_obj("linescope.api:Session.stop")
    assert method._parent_anchor == "session-"
    assert "#session-stop" in method.get_signature()


def test_nested_type_references_leave_container_syntax_intact():
    annotation = "dict[str, list[[SourceLocation]]] | [ProfileResult]"
    rendered = custom_autorefs(annotation)
    assert rendered == (
        "dict[str, list[[SourceLocation][sourcelocation]]] | [ProfileResult][profileresult]"
    )
    assert types_conversion("list[linescope.model.SourceLocation]") == ("list[[SourceLocation]]")
    assert custom_autorefs("Callable[[str], bool]") == "Callable[[str], bool]"


@pytest.mark.parametrize("name", ["DataFrame", "SparkSession", "InteractiveShell"])
def test_external_classes_link_to_their_official_reference(name):
    url = CUSTOM_URLS[name.lower()]
    assert custom_autorefs(f"[{name}]") == f"[{name}]({url})"
    assert custom_autorefs(f"list[[{name}]]") == f"list[[{name}]({url})]"


def test_autorefs_preserves_literal_code_and_explicit_markdown_links():
    source = (
        "`[ProfileResult]`\n\n"
        "```python\nvalue = list[ProfileResult]\n```\n\n"
        "[API](https://example.com/with/[brackets])\n\n"
        "![image](picture.png)\n\n"
        "[ref]: https://example.com/\n"
    )
    assert custom_autorefs(source) == source


def test_generated_dataclass_attribute_types_are_clickable():
    rendered = render(":: linescope.model:LineStats\n    :: table:\n        - attributes")
    assert "[SourceLocation][sourcelocation]" in rendered
    assert "[MemoryStats][memorystats]" in rendered
    assert "list[[SymbolRef][symbolref]]" in rendered


def test_methods_render_related_objects_without_error_sections():
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


def test_default_factory_is_validated_using_its_documented_expression():
    rendered = AutoDocs.get_obj("linescope.config:Config").get_table(["parameters"])
    assert "default=default_backend()" in rendered


def test_incorrect_documented_defaults_fail_the_build():
    def example(value=1):
        """Describe a default that disagrees with the signature.

        Parameters
        ----------
        value : int, default=2
            Invalid documented default.

        """

    with pytest.raises(ValueError, match="doesn't match"):
        AutoDocs(example).get_table(["parameters"])
