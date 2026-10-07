"""LineScope.

Author: Mavs
Description: Script and module CLI execution, configuration, and restoration
tests.

"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from click.testing import CliRunner
import pytest

from linescope import cli


@pytest.fixture
def runner():
    """Provide an isolated Click command runner for CLI assertions.

    Invoke the CLI through an isolated runner and inspect its arguments, saved
    report, or restored process state.

    """
    return CliRunner()


@pytest.fixture(autouse=True)
def suppress_browser_launch(monkeypatch):
    """Keep automatic report display inside the CLI test process.

    Exercise report generation without opening a real browser.

    """
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


def test_trace_gpu_option_warns_and_saves_python_report(runner, tmp_path):
    """Warn about unavailable GPU metrics while profiling the script.

    Exercise the CLI boundary and emit the diagnostic without advertising
    device columns or skipping the user's workload.

    """
    script = tmp_path / "workload.py"
    script.write_text('print("workload completed")\n', encoding="utf-8")
    report = tmp_path / "gpu.html"
    with pytest.warns(RuntimeWarning, match="trace.*GPU"):
        result = runner.invoke(cli.main, [*cli_options(report), "--gpu", str(script)])
    assert result.exit_code == 0, result.output
    assert "workload completed" in result.stdout
    html = report.read_text(encoding="utf-8")
    assert "GPU time" not in html
    assert "GPU peak memory" not in html


class TestCommandParsing:
    """Check command parsing.

    Test CLI discovery, errors, and argument boundaries.

    """

    @pytest.mark.parametrize(
        ("argument", "expected"),
        [("--help", "Profile your Python source"), ("--version", "LineScope, version 0.1.0")],
    )
    def test_help_and_version(self, runner, argument, expected):
        """Verify help and version.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        result = runner.invoke(cli.main, [argument])
        assert result.exit_code == 0
        assert expected in result.stdout

    def test_help_lists_options_without_api_docstring(self, runner):
        """Verify help lists options without api docstring.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        result = runner.invoke(cli.main, ["--help"])
        assert result.exit_code == 0
        assert "--memory / --no-memory" in result.stdout
        assert "-b, --backend" in result.stdout
        assert "-i, --include" in result.stdout
        assert "-e, --exclude" in result.stdout
        assert "-r, --root" in result.stdout
        assert "-d, --display" in result.stdout
        assert "--display [none|end]" in result.stdout
        assert "Parameters" not in result.stdout
        assert "default=None" not in result.stdout

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ([], "provide a script or -m MODULE"),
            (["--not-a-real-option"], "No such option"),
            (["--display", "invalid", "main.py"], "Invalid value for '--display'"),
            (["--backend"], "requires an argument"),
            (["-m"], "-m requires a module name"),
        ],
    )
    def test_invalid_arguments(self, runner, arguments, message):
        """Verify invalid arguments.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        result = runner.invoke(cli.main, arguments)
        assert result.exit_code == 2
        assert message in result.stderr

    def test_missing_script(self, runner, tmp_path):
        """Verify missing script.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        result = runner.invoke(cli.main, [str(tmp_path / "missing.py")])
        assert result.exit_code == 2
        assert "script does not exist" in result.stderr

    def test_target_directory_rejected(self, runner, tmp_path):
        """Verify target directory rejected.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        result = runner.invoke(cli.main, [str(tmp_path)])
        assert result.exit_code == 2
        assert "script does not exist" in result.stderr

    def test_repeated_include_exclude_options(self):
        """Verify repeated include exclude options.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        with cli.main.make_context(
            "linescope",
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
            ],
        ) as ctx:
            assert ctx.params["include"] == ("pkg", "other")
            assert ctx.params["exclude"] == ("tests", "**/generated.py")
            assert ctx.params["target"] == ("script.py", "--memory")
            assert ctx.params["memory"] is None

    @pytest.mark.parametrize("status", [0, 7])
    def test_memory_preserves_arguments_and_exit_status(self, runner, tmp_path, status):
        """Verify memory profiling preserves arguments and exit status.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "worker.py"
        recorded = tmp_path / "arguments.json"
        script.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))\n"
            f"raise SystemExit({status})\n",
            encoding="utf-8",
        )
        arguments = [
            "--memory",
            "--display",
            "none",
            "--no-spark",
            "--no-notebooks",
            str(script),
            "--help",
            "two words",
        ]
        result = runner.invoke(cli.main, arguments)
        assert result.exit_code == status
        assert json.loads(recorded.read_text()) == ["--help", "two words"]

    @pytest.mark.parametrize("option", ["memory", "gpu", "spark", "notebooks"])
    @pytest.mark.parametrize("flag", [None, True, False])
    def test_boolean_options_follow_configuration_unless_explicit(
        self, runner, tmp_path, monkeypatch, option, flag
    ):
        """Verify boolean options follow configuration unless explicit.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        (tmp_path / "pyproject.toml").write_text(
            f'[tool.linescope]\nbackend="trace"\n{option}=true\n'
        )
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        captured = {}

        def capture_session(**options):
            """Provide the controlled behavior used by this test.

            Capture effective session options without starting integrations.

            """
            captured.update(vars(cli.resolve_config(**options)))
            raise SystemExit(0)

        monkeypatch.setattr(cli, "Session", capture_session)
        arguments = [] if flag is None else [f"--{'' if flag else 'no-'}{option}"]
        result = runner.invoke(cli.main, [*arguments, str(script)])
        assert result.exit_code == 0, result.output
        assert captured[option] is (True if flag is None else flag)


class TestScriptExecution:
    """Check script execution.

    Run actual scripts through the CLI without opening a browser.

    """

    def test_script_argv_unicode_paths_and_process_restoration(self, runner, tmp_path):
        """Verify script argv unicode paths and process restoration.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
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
        result = runner.invoke(
            cli.main,
            [*cli_options(report), str(script), str(data), "--flag", "two words", "🍋"],
        )
        assert result.exit_code == 0, result.output
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
        assert "LineScope report:" in result.stderr

    def test_explicit_argument_separator(self, runner, tmp_path):
        """Verify explicit argument separator.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        assert runner.invoke(cli.main, [*cli_options(report), "--", str(script)]).exit_code == 0
        assert "value = " in report.read_text(encoding="utf-8")

    @pytest.mark.parametrize("module", [False, True])
    def test_target_options_are_forwarded_verbatim(self, runner, tmp_path, monkeypatch, module):
        """Verify target options are forwarded verbatim.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "argument_workload.py"
        script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        flags = [
            "--help",
            "--version",
            "--backend",
            "custom",
            "--display=none",
            "-b",
            "target-backend",
            "-i",
            "target-include",
            "-e",
            "target-exclude",
            "-d",
            "target-display",
            "-r",
            "target-root",
            "--",
            "-m",
        ]
        target = ["-m", script.stem] if module else [str(script)]

        try:
            result = runner.invoke(
                cli.main, [*cli_options(tmp_path / "profile.html"), *target, *flags]
            )
        finally:
            sys.modules.pop(script.stem, None)

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == flags

    def test_reads_default_argv(self, tmp_path, monkeypatch):
        """Verify reads default argv.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        monkeypatch.setattr(sys, "argv", ["linescope", *cli_options(report), str(script)])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 0
        assert report.is_file()

    def test_nearest_project_settings(self, runner, tmp_path):
        """Verify nearest project settings.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        (tmp_path / "pyproject.toml").write_text(
            '[tool.linescope]\nbackend="trace"\nnotebooks=false\nspark=false\nexclude=["ignored.py"]\n'
        )
        subdirectory = tmp_path / "src"
        subdirectory.mkdir()
        script = subdirectory / "worker.py"
        script.write_text("PROJECT_CONFIG_WORKLOAD = 42\n")
        report = tmp_path / "profile.html"
        assert runner.invoke(cli.main, ["-o", str(report), str(script)]).exit_code == 0
        text = report.read_text(encoding="utf-8")
        assert "PROJECT_CONFIG_WORKLOAD" in text
        assert "trace" in text

    @pytest.mark.parametrize(
        ("backend_option", "include_option", "exclude_option", "display_option", "root_option"),
        [
            ("--backend", "--include", "--exclude", "--display", "--root"),
            ("-b", "-i", "-e", "-d", "-r"),
        ],
    )
    def test_actual_include_and_exclude_filtering(
        self,
        runner,
        tmp_path,
        backend_option,
        include_option,
        exclude_option,
        display_option,
        root_option,
    ):
        """Verify actual include and exclude filtering.

        Run a script using long or short options and inspect its saved report.
        Allow both forms in repeated filters while preserving source scope.

        """
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
                runner.invoke(
                    cli.main,
                    [
                        backend_option,
                        "trace",
                        "--no-spark",
                        "--no-notebooks",
                        display_option,
                        "none",
                        root_option,
                        str(tmp_path),
                        "-o",
                        str(report),
                        include_option,
                        "cli_filter_package",
                        "--include",
                        "worker.py",
                        exclude_option,
                        "cli_filter_package/omit.py",
                        str(script),
                    ],
                ).exit_code
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

    def test_output_destination_from_project_configuration(self, runner, tmp_path, monkeypatch):
        """Verify output destination from project configuration.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.linescope]\nbackend="trace"\nnotebooks=false\nspark=false\noutput="custom.html"\n'
        )
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        assert runner.invoke(cli.main, [str(script)]).exit_code == 0
        assert (tmp_path / "custom.html").is_file()
        assert not (tmp_path / "linescope.html").exists()

    def test_explicit_display_opens_one_saved_report(self, runner, tmp_path, monkeypatch):
        """Verify explicit display opens one saved report.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_kwargs: opened.append(url))
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        report = tmp_path / "profile.html"
        assert (
            runner.invoke(
                cli.main, [*cli_options(report), "--display", "end", str(script)]
            ).exit_code
            == 0
        )
        assert opened == [report.as_uri()]

    def test_explicit_headless_display(self, runner, tmp_path, monkeypatch):
        """Verify explicit headless display.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """

        def forbidden(*args, **kwargs):
            """Reject an unexpected browser display during headless execution.

            Fail immediately when code invokes an operation the case expects to
            avoid.

            """
            del args, kwargs
            pytest.fail("Headless CLI unexpectedly opened a browser")

        monkeypatch.setattr("webbrowser.open", forbidden)
        script = tmp_path / "worker.py"
        script.write_text("value = 42\n")
        assert (
            runner.invoke(
                cli.main, [*cli_options(tmp_path / "profile.html"), str(script)]
            ).exit_code
            == 0
        )

    def test_default_display_opens_temporary_report_in_new_tab(
        self, runner, tmp_path, monkeypatch
    ):
        """Verify default display opens temporary report in new tab.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        from urllib.parse import unquote, urlsplit
        from urllib.request import url2pathname

        opened = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "webbrowser.open", lambda url, **options: opened.append((url, options))
        )
        script = tmp_path / "worker.py"
        script.write_text("value = sum(range(100))\n", encoding="utf-8")
        assert (
            runner.invoke(cli.main, ["--backend", "trace", "--no-spark", str(script)]).exit_code
            == 0
        )

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
    def test_module_argv_and_main_restored(self, runner, tmp_path, monkeypatch, separator):
        """Verify module argv and main restored.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
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
                runner.invoke(
                    cli.main,
                    [
                        *cli_options(tmp_path / "profile.html"),
                        "-m",
                        "cli_module_package.worker",
                        *separator,
                        str(data),
                        "--flag",
                    ],
                ).exit_code
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

    def test_missing_module_still_finalizes_report(self, runner, tmp_path, monkeypatch):
        """Verify missing module still finalizes report.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        monkeypatch.chdir(tmp_path)
        report = tmp_path / "profile.html"
        result = runner.invoke(
            cli.main, [*cli_options(report), "-m", "linescope_module_that_does_not_exist"]
        )
        assert result.exit_code == 1
        assert isinstance(result.exception, ImportError)
        assert report.is_file()
        assert "Failed" in report.read_text(encoding="utf-8")


class TestWorkloadFailures:
    """Check workload failures.

    Keep reports and original workload failures without leaking state.

    """

    @pytest.mark.parametrize("exception", ["ValueError('workload failed')", "KeyboardInterrupt()"])
    def test_original_exception_and_report(self, runner, tmp_path, exception):
        """Verify original exception and report.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "failure.py"
        script.write_text(f"raise {exception}\n")
        report = tmp_path / "profile.html"
        previous_argv, previous_path = sys.argv[:], sys.path[:]
        previous_trace = sys.gettrace()
        result = runner.invoke(cli.main, [*cli_options(report), str(script)])
        assert result.exit_code == 1

        if exception.startswith("ValueError"):
            assert isinstance(result.exception, ValueError)
            assert str(result.exception) == "workload failed"
        else:
            assert "Aborted!" in result.stderr

        assert report.is_file()
        assert "Failed" in report.read_text(encoding="utf-8")
        assert sys.argv == previous_argv
        assert sys.path == previous_path
        assert sys.gettrace() is previous_trace

    def test_browser_failure_preserves_workload_exception(self, runner, tmp_path, monkeypatch):
        """Verify browser failure preserves workload exception.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """

        def unavailable(*_args, **_kwargs):
            """Simulate a browser failure without replacing the workload error.

            Keep the display error separate from the original workload
            exception.

            """
            raise OSError("browser unavailable")

        script = tmp_path / "failure.py"
        script.write_text("raise ValueError('workload failed')\n", encoding="utf-8")
        monkeypatch.setattr("linescope.api.Session.show", unavailable)

        result = runner.invoke(cli.main, ["--backend", "trace", "--no-spark", str(script)])
        assert result.exit_code == 1
        assert isinstance(result.exception, ValueError)
        assert str(result.exception) == "workload failed"
        assert "browser unavailable" in result.stderr

    @pytest.mark.parametrize(
        ("code", "success"), [(0, True), (None, True), (7, False), ("failed", False)]
    )
    def test_system_exit_status_and_report(self, runner, tmp_path, code, success):
        """Verify system exit status and report.

        Invoke the CLI through an isolated runner and inspect its arguments,
        saved report, or restored process state.

        """
        script = tmp_path / "exit.py"
        script.write_text(f"raise SystemExit({code!r})\n")
        report = tmp_path / "profile.html"
        result = runner.invoke(cli.main, [*cli_options(report), str(script)])
        assert result.exit_code == (0 if success else code if isinstance(code, int) else 1)

        if result.exception is not None:
            assert isinstance(result.exception, SystemExit)
            assert result.exception.code == code

        assert ("Success" if success else "Failed") in report.read_text(encoding="utf-8")
