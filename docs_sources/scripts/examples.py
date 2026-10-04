"""LineScope.

Author: Mavs
Description: Include runnable example source in documentation pages.

"""

from pathlib import Path
import re

from .autodocs import FENCE_RE

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
EXAMPLE_RE = re.compile(r"^([ \t]*):: example: ([^\n]+)$", re.MULTILINE)


def include_examples(markdown: str) -> str:
    """Include complete example files without executing them.

    Expand `:: example: filename.py` directives into ordinary Python fences.
    Resolve paths within the repository's examples directory and leave
    directives inside existing fences literal.

    Raise `ValueError` if a directive refers to a file outside the examples
    directory.

    Raise `FileNotFoundError` if an example file does not exist.

    Parameters
    ----------
    markdown : str
        Markdown source containing example directives.

    Returns
    -------
    str
        Markdown with the current runnable source inserted.

    """
    fences: list[str] = []

    def protect_fence(match: re.Match[str]) -> str:
        fences.append(match.group())
        return f"\x00EXAMPLE_FENCE_{len(fences) - 1}\x00"

    def include(match: re.Match[str]) -> str:
        indent, filename = match.groups()
        path = (EXAMPLES_DIR / filename.strip()).resolve()
        if not path.is_relative_to(EXAMPLES_DIR.resolve()):
            raise ValueError(f"Example source must be inside examples: {filename}")
        source = path.read_text(encoding="utf-8")
        lines = ["```python", *source.splitlines(), "```"]
        return "\n".join(indent + line for line in lines)

    markdown = FENCE_RE.sub(protect_fence, markdown)
    markdown = EXAMPLE_RE.sub(include, markdown)
    for index, fence in enumerate(fences):
        markdown = markdown.replace(f"\x00EXAMPLE_FENCE_{index}\x00", fence)
    return markdown
