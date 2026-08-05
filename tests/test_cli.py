"""Tests for the ``importbudget`` command line interface."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys

import pytest

from importbudget.applies import NATIVE_TARGET_VERSION
from importbudget.budgets import Budget
from importbudget.cli import DEFAULT_TOP, build_parser, main
from importbudget.entrypoints import DEFAULT_RUNS, DEFAULT_WARMUP_RUNS
from importbudget.verifies import (
    DEFAULT_DIVERGENCE_THRESHOLD,
    DEFAULT_VERIFY_RUNS,
    DEFAULT_VERIFY_WARMUP,
)


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

    def test_plan_defaults_match_the_profile_command(self):
        args = build_parser().parse_args(["plan", "demopkg"])

        assert args.command == "plan"
        assert args.entrypoint == "demopkg"
        assert args.runs == DEFAULT_RUNS
        assert args.warmup == DEFAULT_WARMUP_RUNS
        assert args.top == DEFAULT_TOP
        assert args.min_ms == 0.0
        assert args.from_profile is None

    def test_plan_accepts_a_saved_profile_instead_of_an_entrypoint(self):
        args = build_parser().parse_args(
            ["plan", "--from-profile", "p.json", "--min-ms", "2.5"]
        )

        assert args.entrypoint is None
        assert args.from_profile == "p.json"
        assert args.min_ms == 2.5

    def test_apply_defaults_to_a_native_dry_run(self):
        args = build_parser().parse_args(["apply", "plan.json"])

        assert args.command == "apply"
        assert args.plan == "plan.json"
        assert args.write is False
        assert args.target_version == NATIVE_TARGET_VERSION
        assert args.json is False

    def test_apply_flags_are_parsed(self):
        args = build_parser().parse_args(
            ["apply", "plan.json", "--write", "--target-version", "3.13", "--json"]
        )

        assert (args.write, args.target_version, args.json) == (True, "3.13", True)

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


class TestPlanCommand:
    def test_the_table_separates_proposed_from_excluded(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)

        code = main(["plan", "demopkg", "--runs", "1", "--warmup", "0", "--top", "0"])

        out = capsys.readouterr().out
        assert code == 0
        assert "proposed - proved safe to make lazy" in out
        assert "excluded - not proven safe" in out
        assert "demopkg/util.py:3" in out
        assert "MODULE_LEVEL_USE" in out

    def test_json_output_is_a_plan_document(self, project_dir, monkeypatch, capsys):
        monkeypatch.chdir(project_dir)

        code = main(
            ["plan", "demopkg", "--runs", "1", "--warmup", "0", "--json"],
        )

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["document"] == "plan"
        assert document["schema_version"] == 1
        assert document["totals"]["candidate_count"] > 0

    def test_from_profile_round_trips_a_saved_profile(
        self, project_dir, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)
        main(["profile", "demopkg", "--runs", "1", "--warmup", "0", "--json"])
        saved = tmp_path / "profile.json"
        saved.write_text(capsys.readouterr().out, encoding="utf-8")

        code = main(["plan", "--from-profile", str(saved), "--json"])

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["profile"]["origin"] == saved.as_posix()
        assert any(s["verdict"] == "safe" for s in document["statements"])

    def test_min_ms_moves_cheap_statements_out_of_the_proposal(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)

        main(
            [
                "plan",
                "demopkg",
                "--runs",
                "1",
                "--warmup",
                "0",
                "--min-ms",
                "1000",
                "--json",
            ]
        )

        document = json.loads(capsys.readouterr().out)
        assert document["totals"]["predicted_saving_us"] == 0
        assert document["totals"]["below_threshold_count"] > 0

    def test_neither_input_form_is_an_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        code = main(["plan"])

        assert code == 1
        assert "needs an entrypoint, or --from-profile" in capsys.readouterr().err

    def test_both_input_forms_at_once_is_an_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        code = main(["plan", "demopkg", "--from-profile", "p.json"])

        assert code == 1
        assert "not both" in capsys.readouterr().err

    def test_an_unreadable_profile_document_exits_with_a_message(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)

        code = main(["plan", "--from-profile", str(tmp_path / "nope.json")])

        assert code == 1
        assert "could not read profile document" in capsys.readouterr().err


class TestApplyCommand:
    def test_a_dry_run_shows_the_diff_and_writes_nothing(
        self, make_plan, tmp_path, capsys
    ):
        plan = make_plan("import numpy\n")

        code = main(["apply", str(plan)])

        out = capsys.readouterr().out
        assert code == 0
        assert "+lazy import numpy" in out
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "import numpy\n"

    def test_write_converts_the_file_and_urges_a_test_run(
        self, make_plan, tmp_path, capsys
    ):
        plan = make_plan("import numpy\n")

        code = main(["apply", str(plan), "--write"])

        out = capsys.readouterr().out
        assert code == 0
        assert "run your project's test suite" in out
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy\n"
        )

    def test_json_output_is_an_apply_document(self, make_plan, capsys):
        plan = make_plan("import numpy\n")

        code = main(["apply", str(plan), "--json"])

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["document"] == "apply"
        assert document["totals"]["converted_count"] == 1

    def test_the_target_version_selects_the_fallback_emitter(
        self, make_plan, tmp_path, capsys
    ):
        plan = make_plan("import numpy\n")

        code = main(["apply", str(plan), "--target-version", "3.13", "--write"])

        capsys.readouterr()
        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")
        assert code == 0
        assert "lazy import" not in converted
        assert "importlib.util.LazyLoader" in converted

    def test_an_unsupported_target_version_is_rejected_by_the_parser(self):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(["apply", "plan.json", "--target-version", "3.9"])

        assert exit_info.value.code == 2

    def test_an_unreadable_plan_document_exits_with_a_message(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)

        code = main(["apply", str(tmp_path / "nope.json")])

        assert code == 1
        assert "could not read plan document" in capsys.readouterr().err

    def test_a_real_plan_feeds_a_real_apply(
        self, project_dir, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)
        main(["plan", "demopkg", "--runs", "1", "--warmup", "0", "--json"])
        saved = tmp_path / "plan.json"
        saved.write_text(capsys.readouterr().out, encoding="utf-8")

        code = main(["apply", str(saved), "--json"])

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["totals"]["converted_count"] > 0
        assert all(
            statement["after"].startswith("lazy ")
            for statement in document["statements"]
            if statement["status"] == "converted"
        )


class TestModuleExecution:
    def test_python_dash_m_importbudget_runs_the_cli(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["importbudget", "--version"])

        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("importbudget", run_name="__main__")

        assert exit_info.value.code == 0

    def test_importing_the_entry_module_does_not_exit_the_interpreter(self):
        completed = subprocess.run(
            [sys.executable, "-c", "import importbudget.__main__; print('ok')"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0
        assert completed.stdout.strip() == "ok"


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


class TestVerifyAndCheckParser:
    def test_verify_defaults_ask_for_more_pairs_than_profile_asks_for_runs(self):
        args = build_parser().parse_args(["verify", "plan.json"])

        assert args.command == "verify"
        assert args.plan == "plan.json"
        assert args.runs == DEFAULT_VERIFY_RUNS
        assert args.warmup == DEFAULT_VERIFY_WARMUP
        assert args.divergence_threshold == DEFAULT_DIVERGENCE_THRESHOLD
        assert args.target_version == NATIVE_TARGET_VERSION
        assert args.json is False

    def test_verify_flags_are_parsed(self):
        args = build_parser().parse_args(
            [
                "verify",
                "plan.json",
                "--runs",
                "9",
                "--warmup",
                "2",
                "--divergence-threshold",
                "0.5",
                "--target-version",
                "3.13",
                "--json",
            ]
        )

        assert (args.runs, args.warmup, args.divergence_threshold) == (9, 2, 0.5)
        assert (args.target_version, args.json) == ("3.13", True)

    def test_check_defaults_match_the_profile_command(self):
        args = build_parser().parse_args(["check", "demopkg", "--max", "150ms"])

        assert args.command == "check"
        assert args.entrypoint == "demopkg"
        assert args.max == Budget.parse("150ms")
        assert args.runs == DEFAULT_RUNS
        assert args.warmup == DEFAULT_WARMUP_RUNS
        assert args.module is False
        assert args.json is False

    @pytest.mark.parametrize(
        "text",
        [pytest.param("150ms", id="milliseconds"), pytest.param("0.15s", id="seconds")],
    )
    def test_both_documented_budget_forms_reach_the_same_value(self, text):
        args = build_parser().parse_args(["check", "demopkg", "--max", text])

        assert args.max.us == 150_000

    def test_a_budget_is_required(self):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(["check", "demopkg"])

        assert exit_info.value.code == 2

    def test_an_unparsable_budget_exits_naming_the_value(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(["check", "demopkg", "--max", "notaduration"])

        assert exit_info.value.code != 0
        assert "notaduration" in capsys.readouterr().err


class TestCheckCommand:
    def test_a_generous_budget_exits_zero(self, project_dir, monkeypatch, capsys):
        monkeypatch.chdir(project_dir)

        code = main(
            ["check", "demopkg", "--max", "30s", "--runs", "1", "--warmup", "0"]
        )

        assert code == 0
        assert "within budget" in capsys.readouterr().out

    def test_a_tiny_budget_exits_one(self, project_dir, monkeypatch, capsys):
        monkeypatch.chdir(project_dir)

        code = main(
            ["check", "demopkg", "--max", "1us", "--runs", "1", "--warmup", "0"]
        )

        assert code == 1
        assert "OVER BUDGET" in capsys.readouterr().out

    def test_an_unmeasurable_entrypoint_exits_two(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        code = main(
            [
                "check",
                "no_such_module_xyz",
                "--max",
                "30s",
                "--runs",
                "1",
                "--warmup",
                "0",
            ]
        )

        out = capsys.readouterr().out
        assert code == 2
        assert "could not measure" in out
        assert "OVER BUDGET" not in out

    def test_json_output_carries_the_verdict_and_the_exit_code(
        self, project_dir, monkeypatch, capsys
    ):
        monkeypatch.chdir(project_dir)

        code = main(
            [
                "check",
                "demopkg",
                "--max",
                "30s",
                "--runs",
                "1",
                "--warmup",
                "0",
                "--json",
            ]
        )

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["document"] == "check"
        assert document["outcome"] == "within"
        assert document["exit_code"] == 0


class TestVerifyCommand:
    def test_the_table_reports_the_interleaved_schedule(
        self, make_plan, monkeypatch, tmp_path, capsys
    ):
        plan_path = make_plan("import decimal\n\nVALUE = 1\n")
        monkeypatch.chdir(tmp_path)

        code = main(
            [
                "verify",
                str(plan_path),
                "--runs",
                "2",
                "--warmup",
                "0",
                "--target-version",
                "3.12",
            ]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "schedule: before after before after" in out

    def test_json_output_is_machine_readable(
        self, make_plan, monkeypatch, tmp_path, capsys
    ):
        plan_path = make_plan("import decimal\n\nVALUE = 1\n")
        monkeypatch.chdir(tmp_path)

        code = main(
            [
                "verify",
                str(plan_path),
                "--runs",
                "2",
                "--warmup",
                "0",
                "--target-version",
                "3.12",
                "--json",
            ]
        )

        document = json.loads(capsys.readouterr().out)
        assert code == 0
        assert document["document"] == "verify"
        assert document["measurement"]["schedule"] == [
            "before",
            "after",
            "before",
            "after",
        ]

    def test_a_plan_with_nothing_to_convert_exits_with_an_error_message(
        self, make_plan, capsys
    ):
        plan_path = make_plan("import decimal\n", excluded=["import decimal"])

        code = main(["verify", str(plan_path)])

        assert code == 1
        assert "importbudget:" in capsys.readouterr().err
