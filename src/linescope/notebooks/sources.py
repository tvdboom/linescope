"""LineScope.

Author: Mavs
Description: Read notebook positions and recognize generated magic wrappers.

"""

import ast
import hashlib
import json
from pathlib import Path

from linescope.enums import SourceKind
from linescope.model import SourceUnit


def profile_cell_source(source: str) -> str:
    """Read a Python magic's body for notebook-position matching.

    Parameters
    ----------
    source : str
        Original notebook cell, possibly starting with a Python magic.

    Returns
    -------
    str
        Python body for LineScope or Python magics, or unchanged source.

    """
    first, separator, body = source.partition("\n")
    if separator and first.split(maxsplit=1)[:1] in (
        ["%%profile"],
        ["%%linescope"],
        ["%python"],
        ["%%python"],
    ):
        return body
    return source


def local_notebook_sources(path: str) -> list[SourceUnit]:
    """Read saved Jupyter cells as a private index of notebook positions.

    Count markdown and raw cells when assigning positions. Reading this index
    does not register any source for display or imply that a cell executed.

    Parameters
    ----------
    path : str
        Explicit notebook filename reported by the frontend.

    Returns
    -------
    list[[SourceUnit]]
        Code cells with saved frontend identifiers and one-based positions.

    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("cells"), list):
        raise ValueError("Notebook cell metadata unavailable")

    units = []
    for number, cell in enumerate(document["cells"], 1):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = "".join(source)
        if not isinstance(source, str):
            continue
        cell_id = cell.get("id")
        digest = hashlib.sha256(source.encode()).hexdigest()[:12]
        units.append(
            SourceUnit(
                f"notebook://{path}#cell-{number}-{digest}",
                f"{path} · cell {number}",
                source,
                SourceKind.NOTEBOOK,
                cell_number=number,
                cell_id=cell_id if isinstance(cell_id, str) else None,
            )
        )
    return units


def python_magic_source(source: str) -> str | None:
    """Extract user Python from a generated Databricks cell-magic wrapper.

    Recognize only a standalone `get_ipython().run_cell_magic` call with a
    literal Python body. Never execute or evaluate notebook source.

    Parameters
    ----------
    source : str
        Cached compiler source that may contain a runtime-generated wrapper.

    Returns
    -------
    str | None
        Original Python body, or None for ordinary source and other magics.

    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return None
    call = tree.body[0].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
        return None
    owner = call.func.value
    if (
        call.func.attr != "run_cell_magic"
        or not isinstance(owner, ast.Call)
        or not isinstance(owner.func, ast.Name)
        or owner.func.id != "get_ipython"
        or owner.args
        or owner.keywords
        or len(call.args) != 3
        or call.keywords
        or not isinstance(call.args[0], ast.Constant)
        or call.args[0].value not in {"python", "profile", "linescope"}
        or not isinstance(call.args[2], ast.Constant)
        or not isinstance(call.args[2].value, str)
    ):
        return None
    return call.args[2].value
