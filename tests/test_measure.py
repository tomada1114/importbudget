"""Tests for entrypoint handling and the measurement runner.

The tests in this module spawn real interpreters, so they keep run counts low.
"""

from __future__ import annotations

import os
import sys

import pytest

from importbudget.errors import EntrypointError, MeasurementError
from importbudget.measure import (
    Entrypoint,
    EntrypointKind,
    RunOptions,
    build_child_env,
    measure,
)
from importbudget.stderr import MAX_STDERR_LINES


class TestEntrypointParsing:
    def test_module_name_is_imported_with_dash_c(self):
        entrypoint = Entrypoint.parse("demopkg")

        assert entrypoint.kind is EntrypointKind.MODULE
        assert entrypoint.command_args == ["-c", "import demopkg"]
        assert entrypoint.top_level_package == "demopkg"

    def test_run_module_flag_selects_dash_m(self):
        entrypoint = Entrypoint.parse("demopkg.cli", run_module=True)

        assert entrypoint.kind is EntrypointKind.MODULE_RUN
        assert entrypoint.command_args == ["-m", "demopkg.cli"]
        assert entrypoint.top_level_package == "demopkg"

    def test_existing_file_is_detected_as_a_script(self, project_dir):
        entrypoint = Entrypoint.parse("run_demo.py", cwd=project_dir)

        assert entrypoint.kind is EntrypointKind.SCRIPT
        assert entrypoint.command_args == ["run_demo.py"]
        assert entrypoint.top_level_package is None

    def test_missing_script_is_rejected(self, tmp_path):
        with pytest.raises(EntrypointError, match=r"Script entrypoint not found"):
            Entrypoint.parse("missing.py", cwd=tmp_path)

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("not a module", id="spaces"),
            pytest.param("2bad", id="leading-digit"),
            pytest.param("", id="empty"),
            pytest.param("pkg/sub", id="path-separator"),
        ],
    )
    def test_invalid_module_names_are_rejected(self, target, tmp_path):
        with pytest.raises(EntrypointError, match=r"not an importable module name"):
            Entrypoint.parse(target, cwd=tmp_path)


class TestRunOptions:
    def test_defaults_discard_one_cold_run(self):
        options = RunOptions()

        assert options.warmup == 1
        assert options.runs == 3

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            pytest.param({"runs": 0}, r"runs must be >= 1", id="zero-runs"),
            pytest.param({"warmup": -1}, r"warmup must be >= 0", id="negative-warmup"),
        ],
    )
    def test_impossible_run_counts_are_rejected(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            RunOptions(**kwargs)


class TestChildEnvironment:
    def test_working_directory_comes_first_on_the_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/somewhere/else")

        env = build_child_env(tmp_path)

        assert env["PYTHONPATH"] == f"{tmp_path}{os.pathsep}/somewhere/else"

    def test_absent_pythonpath_is_created(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PYTHONPATH", raising=False)

        assert build_child_env(tmp_path)["PYTHONPATH"] == str(tmp_path)


class TestMeasure:
    def test_module_entrypoint_is_measured_and_averaged(self, project_dir):
        measurement = measure(
            Entrypoint.parse("demopkg"),
            RunOptions(runs=2, warmup=1, cwd=project_dir),
        )

        assert measurement.returncodes == (0, 0)
        assert measurement.runs == 2
        assert measurement.warmup_runs == 1
        assert measurement.python_version.startswith("3.")
        assert measurement.tree.names() >= {"demopkg", "demopkg.slow_a"}
        assert measurement.measured_us > 0

    def test_bootstrap_baseline_comes_from_the_same_interpreter(self, project_dir):
        measurement = measure(
            Entrypoint.parse("demopkg"), RunOptions(runs=1, warmup=0, cwd=project_dir)
        )

        assert "encodings" in measurement.baseline_modules
        assert "demopkg" not in measurement.baseline_modules

    def test_module_run_baseline_absorbs_the_runpy_overhead(self, project_dir):
        measurement = measure(
            Entrypoint.parse("demopkg.cli", run_module=True),
            RunOptions(runs=1, warmup=0, cwd=project_dir),
        )

        assert "runpy" in measurement.baseline_modules
        assert "demopkg" not in measurement.baseline_modules
        assert "demopkg.slow_a" in measurement.tree.names()

    def test_script_entrypoint_runs(self, project_dir):
        measurement = measure(
            Entrypoint.parse("run_demo.py", cwd=project_dir),
            RunOptions(runs=1, warmup=0, cwd=project_dir),
        )

        assert measurement.entrypoint.kind is EntrypointKind.SCRIPT
        assert "demopkg" in measurement.tree.names()

    def test_averaging_two_runs_keeps_the_totals_consistent(self, project_dir):
        measurement = measure(
            Entrypoint.parse("demopkg"),
            RunOptions(runs=3, warmup=0, cwd=project_dir),
        )

        tree = measurement.tree
        error = abs(tree.total_self_us - tree.total_root_cumulative_us)

        assert error / tree.total_root_cumulative_us < 0.01

    def test_failing_entrypoint_is_reported_not_hidden(self, tmp_path):
        measurement = measure(
            Entrypoint.parse("no_such_module_xyz"),
            RunOptions(runs=1, warmup=0, cwd=tmp_path),
        )

        assert measurement.returncodes == (1,)
        assert any("non-zero status" in warning for warning in measurement.warnings)

    def test_entrypoint_traceback_lands_in_stderr_not_warnings(self, tmp_path):
        measurement = measure(
            Entrypoint.parse("no_such_module_xyz"),
            RunOptions(runs=1, warmup=0, cwd=tmp_path),
        )

        assert any("ModuleNotFoundError" in line for line in measurement.stderr.lines)
        assert not any(
            "ModuleNotFoundError" in warning for warning in measurement.warnings
        )

    def test_chatty_entrypoint_does_not_flood_the_measurement(self, tmp_path):
        script = tmp_path / "chatty.py"
        script.write_text(
            "import sys\n"
            "for index in range(60):\n"
            "    print(f'log line {index}', file=sys.stderr)\n",
            encoding="utf-8",
        )

        measurement = measure(
            Entrypoint.parse("chatty.py", cwd=tmp_path),
            RunOptions(runs=1, warmup=0, cwd=tmp_path),
        )

        assert len(measurement.stderr.lines) == MAX_STDERR_LINES
        assert measurement.stderr.suppressed == 60 - MAX_STDERR_LINES
        assert measurement.warnings == ()

    def test_averaged_runs_keep_the_totals_exact_not_merely_close(self, project_dir):
        # Rounding each per-module mean on its own used to leave a residual
        # that pushed the attributed total above the measured total.
        measurement = measure(
            Entrypoint.parse("demopkg"),
            RunOptions(runs=3, warmup=0, cwd=project_dir),
        )

        tree = measurement.tree

        assert tree.total_self_us == tree.total_root_cumulative_us

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX shell stub for a failing interpreter"
    )
    def test_interpreter_that_cannot_report_a_baseline_raises(self, tmp_path):
        stub = tmp_path / "fake-python"
        stub.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        stub.chmod(0o755)

        with pytest.raises(MeasurementError, match=r"baseline"):
            measure(
                Entrypoint.parse("demopkg"),
                RunOptions(runs=1, warmup=0, cwd=tmp_path, python=str(stub)),
            )

    def test_unusable_interpreter_raises(self, tmp_path):
        with pytest.raises(MeasurementError, match=r"Could not run the interpreter"):
            measure(
                Entrypoint.parse("demopkg"),
                RunOptions(runs=1, warmup=0, cwd=tmp_path, python=sys.executable + "x"),
            )
