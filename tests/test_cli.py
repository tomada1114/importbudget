"""Tests for the ``importbudget`` command line interface."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys

import pytest

from importbudget.cli import DEFAULT_TOP, build_parser, main
from importbudget.entrypoints import DEFAULT_RUNS, DEFAULT_WARMUP_RUNS


class TestParser:
    def test_profile_defaults_match_the_documented_measurement_policy(self):
        args = build_parser().parse_args(["profile", "demopkg"])

        assert args.command == "profile"
        assert args.entrypoint == "demopkg"
        assert args.runs == DEFAULT_RUNS
        assert args.warmup == DEFAULT_WARMUP_RUNS
        assert args.top == DEFAULT_TOP
        assert args.module is False
        assert args.json is False

    def test_flags_are_parsed(self):
        args = build_parser().parse_args(
            ["profile", "demopkg.cli", "-m", "--runs", "5", "--top", "3", "--json"]
        )

        assert (args.module, args.runs, args.top, args.json) == (True, 5, 3, True)

    def test_a_subcommand_is_required(self):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args([])

        assert exit_info.value.code == 2

    def test_version_is_reported(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(["--version"])

        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip()


class TestMain:
    def test_table_output_lists_the_costly_statements(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)

        code = main(["profile", "demopkg", "--runs", "1", "--warmup", "0"])

        out = capsys.readouterr().out
        assert code == 0
        assert "demopkg/__init__.py:18" in out
        assert "from . import slow_a" in out

    def test_json_output_is_machine_readable(self, project_dir, monkeypatch, capsys):
        monkeypatch.chdir(project_dir)

        code = main(["profile", "demopkg", "--runs", "1", "--warmup", "0", "--json"])

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["schema_version"] == 1

    def test_unknown_entrypoint_exits_with_an_error_message(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)

        code = main(["profile", "no_such_module_xyz", "--runs", "1", "--warmup", "0"])

        assert code == 1
        assert "importbudget:" in capsys.readouterr().err

    def test_invalid_run_count_exits_with_an_error_message(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)

        code = main(["profile", "demopkg", "--runs", "0"])

        assert code == 1
        assert "runs must be >= 1" in capsys.readouterr().err

    def test_unusable_interpreter_prints_an_error_not_a_traceback(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)
        # The CLI always profiles with sys.executable; make that unusable.
        monkeypatch.setattr(sys, "executable", "/nonexistent/python")

        exit_code = main(["profile", "demopkg", "--runs", "1", "--warmup", "0"])

        captured = capsys.readouterr()
        assert exit_code == 1
        assert captured.err.startswith("importbudget: ")
        assert "Traceback" not in captured.err

    def test_json_separates_program_stderr_from_warnings(
        self, tmp_path, monkeypatch, capsys
    ):
        (tmp_path / "chatty.py").write_text(
            "import sys\nimport decimal\nprint('hello from the app', file=sys.stderr)\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        main(["profile", "chatty.py", "--runs", "1", "--warmup", "0", "--json"])

        document = json.loads(capsys.readouterr().out)
        assert "hello from the app" in document["stderr"]["lines"]
        assert document["stderr"]["suppressed"] == 0
        assert not any("hello from the app" in w for w in document["warnings"])


class TestModuleExecution:
    def test_python_dash_m_importbudget_runs_the_cli(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["importbudget", "--version"])

        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("importbudget", run_name="__main__")

        assert exit_info.value.code == 0


class TestEndToEnd:
    def test_json_output_satisfies_the_ten_percent_criterion(self, project_dir):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "importbudget",
                "profile",
                "demopkg",
                "--runs",
                "2",
                "--warmup",
                "1",
                "--json",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        document = json.loads(completed.stdout)
        measurement = document["measurement"]
        net = measurement["measured_us"] - measurement["filtered_baseline_us"]
        error = abs(measurement["attributed_us"] - net) / net

        assert error < 0.10
        assert document["statements"][0]["key"] == "demopkg/__init__.py:18"
        assert document["statements"][0]["self_ms"] > 25
        assert measurement["returncodes"] == [0, 0]

    def test_script_entrypoint_runs_through_the_console_script(self, project_dir):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "importbudget",
                "profile",
                "run_demo.py",
                "--runs",
                "1",
                "--warmup",
                "0",
                "--top",
                "0",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        assert "run_demo.py:3" in completed.stdout
