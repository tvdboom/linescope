"""LineScope.

Author: Mavs
Description: Script and module CLI execution, configuration, and restoration
tests.

"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from linescope import cli


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    """Provide isolated configuration.

    Remove persistent API overrides from each CLI test.

    """
    monkeypatch.setattr("linescope.config._overrides", {})
    monkeypatch.setattr("webbrowser.open", lambda _url, **_kwargs: True)


def cli_options(report):
    """Provide cli options.

    Use the portable backend and explicit headless report destination.

    """
    return [
        "--backend",
        "trace",
        "--no-spark",
        "--no-notebooks",
        "--display",
        "none",
        "-o",
        str(report),
    ]


class TestCommandParsing:
    """Check command parsing.

    Test CLI discovery, errors, and argument boundaries.

    """

    @pytest.mark.parametrize(
        ("argument", "expected"),
        [("--help", "Profile your Python source"), ("--version", "LineScope 0.1.0")],
    )
    def test_help_and_version(self, argument, expected, capsys):
        with pytest.raises(SystemExit) as error:
            cli.main([argument])
        assert error.value.code == 0
        assert expected in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ([], "provide a script or -m MODULE"),
            (["--not-a-real-option"], "unrecognized arguments"),
            (["--display", "invalid", "main.py"], "invalid choice"),
            (["--backend"], "expected one argument"),
            (["-m"], "-m requires a module name"),
        ],
    )
    def test_invalid_arguments(self, arguments, message, capsys):
        with pytest.raises(SystemExit) as error:
            cli.main(arguments)
        assert error.value.code == 2
        assert message in capsys.readouterr().err

    def test_missing_script(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as error:
            cli.main([str(tmp_path / "missing.py")])
        assert error.value.code == 2
        assert "script does not exist" in capsys.readouterr().err

    def test_target_directory_rejected(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli.main([str(tmp_path)])
        assert "script does not exist" in capsys.readouterr().err

    def test_repeated_include_exclude_options(self):
        parsed = cli.build_parser().parse_args(
            [
                "--include",
                "pkg",
                "--include",
                "other",
                "--exclude",
                "tests",
                "--exclude",
                "**/generated.py",
                "script.py",
                "--memory",
            ]
        )
        assert parsed.include == ["pkg", "other"]
        assert parsed.exclude == ["tests", "**/generated.py"]
        assert parsed.target == ["script.py", "--memory"]
        assert parsed.memory is None


class TestScriptExecution:
    """Check script execution.

    Run actual scripts through the CLI without opening a browser.

    """

    def test_script_argv_unicode_paths_and_process_restoration(self, tmp_path, capsys):
        script = tmp_path / "worker café.py"
        data = tmp_path / "arguments.json"
        report = tmp_path / "profile.html"
        script.write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text(json.dumps({'argv': sys.argv, 'name': __name__, "
            "'file': __file__, 'path': sys.path[0]}))\n",
            encoding="utf-8",
        )
        original_argv, original_path = sys.argv, sys.path
        argv_values, path_values = sys.argv[:], sys.path[:]
        assert (
            cli.main([*cli_options(report), str(script), str(data), "--flag", "two words", "🍋"])
            == 0
        )
        recorded = json.loads(data.read_text())
        assert recorded["argv"] == [str(script), str(data), "--flag", "two words", "🍋"]
        assert recorded["name"] == "__main__"
        assert recorded["file"] == str(script)
        assert recorded["path"] == str(tmp_path)
        assert sys.argv is original_argv
        assert sys.path is original_path
        assert sys.argv == argv_values
        assert sys.path == path_values
        assert report.is_file()
        assert "LineScope report:" in capsys.readouterr().err

    def test_explicit_argument_separator(self, tmp_path):
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        assert cli.main([*cli_options(report), "--", str(script)]) == 0
        assert "value = " in report.read_text(encoding="utf-8")

    def test_reads_default_argv(self, tmp_path, monkeypatch):
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        monkeypatch.setattr(sys, "argv", ["linescope", *cli_options(report), str(script)])
        assert cli.main() == 0
        assert report.is_file()

    def test_nearest_project_settings(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.linescope]\nbackend="trace"\nnotebooks=false\nspark=false\nexclude=["ignored.py"]\n'
        )
        subdirectory = tmp_path / "src"
        subdirectory.mkdir()
        script = subdirectory / "worker.py"
        script.write_text("PROJECT_CONFIG_WORKLOAD = 42\n")
        report = tmp_path / "profile.html"
        assert cli.main(["-o", str(report), str(script)]) == 0
        text = report.read_text(encoding="utf-8")
        assert "PROJECT_CONFIG_WORKLOAD" in text
        assert "trace" in text

    def test_actual_include_and_exclude_filtering(self, tmp_path, monkeypatch):
        del monkeypatch
        package = tmp_path / "cli_filter_package"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "keep.py").write_text("def work():\n    return 'INCLUDED_SOURCE_MARKER'\n")
        (package / "omit.py").write_text("def work():\n    return 'EXCLUDED_SOURCE_MARKER'\n")
        script = tmp_path / "worker.py"
        script.write_text(
            "from cli_filter_package.keep import work\nfrom cli_filter_package.omit import"
            " work as omitted\nwork()\nomitted()\n"
        )
        report = tmp_path / "profile.html"
        try:
            assert (
                cli.main(
                    [
                        *cli_options(report),
                        "--include",
                        "cli_filter_package",
                        "--include",
                        "worker.py",
                        "--exclude",
                        "cli_filter_package/omit.py",
                        str(script),
                    ]
                )
                == 0
            )
        finally:
            for name in (
                "cli_filter_package",
                "cli_filter_package.keep",
                "cli_filter_package.omit",
            ):
                sys.modules.pop(name, None)
        html = report.read_text(encoding="utf-8")
        assert "INCLUDED_SOURCE_MARKER" in html
        assert "EXCLUDED_SOURCE_MARKER" not in html

    def test_output_destination_from_project_configuration(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.linescope]\nbackend="trace"\nnotebooks=false\nspark=false\noutput="custom.html"\n'
        )
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        assert cli.main([str(script)]) == 0
        assert (tmp_path / "custom.html").is_file()
        assert not (tmp_path / "linescope.html").exists()

    def test_explicit_display_opens_one_saved_report(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_kwargs: opened.append(url))
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        assert cli.main([*cli_options(report), "--display", "end", str(script)]) == 0
        assert opened == [report.as_uri()]

    def test_explicit_headless_display(self, tmp_path, monkeypatch):
        def forbidden(*args, **kwargs):
            del args, kwargs
            pytest.fail("Headless CLI unexpectedly opened a browser")

        monkeypatch.setattr("webbrowser.open", forbidden)
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        assert cli.main([*cli_options(tmp_path / "profile.html"), str(script)]) == 0

    def test_default_display_opens_temporary_report_in_new_tab(self, tmp_path, monkeypatch):
        from urllib.parse import unquote, urlsplit
        from urllib.request import url2pathname

        opened = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "webbrowser.open", lambda url, **options: opened.append((url, options))
        )
        script = tmp_path / "worker.py"
        script.write_text("value = sum(range(100))\n", encoding="utf-8")
        assert cli.main(["--backend", "trace", "--no-spark", str(script)]) == 0
        assert len(opened) == 1
        uri, options = opened[0]
        assert options == {"new": 2}
        destination = Path(url2pathname(unquote(urlsplit(uri).path)))

        try:
            assert destination.parent != tmp_path
            assert "worker.py" in destination.read_text(encoding="utf-8")
            assert not list(tmp_path.glob("*.html"))
        finally:
            destination.unlink()


class TestModuleExecution:
    """Check module execution.

    Run importable modules with Python's normal __main__ semantics.

    """

    @pytest.mark.parametrize("separator", [[], ["--"]])
    def test_module_argv_and_main_restored(self, tmp_path, monkeypatch, separator):
        package = tmp_path / "cli_module_package"
        package.mkdir()
        (package / "__init__.py").write_text("")
        module = package / "worker.py"
        module.write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path(sys.argv[1]).write_text(json.dumps({'argv': sys.argv, 'name': __name__, "
            "'package': __package__}))\n"
        )
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "module.json"
        previous_main = sys.modules["__main__"]
        try:
            assert (
                cli.main(
                    [
                        *cli_options(tmp_path / "profile.html"),
                        "-m",
                        "cli_module_package.worker",
                        *separator,
                        str(data),
                        "--flag",
                    ]
                )
                == 0
            )
        finally:
            sys.modules.pop("cli_module_package.worker", None)
            sys.modules.pop("cli_module_package", None)
        recorded = json.loads(data.read_text())
        assert Path(recorded["argv"][0]) == module
        assert recorded["argv"][1:] == [str(data), "--flag"]
        assert recorded["name"] == "__main__"
        assert recorded["package"] == "cli_module_package"
        assert sys.modules["__main__"] is previous_main

    def test_missing_module_still_finalizes_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        report = tmp_path / "profile.html"
        with pytest.raises(ImportError):
            cli.main([*cli_options(report), "-m", "linescope_module_that_does_not_exist"])
        assert report.is_file()
        assert "Failed" in report.read_text(encoding="utf-8")


class TestWorkloadFailures:
    """Check workload failures.

    Keep reports and original workload failures without leaking state.

    """

    @pytest.mark.parametrize("exception", ["ValueError('workload failed')", "KeyboardInterrupt()"])
    def test_original_exception_and_report(self, tmp_path, exception):
        script = tmp_path / "failure.py"
        script.write_text(f"raise {exception}\n")
        report = tmp_path / "profile.html"
        previous_argv, previous_path = sys.argv[:], sys.path[:]
        previous_trace = sys.gettrace()
        exception_type = ValueError if exception.startswith("ValueError") else KeyboardInterrupt
        with pytest.raises(exception_type):
            cli.main([*cli_options(report), str(script)])
        assert report.is_file()
        assert "Failed" in report.read_text(encoding="utf-8")
        assert sys.argv == previous_argv
        assert sys.path == previous_path
        assert sys.gettrace() is previous_trace

    def test_browser_failure_preserves_workload_exception(self, tmp_path, monkeypatch, capsys):
        def unavailable(*_args, **_kwargs):
            raise OSError("browser unavailable")

        script = tmp_path / "failure.py"
        script.write_text("raise ValueError('workload failed')\n", encoding="utf-8")
        monkeypatch.setattr("linescope.api.Session.show", unavailable)

        with pytest.raises(ValueError, match="workload failed"):
            cli.main(["--backend", "trace", "--no-spark", str(script)])

        assert "browser unavailable" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("code", "success"), [(0, True), (None, True), (7, False), ("failed", False)]
    )
    def test_system_exit_status_and_report(self, tmp_path, code, success):
        script = tmp_path / "exit.py"
        script.write_text(f"raise SystemExit({code!r})\n")
        report = tmp_path / "profile.html"
        with pytest.raises(SystemExit) as error:
            cli.main([*cli_options(report), str(script)])
        assert error.value.code == code
        assert ("Success" if success else "Failed") in report.read_text(encoding="utf-8")
