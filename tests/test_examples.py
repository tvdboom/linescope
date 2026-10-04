"""LineScope.

Author: Mavs
Description: Check runnable examples and matching notebook workloads.

"""

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("script", "report"),
    [
        ("script.py", "linescope.html"),
        ("custom_backend.py", "custom-backend.html"),
    ],
)
def test_portable_examples_save_reports_without_optional_dependencies(
    tmp_path,
    script,
    report,
):
    launcher = (
        "import runpy, sys, webbrowser\n"
        "from linescope import configure\n"
        "configure(output=sys.argv[2])\n"
        "webbrowser.open = lambda *args, **kwargs: True\n"
        "runpy.run_path(sys.argv[1], run_name='__main__')\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            launcher,
            str(REPO_ROOT / "examples" / script),
            str(tmp_path / report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()
    html = (tmp_path / report).read_text(encoding="utf-8")
    assert "</html>" in html
    assert "trace" in html
    assert script in html


def test_spark_notebook_uses_the_script_workload_and_cleanup():
    script = ast.parse((REPO_ROOT / "examples/local_spark.py").read_text(encoding="utf-8"))
    imports = [node for node in script.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    main = next(node for node in script.body if isinstance(node, ast.FunctionDef))
    workload = main.body[1:] if ast.get_docstring(main) is not None else main.body
    notebook = json.loads(
        (REPO_ROOT / "examples/notebooks/local_spark.ipynb").read_text(encoding="utf-8")
    )
    code = "\n\n".join(
        "".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"
    )
    expected = ast.Module(body=[*imports, *workload], type_ignores=[])
    assert ast.dump(ast.parse(code)) == ast.dump(expected)


def test_databricks_source_export_matches_notebook_cells():
    source = (REPO_ROOT / "examples/databricks_notebook.py").read_text(encoding="utf-8")
    notebook = json.loads(
        (REPO_ROOT / "examples/notebooks/databricks.ipynb").read_text(encoding="utf-8")
    )

    def code_lines(source):
        return [
            line
            for raw in source.splitlines()
            if (line := raw.removeprefix("# MAGIC ")).strip() and not line.lstrip().startswith("#")
        ]

    exported = [
        lines for cell in source.split("# COMMAND ----------")[1:] if (lines := code_lines(cell))
    ]
    cells = [
        code_lines("".join(cell["source"]))
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    ]
    assert exported == cells
