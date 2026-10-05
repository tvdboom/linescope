"""LineScope.

Author: Mavs
Description: Check runnable examples and matching notebook workloads.

"""

import ast
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from linescope import Session
from linescope.enums import SessionState
from linescope.source import build_navigation

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("script", "report"),
    [
        ("script_example.py", "linescope.html"),
        ("custom_backend_example.py", "custom-backend.html"),
        ("package_example.py", "package.html"),
    ],
)
def test_examples_save_reports_with_controlled_trace_collector(
    tmp_path,
    script,
    report,
):
    """Verify examples save reports with controlled trace collector.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    launcher = (
        "import pathlib, runpy, sys, webbrowser\n"
        "from linescope import configure\n"
        "from linescope.backends import scalene\n"
        "from linescope.backends.trace import TraceBackend\n"
        "class DemoCollector(TraceBackend):\n"
        "    name = 'scalene'\n"
        "    def __init__(self, *, memory, **options):\n"
        "        assert memory is False\n"
        "        super().__init__(memory=False, **options)\n"
        "scalene.ScaleneBackend = DemoCollector\n"
        "import linescope.memory as memory_module\n"
        "class DemoMemoryCollector:\n"
        "    def __init__(self, *args): pass\n"
        "    def start(self): pass\n"
        "    def stop(self): pass\n"
        "    def stop_tracing(self): pass\n"
        "    def finish_allocations(self): pass\n"
        "    def allocations(self): return {}, []\n"
        "    def snapshot(self): return {}, [], [], False\n"
        "memory_module.ProcessMemoryCollector = DemoMemoryCollector\n"
        "sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))\n"
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
    if script == "script_example.py":
        assert completed.stdout == ""
    else:
        assert completed.stdout.strip()
    html = (tmp_path / report).read_text(encoding="utf-8")
    assert "</html>" in html
    assert "scalene" in html
    assert script in html


@pytest.mark.scalene
@pytest.mark.parametrize(
    ("script", "report"),
    [
        ("script_example.py", "linescope.html"),
        ("custom_backend_example.py", "custom-backend.html"),
        ("package_example.py", "package.html"),
    ],
)
def test_demos_run_with_scalene_and_memory(tmp_path, script, report):
    """Verify demos run with scalene and memory.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    from tests.test_scalene import run_probe

    result = run_probe(
        tmp_path,
        rf"""
import ast, json, os, pathlib, re, runpy, sys, webbrowser
from linescope import configure
os.chdir({str(tmp_path)!r})
configure(output={str(tmp_path / report)!r})
webbrowser.open = lambda *args, **kwargs: True
sys.path.insert(0, {str(REPO_ROOT / "examples")!r})
runpy.run_path({str(REPO_ROOT / "examples" / script)!r}, run_name='__main__')
html = pathlib.Path({report!r}).read_text(encoding='utf-8')
rows = re.findall(
    r'<tr[^>]*class="source-row"[^>]*data-line="([0-9]+)"'
    r' data-time="([^"]*)" data-memory="([^"]*)"[^>]*>(.*?)</tr>',
    html,
    flags=re.DOTALL,
)
timed_lines = {{int(number) for number, duration, _, _ in rows if duration and int(duration) > 0}}
functions = ast.parse(pathlib.Path({str(REPO_ROOT / "examples" / script)!r}).read_text())
timed_functions = [
    node.name for node in functions.body
    if isinstance(node, ast.FunctionDef)
    and any(node.lineno <= number <= node.end_lineno for number in timed_lines)
]
memory_rows = [
    re.findall(r'<td class="metric">(.*?)</td>', cells)
    for _, _, delta, cells in rows if delta and int(delta) > 0
]
print(json.dumps({{
    'sampled': 'Estimated time' in html,
    'memory': 'Mem Change' in html,
    'memory_order': 'data-order="memory"' in html,
    'timed_functions': timed_functions,
    'timed': bool(timed_lines),
    'package_sources': '__main__.py' in html and 'helpers.py' in html,
    'memory_values': any(cells[2] != chr(8212) and cells[3] != chr(8212) for cells in memory_rows),
}}))
""",
        preload=False,
    )
    assert result["sampled"]
    assert result["memory"]
    assert result["memory_order"]
    if script == "package_example.py":
        assert result["timed"]
        assert result["package_sources"]
    else:
        assert result["memory_values"]
    if script == "script_example.py":
        assert {"generate_csv", "load_readings", "rolling_slow"} <= set(result["timed_functions"])


@pytest.fixture
def package_demo(monkeypatch):
    """Provide the package example and a controlled profiling factory.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    monkeypatch.syspath_prepend(str(REPO_ROOT / "examples"))
    demo = runpy.run_path(str(REPO_ROOT / "examples/package_example.py"))
    sessions = []

    def collect(**options):
        """Provide the controlled behavior used by this test.

        Create a controlled session while checking forwarded profiling options.

        """
        assert options["backend"] == "scalene"
        assert options["memory"] is True
        options.update(backend="trace", memory=False)
        session = Session(**options)
        sessions.append(session)
        return session

    demo["main"].__globals__["profile"] = collect
    return demo, sessions


def test_package_demo_captures_cross_module_sources_and_links(package_demo, monkeypatch, tmp_path):
    """Verify package demo captures cross module sources and links.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    demo, sessions = package_demo
    previous_trace = sys.gettrace()
    monkeypatch.chdir(tmp_path)
    demo["main"]()

    session = sessions[0]
    assert session.state is SessionState.STOPPED
    assert sys.gettrace() is previous_trace
    result = session.result
    paths = {
        Path(unit.path).relative_to(REPO_ROOT / "examples").as_posix()
        for unit in result.sources.values()
    }
    assert {
        "package_example.py",
        "sample_package/__main__.py",
        "sample_package/helpers.py",
    } <= paths
    assert "script_example.py" not in paths
    helper = next(unit for unit in result.sources.values() if unit.path.endswith("/helpers.py"))
    assert helper.source == (REPO_ROOT / "examples/sample_package/helpers.py").read_text(
        encoding="utf-8"
    )
    _, symbol_refs = build_navigation(result.sources)
    references = [ref for values in symbol_refs.values() for ref in values]
    assert {
        ref.name for ref in references if ref.target and ref.target.source_id == helper.id
    } >= {
        "prepare",
        "calculate",
    }
    assert any(
        line.location.source_id == helper.id and line.hits for line in result.root_run.lines
    )
    html = (tmp_path / "package.html").read_text(encoding="utf-8")
    assert "helpers.py" in html
    assert "__main__.py" in html
    assert "</html>" in html


def test_package_demo_restores_collection_after_workload_error(
    package_demo, monkeypatch, tmp_path
):
    """Verify package demo restores collection after workload error.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    demo, sessions = package_demo
    previous_trace = sys.gettrace()
    monkeypatch.chdir(tmp_path)

    def fail_workload(*, size):
        """Raise a workload error after the package example starts collection.

        Run the relevant example with controlled integrations and inspect report
        contents, configuration, or resource cleanup.

        """
        assert size > 0
        raise RuntimeError("Package workload failed")

    demo["main"].__globals__["run_package"] = fail_workload
    with pytest.raises(RuntimeError, match="Package workload failed"):
        demo["main"]()
    assert sessions[0].state is SessionState.STOPPED
    assert sys.gettrace() is previous_trace
    assert not (tmp_path / "package.html").exists()
    with Session(backend="trace", display="none", notebooks=False, spark=False):
        pass


@pytest.mark.parametrize("name", ["notebook_example", "spark_example", "databricks_example"])
def test_notebook_demos_request_scalene_and_memory(name):
    """Verify notebook demos request scalene and memory.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    notebook = json.loads(
        (REPO_ROOT / "examples/notebooks" / f"{name}.ipynb").read_text(encoding="utf-8")
    )
    requests = []
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        code = "".join(cell["source"])
        if code.startswith("%%profile"):
            options = code.splitlines()[0].split()
            assert options[options.index("--backend") + 1] == "scalene"
            assert "--memory" in options
            requests.append(code)
            continue
        if code.startswith("%"):
            continue
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "profile")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "start")
            ):
                keywords = {item.arg: ast.literal_eval(item.value) for item in node.keywords}
                if keywords.get("display") == "cell-summary":
                    assert keywords["backend"] == "trace"
                    continue
                assert keywords["backend"] == "scalene"
                assert keywords["memory"] is True
                requests.append(node)
    assert requests


@pytest.mark.parametrize("worker_python", [None, "configured-python"])
def test_spark_demo_configures_workers_before_startup(monkeypatch, mocker, worker_python):
    """Verify spark demo configures workers before startup.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    monkeypatch.setattr(os, "environ", os.environ.copy())
    if worker_python is None:
        monkeypatch.delenv("PYSPARK_PYTHON", raising=False)
    else:
        monkeypatch.setenv("PYSPARK_PYTHON", worker_python)

    builder = mocker.Mock()
    builder.master.return_value = builder
    builder.appName.return_value = builder
    builder.config.return_value = builder

    def fail_startup():
        """Reject Spark startup after checking configured worker limits.

        Run the relevant example with controlled integrations and inspect report
        contents, configuration, or resource cleanup.

        """
        assert os.environ["PYSPARK_PYTHON"] == (worker_python or sys.executable)
        raise RuntimeError("Spark startup unavailable")

    builder.getOrCreate.side_effect = fail_startup
    sql = ModuleType("pyspark.sql")
    sql.SparkSession = SimpleNamespace(builder=builder)
    sql.functions = ModuleType("pyspark.sql.functions")
    monkeypatch.setitem(sys.modules, "pyspark", ModuleType("pyspark"))
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    demo = runpy.run_path(str(REPO_ROOT / "examples/spark_example.py"))
    # This check owns worker selection; Java setup is covered independently.
    demo["main"].__globals__["prepare_java"] = lambda: None

    with pytest.raises(RuntimeError, match="Spark startup unavailable"):
        demo["main"]()
    builder.getOrCreate.assert_called_once_with()


def test_spark_notebook_uses_the_script_workload_and_cleanup():
    """Verify spark notebook uses the script workload and cleanup.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    script = ast.parse((REPO_ROOT / "examples/spark_example.py").read_text(encoding="utf-8"))
    imports = [node for node in script.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    main = next(
        node for node in script.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    helpers = [
        node for node in script.body if isinstance(node, ast.FunctionDef) and node.name != "main"
    ]
    workload = main.body[1:] if ast.get_docstring(main) is not None else main.body
    notebook = json.loads(
        (REPO_ROOT / "examples/notebooks/spark_example.ipynb").read_text(encoding="utf-8")
    )
    code = "\n\n".join(
        "".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"
    )
    expected = ast.Module(body=[*imports, *helpers, *workload], type_ignores=[])
    assert ast.dump(ast.parse(code)) == ast.dump(expected)


@pytest.fixture
def spark_java_setup(monkeypatch):
    """Load the Spark example Java discovery helper without starting Spark.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    sql = ModuleType("pyspark.sql")
    sql.SparkSession = SimpleNamespace(builder=None)
    sql.functions = ModuleType("pyspark.sql.functions")
    monkeypatch.setitem(sys.modules, "pyspark", ModuleType("pyspark"))
    monkeypatch.setitem(sys.modules, "pyspark.sql", sql)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    return runpy.run_path(str(REPO_ROOT / "examples/spark_example.py"))["prepare_java"]


def test_spark_java_setup_preserves_java_on_path(spark_java_setup, monkeypatch):
    """Verify spark java setup preserves java on path.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/java")
    spark_java_setup()
    assert "JAVA_HOME" not in os.environ


def test_spark_java_setup_preserves_explicit_java_home(spark_java_setup, monkeypatch, tmp_path):
    """Verify spark java setup preserves explicit java home.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    executable = tmp_path / "bin" / ("java.exe" if sys.platform == "win32" else "java")
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    spark_java_setup()
    assert os.environ["JAVA_HOME"] == str(tmp_path)


def test_spark_java_setup_rejects_invalid_explicit_home(spark_java_setup, monkeypatch, tmp_path):
    """Verify spark java setup rejects invalid explicit home.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/java")
    with pytest.raises(SystemExit, match=r"JAVA_HOME points to .* is missing"):
        spark_java_setup()
    assert os.environ["JAVA_HOME"] == str(tmp_path)


@pytest.mark.parametrize("saved_hive", ["user", "machine", None])
def test_spark_java_setup_reads_saved_windows_environment(
    spark_java_setup, monkeypatch, tmp_path, saved_hive
):
    """Verify spark java setup reads saved windows environment.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    executable = tmp_path / "bin/java.exe"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    registry = ModuleType("winreg")
    registry.HKEY_CURRENT_USER = "user"
    registry.HKEY_LOCAL_MACHINE = "machine"

    class Key:
        """Emulate a Windows registry key for Java discovery tests.

        Attributes
        ----------
        hive : str
            Simulated Windows registry hive used to select a saved Java path.

        """

        def __init__(self, hive):
            """Initialize the controlled test state and recorded observations.

            Retain only the state needed to observe arguments, results, and
            cleanup in the surrounding test.

            """
            self.hive = hive

        def __enter__(self):
            """Return the simulated resource when entering its context.

            Keep the same object available to calls inside the managed block.

            """
            return self

        def __exit__(self, *_args):
            """Leave the simulated resource context without suppressing errors.

            Return a false value so the surrounding test still observes any
            original exception.

            """
            return False

    def read_value(key, name):
        """Return a configured saved Java path from the simulated registry key.

        Run the relevant example with controlled integrations and inspect report
        contents, configuration, or resource cleanup.

        """
        assert name == "JAVA_HOME"
        if key.hive == saved_hive:
            return str(tmp_path), 1
        raise FileNotFoundError("JAVA_HOME")

    registry.OpenKey = lambda hive, _path: Key(hive)
    registry.QueryValueEx = read_value
    monkeypatch.setitem(sys.modules, "winreg", registry)
    if saved_hive is None:
        with pytest.raises(SystemExit, match="Local Spark requires Java"):
            spark_java_setup()
        assert "JAVA_HOME" not in os.environ
    else:
        spark_java_setup()
        assert os.environ["JAVA_HOME"] == str(tmp_path)


def test_databricks_source_export_matches_notebook_cells():
    """Verify databricks source export matches notebook cells.

    Run the relevant example with controlled integrations and inspect report
    contents, configuration, or resource cleanup.

    """
    source = (REPO_ROOT / "examples/databricks_example.py").read_text(encoding="utf-8")
    notebook = json.loads(
        (REPO_ROOT / "examples/notebooks/databricks_example.ipynb").read_text(encoding="utf-8")
    )

    def code_lines(source):
        """Extract executable notebook source for comparison with its export.

        Run the relevant example with controlled integrations and inspect report
        contents, configuration, or resource cleanup.

        """
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
