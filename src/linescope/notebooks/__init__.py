"""Notebook capture and explicit child-profile correlation."""

from linescope.notebooks.correlation import ChildContext, merge_child
from linescope.notebooks.ipython import (
    NotebookIntegration,
    load_ipython_extension,
    unload_ipython_extension,
)

__all__ = [
    "ChildContext",
    "NotebookIntegration",
    "load_ipython_extension",
    "merge_child",
    "unload_ipython_extension",
]
