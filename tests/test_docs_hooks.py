"""Regression tests for executable documentation and API source links."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("mkdocs")
pytest.importorskip("regex")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docs_sources.scripts.autodocs import AutoDocs, render
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


def test_dataclass_fields_are_documented_when_not_repeated_in_docstring():
    rendered = AutoDocs.get_obj("linescope.model:LineStats").get_table(["attributes"])
    assert "wall_time_ns" in rendered
    assert "Default: None" in rendered
    assert "Required constructor argument" in rendered


def test_example_fence_is_separated_from_following_method_table():
    rendered = render(":: linescope.source:SourceRegistry\n    :: examples\n    :: methods")
    assert "```<table" not in rendered
    assert "```\n\n<table" in rendered
