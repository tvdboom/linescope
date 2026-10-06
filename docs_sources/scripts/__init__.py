"""LineScope.

Author: Mavs
Description: Import classes and functions exposed in mkdocs.yml.

"""

from .autodocs import clean_search, corrections
from .autodocs import render as render_autodocs
from .autorun import formatter
from .examples import include_examples
from .notebooks import cleanup_notebooks, finish_build, prepare_notebooks

__all__ = [
    "clean_search",
    "cleanup_notebooks",
    "corrections",
    "finish_build",
    "formatter",
    "prepare_notebooks",
    "render",
]


def render(markdown: str, **kwargs: object) -> str:
    """Render example source and API directives in a documentation page.

    Include runnable examples before rendering API directives so the inserted
    Python fences preserve source text without executing it.

    Parameters
    ----------
    markdown : str
        Markdown source of the page.

    **kwargs
        MkDocs page, configuration, and file context.

    Returns
    -------
    str
        Markdown with example source and API content rendered.

    """
    return render_autodocs(include_examples(markdown), **kwargs)
