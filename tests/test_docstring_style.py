"""LineScope.

Author: Mavs
Description: Keep repository docstrings consistent with the public API style.

"""

import ast
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = sorted(
    path
    for folder in ("src", "examples", "docs_sources/scripts", "tests")
    for path in (ROOT / folder).rglob("*.py")
)
MARKDOWN_FILES = sorted((ROOT / "docs_sources").rglob("*.md"))


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_docstrings_follow_repository_conventions(path):
    """Verify docstrings follow repository conventions.

    Inspect repository source directly so style requirements also cover
    interfaces exempted from Ruff docstring linting.

    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree)
    assert module_doc is not None
    assert module_doc.startswith("LineScope.\n\nAuthor: Mavs\nDescription: ")

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        doc = ast.get_docstring(node)
        assert doc is not None, f"Line {getattr(node, 'lineno', 1)}: add a docstring"

        value = node.body[0].value
        literal = ast.get_source_segment(source, value)
        assert literal is not None
        lines = literal.splitlines()
        assert len(lines) >= 3, f"Line {value.lineno}: use a multiline docstring"
        assert not lines[-2].strip(), f"Line {value.lineno}: leave a closing blank line"
        for index, line in enumerate(lines):
            width = len(line) + (value.col_offset if index == 0 else 0)
            assert width <= 80, f"Line {value.lineno + index}: docstring exceeds 80 columns"

        assert not re.search(r"(?<!`)``(?!`)", doc), "Use single backticks for inline code"
        assert not re.search(r"^(Notes|Raises)\n-+", doc, re.MULTILINE)

        for section in ("Parameters", "Attributes"):
            match = re.search(
                rf"^{section}\n-+\n(.*?)(?=\n[A-Z][A-Za-z ]+\n-+|\Z)",
                doc,
                re.MULTILINE | re.DOTALL,
            )
            if match is None:
                continue

            entries = match.group(1).splitlines()
            assert not re.search(r"^\w+\s*:[^\n]*, optional", match.group(1), re.MULTILINE)
            for index, line in enumerate(entries):
                if index and re.match(r"^\*{0,2}\w+(?:, \w+)*\s*:", line):
                    assert not entries[index - 1].strip(), f"Separate the field {line!r}"


@pytest.mark.parametrize(
    "path", MARKDOWN_FILES, ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_markdown_uses_short_lines_and_single_inline_backticks(path):
    """Verify markdown uses short lines and single inline backticks.

    Inspect repository source directly so style requirements also cover
    interfaces exempted from Ruff docstring linting.

    """
    source = path.read_text(encoding="utf-8")
    for number, line in enumerate(source.splitlines(), 1):
        assert len(line) <= 80, f"Line {number}: Markdown exceeds 80 columns"
    assert not re.search(r"(?<!`)``(?!`)", source)
    assert not re.search(r"^\s*- (notes|raises)$", source, re.MULTILINE)
