"""LineScope.

Author: Mavs
Description: Stage documentation notebooks for isolated build execution.

"""

import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
from tempfile import TemporaryDirectory

from mkdocs.config.base import Config
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files

from .autodocs import clean_search

_SPARK_NOTEBOOK = "examples/notebooks/spark_example.ipynb"
_workspace: TemporaryDirectory[str] | None = None
_spark_execution: tuple[Config, list[str]] | None = None


def _java_available() -> bool:
    """Find Java using the Spark notebook's environment discovery rules.

    Honor an explicit `JAVA_HOME` before checking `PATH`. On Windows, also
    inspect saved environment settings when the current process has neither.
    Leave the process environment unchanged; the notebook owns Spark setup.

    Returns
    -------
    bool
        Whether the configured Java executable exists. This does not start
        Java or verify compatibility with the installed Spark version.

    """
    java_home = os.environ.get("JAVA_HOME")
    if not java_home and shutil.which("java"):
        return True

    if not java_home and sys.platform == "win32":
        import winreg

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ):
            try:
                with winreg.OpenKey(hive, key) as environment:
                    value, _ = winreg.QueryValueEx(environment, "JAVA_HOME")
            except (FileNotFoundError, PermissionError):
                continue

            if isinstance(value, str) and value:
                java_home = os.path.expandvars(value)
                break

    if not java_home:
        return False

    executable = "java.exe" if sys.platform == "win32" else "java"
    return (Path(java_home) / "bin" / executable).is_file()


def prepare_notebooks(files: Files, config: MkDocsConfig) -> Files:
    """Execute documentation notebooks from owned temporary source copies.

    Preserve page URLs and source downloads while directing notebook kernels
    and generated reports away from the repository. Leave source-only builds
    unchanged when notebook execution is disabled explicitly. Render the Spark
    notebook's saved content when Java is unavailable, while continuing to
    execute other portable notebooks. Available Java does not suppress Spark
    execution errors.

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
    global _spark_execution, _workspace
    cleanup_notebooks()
    notebook_config = config.plugins["mkdocs-jupyter"].config
    if not notebook_config["execute"]:
        return files

    _workspace = TemporaryDirectory(prefix="linescope-docs-")
    root = Path(_workspace.name)
    try:
        for file in files:
            path = PurePosixPath(file.src_uri)
            if path.suffix != ".ipynb" or not path.is_relative_to("examples/notebooks"):
                continue

            ignored = notebook_config["execute_ignore"]
            if (
                path.as_posix() == _SPARK_NOTEBOOK
                and not any(Path(file.abs_src_path).match(pattern) for pattern in ignored)
                and not _java_available()
            ):
                # Restore this build's temporary exclusion before the next serve rebuild.
                _spark_execution = notebook_config, ignored
                notebook_config["execute_ignore"] = [*ignored, "spark_example.ipynb"]
                logging.getLogger("mkdocs.plugins.linescope.notebooks").info(
                    "Java is unavailable; rendering the Spark notebook without execution. "
                    "Install Java and rebuild to generate current Spark outputs."
                )

            staged = root.joinpath(*path.parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file.abs_src_path, staged)
            file.abs_src_path = str(staged)
    except OSError:
        cleanup_notebooks()
        raise

    return files


def cleanup_notebooks(**_kwargs: object):
    """Remove owned notebook copies and reports after success or failure.

    Restore notebook execution settings and release the temporary workspace
    even when notebook execution or a later documentation hook fails.
    Repeated cleanup calls are harmless.

    Parameters
    ----------
    **_kwargs : object
        MkDocs event context, including the error on failed builds.

    """
    global _spark_execution, _workspace
    if _spark_execution is not None:
        notebook_config, ignored = _spark_execution
        notebook_config["execute_ignore"] = ignored
        _spark_execution = None

    if _workspace is not None:
        _workspace.cleanup()
        _workspace = None


def finish_build(config: MkDocsConfig):
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
