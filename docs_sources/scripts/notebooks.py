"""LineScope.

Author: Mavs
Description: Stage documentation notebooks for isolated build execution.

"""

from pathlib import Path, PurePosixPath
import shutil
from tempfile import TemporaryDirectory

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files

from .autodocs import clean_search

_workspace: TemporaryDirectory[str] | None = None


def prepare_notebooks(files: Files, config: MkDocsConfig) -> Files:
    """Execute documentation notebooks from owned temporary source copies.

    Preserve page URLs and source downloads while directing notebook kernels
    and generated reports away from the repository. Leave source-only builds
    unchanged when notebook execution is disabled explicitly.

    Parameters
    ----------
    files : Files
        Documentation files selected by MkDocs, including notebook wrappers.

    config : MkDocsConfig
        Build configuration containing the notebook execution setting.

    Returns
    -------
    Files
        The same file collection with staged notebook source paths.

    """
    global _workspace
    cleanup_notebooks()
    if not config.plugins["mkdocs-jupyter"].config["execute"]:
        return files

    _workspace = TemporaryDirectory(prefix="linescope-docs-")
    root = Path(_workspace.name)
    try:
        for file in files:
            path = PurePosixPath(file.src_uri)
            if path.suffix != ".ipynb" or not path.is_relative_to("examples/notebooks"):
                continue

            staged = root.joinpath(*path.parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file.abs_src_path, staged)
            file.abs_src_path = str(staged)
    except OSError:
        cleanup_notebooks()
        raise

    return files


def cleanup_notebooks(**_kwargs: object) -> None:
    """Remove owned notebook copies and reports after success or failure.

    Release the temporary workspace even when notebook execution or a later
    documentation hook fails. Repeated cleanup calls are harmless.

    Parameters
    ----------
    **_kwargs : object
        MkDocs event context, including the error on failed builds.

    """
    global _workspace
    if _workspace is not None:
        _workspace.cleanup()
        _workspace = None


def finish_build(config: MkDocsConfig) -> None:
    """Clean the search index and release notebook build resources.

    Restore the temporary workspace even if search-index cleanup fails.

    Parameters
    ----------
    config : MkDocsConfig
        Completed build configuration used to locate the search index.

    """
    try:
        clean_search(config)
    finally:
        cleanup_notebooks()
