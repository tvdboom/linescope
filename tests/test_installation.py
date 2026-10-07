"""LineScope.

Author: Mavs
Description: Verify optional dependencies and default profiling in isolation.

"""

from importlib.metadata import metadata, requires
from pathlib import Path
import subprocess
import sys

from packaging.requirements import Requirement
import pytest


@pytest.mark.parametrize("extra", ["", "notebook", "scalene"])
@pytest.mark.parametrize("python_version", ["3.11", "3.14", "3.15"])
def test_scalene_dependency_is_optional(extra: str, python_version: str):
    """Require Scalene only when its extra and Python version support it.

    Inspect installed distribution metadata so wheel tests cover dependency
    markers as well as the source configuration.

    Parameters
    ----------
    extra : str
        Installation extra, or an empty string for the base package.

    python_version : str
        Python minor version supplied to dependency-marker evaluation.

    """
    dependencies = [Requirement(value) for value in requires("linescope") or []]
    scalene = next(value for value in dependencies if value.name == "scalene")
    assert scalene.marker is not None
    environment = {
        "extra": extra,
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
    }
    assert scalene.marker.evaluate(environment) == (extra == "scalene" and python_version < "3.15")


def test_full_extra_includes_scalene():
    """Include Scalene and notebook support in the full installation.

    Follow the self-referencing extra in installed distribution metadata.

    """
    dependencies = [Requirement(value) for value in requires("linescope") or []]
    full = next(
        value
        for value in dependencies
        if value.name == "linescope"
        and value.marker is not None
        and value.marker.evaluate({"extra": "full"})
    )
    assert full.extras == {"scalene", "notebook"}


def test_runtime_integrations_are_not_installable_dependencies():
    """Keep Spark and Databricks dependencies out of package installations.

    Inspect wheel metadata, including every extra, so full installations also
    reuse the environment's runtime libraries.

    """
    dependencies = [Requirement(value) for value in requires("linescope") or []]
    assert not {"pyspark", "databricks-sdk"} & {value.name for value in dependencies}
    assert set(metadata("linescope").get_all("Provides-Extra") or []) == {
        "scalene",
        "notebook",
        "full",
    }


def test_default_api_and_cli_work_without_runtime_integrations(tmp_path: Path):
    """Profile with Trace without optional profiler or runtime libraries.

    Use a fresh interpreter with optional imports blocked. Exercise both public
    entry points with memory enabled and verify source hits and hook cleanup.

    Parameters
    ----------
    tmp_path : Path
        Isolated source and report directory supplied by pytest.

    """
    source = r"""
from pathlib import Path
import sys

sys.modules["scalene"] = None
sys.modules["pyspark"] = None
sys.modules["databricks"] = None

from click.testing import CliRunner
from linescope import cli, profile

root = Path(sys.argv[1])
script = root / "workload.py"
script.write_text("value = sum(range(10))\n", encoding="utf-8")
previous_trace = sys.gettrace()
previous_profile = sys.getprofile()
with profile(
    root=str(root), display="none", notebooks=False, memory=True
) as session:
    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"))
assert session.result.backend == "trace"
assert any(line.hits for line in session.result.root_run.lines)
assert sys.gettrace() is previous_trace
assert sys.getprofile() is previous_profile

report = root / "report.html"
result = CliRunner().invoke(cli.main, [
    "--root", str(root), "--no-notebooks", "--memory",
    "--display", "none", "-o", str(report), str(script),
])
assert result.exit_code == 0, result.output
assert "backends/#trace" in report.read_text(encoding="utf-8")
assert sys.gettrace() is previous_trace
assert sys.getprofile() is previous_profile
assert "linescope.backends.scalene" not in sys.modules

missing_spark = CliRunner().invoke(cli.main, [
    "--root", str(root), "--spark", "--display", "none", str(script),
])
assert isinstance(missing_spark.exception, ImportError)
assert "PySpark in the existing environment" in str(missing_spark.exception)
assert sys.gettrace() is previous_trace
assert sys.getprofile() is previous_profile
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", source, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
