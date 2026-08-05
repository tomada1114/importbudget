"""Tests for the budget gate and the durations it is given."""

from __future__ import annotations

import textwrap

import pytest

from importbudget.budgets import Budget, CheckOptions, CheckOutcome, CheckResult
from importbudget.check import check
from importbudget.entrypoints import Entrypoint, RunOptions

BUDGET_US = 150_000


def make_result(cost_us: int | None, *, failure: str | None = None) -> CheckResult:
    """Build a verdict without measuring anything."""
    return CheckResult(
        target="demopkg",
        kind="module",
        budget=Budget(BUDGET_US),
        cost_us=cost_us,
        failure=failure,
    )


class TestBudgetParse:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("150ms", id="milliseconds"),
            pytest.param("0.15s", id="fractional-seconds"),
            pytest.param("150000us", id="microseconds"),
            pytest.param("  150 MS  ", id="spaced-and-uppercase"),
        ],
    )
    def test_every_accepted_form_parses_to_the_same_budget(self, text):
        assert Budget.parse(text) == Budget(BUDGET_US)

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("150", id="no-unit"),
            pytest.param("notaduration", id="not-a-number"),
            pytest.param("", id="empty"),
            pytest.param("-5ms", id="negative"),
            pytest.param("150 minutes", id="unknown-unit"),
            pytest.param("1.5.2s", id="malformed-number"),
        ],
    )
    def test_an_unparsable_value_raises_naming_the_value(self, text):
        with pytest.raises(ValueError, match=r"cannot read .* as a duration"):
            Budget.parse(text)

    def test_the_error_message_quotes_the_offending_text(self):
        with pytest.raises(ValueError, match=r"'notaduration'"):
            Budget.parse("notaduration")

    def test_a_negative_budget_is_rejected(self):
        with pytest.raises(ValueError, match=r"budget must be >= 0"):
            Budget(-1)

    def test_milliseconds_are_derived_from_microseconds(self):
        assert Budget.parse("0.15s").ms == pytest.approx(150.0)


class TestOutcome:
    def test_cost_below_the_budget_is_within_it(self):
        result = make_result(BUDGET_US - 1)

        assert result.outcome is CheckOutcome.WITHIN
        assert result.exit_code == 0

    def test_cost_exactly_equal_to_the_budget_is_within_it(self):
        result = make_result(BUDGET_US)

        assert result.outcome is CheckOutcome.WITHIN
        assert result.exit_code == 0

    def test_cost_one_microsecond_over_the_budget_fails(self):
        result = make_result(BUDGET_US + 1)

        assert result.outcome is CheckOutcome.OVER
        assert result.exit_code == 1

    def test_an_unmeasurable_entrypoint_is_neither_pass_nor_fail(self):
        result = make_result(None, failure="the entrypoint exited with status 1")

        assert result.outcome is CheckOutcome.FAILED
        assert result.exit_code == 2

    def test_headroom_is_negative_when_over_budget(self):
        assert make_result(BUDGET_US + 500).headroom_us == -500

    def test_headroom_is_unknown_when_nothing_was_measured(self):
        assert make_result(None, failure="boom").headroom_us is None


class TestCheck:
    def test_a_generous_budget_passes_and_reports_the_real_cost(self, project_dir):
        result = check(
            Entrypoint("demopkg"),
            CheckOptions(
                budget=Budget.parse("30s"),
                run=RunOptions(runs=1, warmup=0, cwd=project_dir),
            ),
        )

        assert result.outcome is CheckOutcome.WITHIN
        assert result.cost_us is not None
        assert result.cost_us > 0

    def test_a_tiny_budget_fails(self, project_dir):
        result = check(
            "demopkg",
            CheckOptions(
                budget=Budget.parse("1us"),
                run=RunOptions(runs=1, warmup=0, cwd=project_dir),
            ),
        )

        assert result.outcome is CheckOutcome.OVER
        assert result.exit_code == 1

    def test_interpreter_bootstrap_is_not_charged_to_the_entrypoint(self, project_dir):
        result = check(
            "demopkg",
            CheckOptions(
                budget=Budget.parse("30s"),
                run=RunOptions(runs=1, warmup=0, cwd=project_dir),
            ),
        )

        assert result.filtered_us is not None
        assert result.measured_us is not None
        assert result.filtered_us > 0
        assert result.cost_us == result.measured_us - result.filtered_us

    def test_a_bare_budget_may_be_given_instead_of_full_options(
        self, project_dir, monkeypatch
    ):
        monkeypatch.chdir(project_dir)

        result = check(Entrypoint("demopkg"), Budget.parse("30s"))

        assert result.outcome is CheckOutcome.WITHIN
        assert result.budget == Budget.parse("30s")

    def test_an_entrypoint_that_raises_is_reported_as_unmeasurable(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "brokenmod.py").write_text(
            textwrap.dedent(
                """
                raise ImportError("this module cannot be imported here")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        result = check(
            "brokenmod",
            CheckOptions(
                budget=Budget.parse("30s"),
                run=RunOptions(runs=1, warmup=0, cwd=tmp_path),
            ),
        )

        assert result.outcome is CheckOutcome.FAILED
        assert result.exit_code == 2
        assert result.cost_us is None
        assert result.failure is not None
        assert "ImportError" in result.failure

    def test_a_missing_module_is_reported_as_unmeasurable(self, tmp_path):
        result = check(
            "no_such_module_xyz",
            CheckOptions(
                budget=Budget.parse("30s"),
                run=RunOptions(runs=1, warmup=0, cwd=tmp_path),
            ),
        )

        assert result.outcome is CheckOutcome.FAILED
        assert result.failure is not None
        assert "ModuleNotFoundError" in result.failure

    def test_a_missing_script_is_reported_as_unmeasurable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = check(
            "./no_such_script",
            CheckOptions(
                budget=Budget.parse("30s"),
                run=RunOptions(runs=1, warmup=0, cwd=tmp_path),
            ),
        )

        assert result.outcome is CheckOutcome.FAILED
        assert result.exit_code == 2
