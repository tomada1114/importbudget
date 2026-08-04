"""Tests for the end-to-end profiling session (measure + attribute)."""

from __future__ import annotations

import pytest

from importbudget.attribute import ENTRYPOINT_KEY
from importbudget.entrypoints import Entrypoint, EntrypointKind, RunOptions
from importbudget.errors import EntrypointError, MeasurementError
from importbudget.index import SCRIPT_MODULE
from importbudget.profiler import profile


def fast(cwd):
    return RunOptions(runs=1, warmup=1, cwd=cwd)


class TestProfile:
    def test_module_entrypoint_is_attributed_to_source_lines(self, project_dir):
        result = profile("demopkg", fast(project_dir))

        top = result.attribution.rows[0]
        assert top.key == "demopkg/__init__.py:18"
        assert top.source == "from . import slow_a"
        assert result.source_root == project_dir

    def test_total_attribution_is_within_ten_percent_of_the_measurement(
        self, project_dir
    ):
        result = profile("demopkg", fast(project_dir))

        net = result.attribution.net_measured_us
        error = abs(result.attribution.attributed_us - net) / net

        assert error < 0.10

    def test_module_run_entrypoint_attributes_the_executed_module(self, project_dir):
        entrypoint = Entrypoint.parse("demopkg.cli", run_module=True)

        result = profile(entrypoint, fast(project_dir))

        keys = {row.key for row in result.attribution.rows}
        assert "demopkg/cli.py:3" in keys
        # runpy's own imports are invocation overhead, not the app's cost.
        assert "runpy" not in {
            module for row in result.attribution.rows for module in row.modules
        }

    def test_script_entrypoint_attributes_its_own_import_lines(self, project_dir):
        result = profile("run_demo.py", fast(project_dir))

        row = next(row for row in result.attribution.rows if row.key == "run_demo.py:3")

        assert row.owner == SCRIPT_MODULE
        assert ENTRYPOINT_KEY not in {row.key for row in result.attribution.rows}

    def test_top_returns_the_most_expensive_rows(self, project_dir):
        result = profile("demopkg", fast(project_dir))

        assert result.top(2) == result.attribution.rows[:2]
        assert result.top(0) == result.attribution.rows

    def test_module_run_without_a_runnable_module_still_reports(self, project_dir):
        # `python -m demopkg` fails (no __main__.py), but the imports that did
        # happen are still attributed instead of the run being thrown away.
        entrypoint = Entrypoint.parse("demopkg", run_module=True)

        result = profile(entrypoint, fast(project_dir))

        assert result.measurement.returncodes == (1,)
        assert any("non-zero status" in warning for warning in result.warnings)

    def test_missing_script_entrypoint_is_reported(self, tmp_path):
        entrypoint = Entrypoint("gone.py", EntrypointKind.SCRIPT)

        with pytest.raises(EntrypointError, match=r"Script entrypoint not found"):
            profile(entrypoint, fast(tmp_path))

    def test_unknown_module_is_reported_as_an_entrypoint_error(self, tmp_path):
        with pytest.raises(EntrypointError, match=r"Cannot locate the source"):
            profile("no_such_module_xyz", fast(tmp_path))

    def test_unusable_interpreter_is_a_measurement_error_not_an_oserror(
        self, project_dir
    ):
        # Source location runs before the first measured process, so this is
        # where a bad interpreter is met; it used to escape as FileNotFoundError.
        options = RunOptions(
            runs=1, warmup=0, cwd=project_dir, python="/nonexistent/python"
        )

        with pytest.raises(MeasurementError, match=r"/nonexistent/python"):
            profile("demopkg", options)

    def test_single_file_module_is_supported(self, tmp_path):
        (tmp_path / "lonely.py").write_text("import decimal\n", encoding="utf-8")

        result = profile("lonely", fast(tmp_path))

        keys = {row.key for row in result.attribution.rows}
        assert "lonely.py:1" in keys
