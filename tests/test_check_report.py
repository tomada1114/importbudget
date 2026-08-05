"""Tests for the budget-check report and its JSON contract."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from importbudget.budgets import Budget, CheckResult
from importbudget.check_report import (
    render_check_json,
    render_check_table,
    to_check_json_dict,
)
from importbudget.report import CHECK_DOCUMENT, SCHEMA_VERSION
from importbudget.stderr import ForeignStderr

BUDGET_US = 150_000


BASE = CheckResult(
    target="demopkg",
    kind="module",
    budget=Budget(BUDGET_US),
    cost_us=120_000,
    measured_us=135_000,
    filtered_us=15_000,
    runs=3,
    warmup_runs=1,
    returncodes=(0, 0, 0),
    python_version="3.12.0",
    platform="test-platform",
)


@pytest.fixture
def make_check():
    """Return a factory building a verdict with plausible surroundings."""

    def factory(**overrides):
        return replace(BASE, **overrides)

    return factory


class TestTable:
    def test_a_passing_check_reports_the_remaining_headroom(self, make_check):
        out = render_check_table(make_check())

        assert "within budget" in out
        assert "30.00 ms to spare" in out
        assert "import cost 120.00 ms, budget 150.00 ms" in out

    def test_a_failing_check_says_by_how_much(self, make_check):
        out = render_check_table(make_check(cost_us=BUDGET_US + 1_000))

        assert "OVER BUDGET by 1.00 ms" in out
        assert "within budget" not in out

    def test_an_unmeasurable_entrypoint_claims_neither_outcome(self, make_check):
        out = render_check_table(
            make_check(cost_us=None, measured_us=None, failure="ImportError: nope")
        )

        assert "could not measure demopkg: ImportError: nope" in out
        assert "OVER BUDGET" not in out
        assert "within budget" not in out

    def test_the_header_names_the_run_policy(self, make_check):
        out = render_check_table(make_check())

        assert "mean of 3 run(s), 1 discarded" in out

    def test_warnings_and_program_stderr_are_kept_apart(self, make_check):
        out = render_check_table(
            make_check(
                warnings=("something looked odd",),
                stderr=ForeignStderr(lines=("chatty log line",), suppressed=2),
            )
        )

        assert "  - something looked odd" in out
        assert "entrypoint stderr:" in out
        assert "  - chatty log line" in out
        assert "2 more distinct line(s) suppressed" in out


class TestJson:
    def test_the_document_declares_its_kind_and_version(self, make_check):
        document = to_check_json_dict(make_check())

        assert document["schema_version"] == SCHEMA_VERSION
        assert document["document"] == CHECK_DOCUMENT

    def test_the_numbers_the_gate_used_are_all_present(self, make_check):
        document = to_check_json_dict(make_check())

        assert document["cost_us"] == 120_000
        assert document["cost_ms"] == 120.0
        assert document["budget"] == {"max_us": BUDGET_US, "max_ms": 150.0}
        assert document["headroom_us"] == 30_000
        assert document["outcome"] == "within"
        assert document["exit_code"] == 0

    def test_a_failure_leaves_the_cost_null_rather_than_zero(self, make_check):
        document = to_check_json_dict(
            make_check(cost_us=None, measured_us=None, filtered_us=None, failure="boom")
        )

        assert document["cost_us"] is None
        assert document["cost_ms"] is None
        assert document["headroom_us"] is None
        assert document["outcome"] == "failed"
        assert document["exit_code"] == 2
        assert document["failure"] == "boom"

    def test_the_rendered_json_round_trips(self, make_check):
        document = json.loads(render_check_json(make_check()))

        assert document["document"] == CHECK_DOCUMENT
        assert document["entrypoint"] == {"target": "demopkg", "kind": "module"}
