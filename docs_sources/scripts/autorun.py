"""LineScope.

Author: Mavs
Description: Execute explicit documentation examples using Backtide's pycon
fence convention.

"""

from __future__ import annotations

import ast
from contextlib import redirect_stdout
from doctest import DocTestParser
from io import StringIO
import sys

from markdown import Markdown
from pymdownx.superfences import SuperFencesException


def execute(src: str) -> tuple[list[list[str]], list[str]]:
    """Execute a trusted repository example and collect its Python transcript.

    Each fence has an isolated namespace. Exceptions propagate to fail the
    build. This executes trusted project documentation; it is not an
    untrusted-code sandbox.

    Parameters
    ----------
    src : str
        Python code. A statement ending with `# hide` runs without appearing;
        a statement ending with `# norun` appears without running.

    Returns
    -------
    tuple[list[list[str]], list[str]]
        Transcript blocks and supplementary HTML, matching Backtide's hook
        API.

    """
    if any(line.lstrip().startswith(">>> ") for line in src.splitlines()):
        src = "\n".join(example.source for example in DocTestParser().get_examples(src))

    lines = src.splitlines()
    namespace = {"__name__": "__linescope_docs__"}
    transcript: list[str] = []

    for node in ast.parse(src).body:
        block = lines[node.lineno - 1 : node.end_lineno]
        hidden = block[0].rstrip().endswith("# hide")
        skipped = block[0].rstrip().endswith("# norun")

        if not hidden:
            for number, line in enumerate(block):
                prefix = ">>> " if number == 0 else "... "
                transcript.append(prefix + line.removesuffix("# norun").rstrip())

        if skipped:
            continue

        output = StringIO()
        original_hook = sys.displayhook

        def display(value: object) -> None:
            """Capture an executable example's displayed expression value.

            Preserve output for rendering while suppressing None expression
            results.

            """
            if value is not None:
                print(repr(value))  # noqa: T201 - capture Python's display transcript

        try:
            sys.displayhook = display

            with redirect_stdout(output):
                code = compile(ast.Interactive(body=[node]), "<documentation>", "single")
                exec(code, namespace)
        finally:
            sys.displayhook = original_hook

        if not hidden:
            transcript.extend(output.getvalue().splitlines())

    return [transcript], []


def formatter(
    src: str,
    language: str,
    css_class: str,
    options: dict | None,
    md: Markdown,
    **kwargs: object,
) -> str:
    """Render an executable `pycon` fence and fail on example errors.

    Parameters
    ----------
    src : str
        Trusted repository source to execute.

    language : str
        Fence language passed by SuperFences.

    css_class : str
        Highlighting class name.

    options : dict | None
        Fence options passed by SuperFences.

    md : Markdown
        Markdown renderer.

    **kwargs : object
        Additional SuperFences options.

    Returns
    -------
    str
        Syntax-highlighted transcript HTML.

    """
    try:
        blocks, _ = execute(src.strip())
    except Exception as exc:
        raise SuperFencesException(f"Example failed:\n{src}") from exc

    # Reuse SuperFences' normal formatter so Material supplies the same code
    # container, highlighting, line numbers, and copy controls as other fences.
    to_html = md.preprocessors["fenced_code_block"].extension.superfences[0]["formatter"]
    return to_html(
        src="\n".join(blocks[0]),
        class_name=css_class,
        language=language,
        md=md,
        options=options,
        **kwargs,
    )
