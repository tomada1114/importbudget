"""Tests for the verification report and its JSON contract."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from importbudget.report import SCHEMA_VERSION, VERIFY_DOCUMENT
from importbudget.verifies import Comparison, ComparisonKind, Side, VerifyResult
from importbudget.verify_report import (
    render_verify_json,
    render_verify_table,
    to_verify_json_dict,
)

US_PER_MS = 1000


def comparison(before, after, *, kind=ComparisonKind.RAW, reference=None):
    """Build a comparison from millisecond figures."""
    return Comparison(
        kind=kind,
        before=tuple(float(value * US_PER_MS) for value in before),
        after=tuple(float(value * US_PER_MS) for value in after),
        reference=reference,
    )


#: A large, unambiguous win: -340 ms on a 4 ms standard deviation.
BIG_WIN = comparison([480, 480, 480], [136, 140, 144])

#: A 5 ms delta lost in a 6 ms standard deviation.
INCONCLUSIVE = comparison([110, 110, 110], [99, 105, 111])

#: The same 5 ms delta, recovered against an unchanged subtree.
RECOVERED = comparison(
    [110, 110, 110],
    [104.9, 105.0, 105.1],
    kind=ComparisonKind.NORMALIZED,
    reference="json",
)


BASE = VerifyResult(
    target="demopkg",
    kind="module",
    plan_path="plan.json",
    target_version="3.15",
    schedule=(Side.BEFORE, Side.AFTER, Side.BEFORE, Side.AFTER),
    raw=BIG_WIN,
    predicted_saving_us=320_000,
    attributed_us=500_000,
    unaddressable_us=40_000,
    converted_count=3,
    skipped_count=1,
    runs=4,
    warmup_runs=1,
    python_version="3.12.0",
    platform="test-platform",
)


@pytest.fixture
def make_verify():
    """Return a factory building a verification with plausible surroundings."""

    def factory(**overrides):
        return replace(BASE, **overrides)

    return factory


class TestTable:
    def test_a_confirmed_win_is_claimed_with_its_spread(self, make_verify):
        out = render_verify_table(make_verify())

        assert "verified improvement: -340.00 ms +/- 4.00 ms" in out
        assert "(480.00 ms -> 140.00 ms)" in out

    def test_the_executed_schedule_is_printed_as_evidence(self, make_verify):
        out = render_verify_table(make_verify())

        assert "schedule: before after before after" in out

    def test_both_comparisons_are_shown_with_their_verdicts(self, make_verify):
        out = render_verify_table(
            make_verify(raw=INCONCLUSIVE, normalized=RECOVERED, predicted_saving_us=0)
        )

        assert "raw" in out
        assert "normalized" in out
        assert "not significant" in out
        assert out.count("significant") >= 2

    def test_an_inconclusive_result_refuses_to_claim_an_improvement(self, make_verify):
        out = render_verify_table(make_verify(raw=INCONCLUSIVE, predicted_saving_us=0))

        assert "no significant change" in out
        assert "does not clear the 18.00 ms noise floor" in out
        assert "verified improvement" not in out

    def test_a_close_prediction_is_reported_as_within_the_threshold(self, make_verify):
        out = render_verify_table(make_verify())

        assert "predicted -320.00 ms (within the 30% threshold)" in out

    def test_a_divergent_prediction_names_both_numbers(self, make_verify):
        out = render_verify_table(make_verify(predicted_saving_us=200_000))

        assert "diverges by 70%, over the 30% threshold" in out
        assert "  - predicted -200.00 ms, measured -340.00 ms " in out
        assert "(70% divergence, exceeds the 30% threshold)" in out

    def test_too_few_pairs_warns_that_no_claim_is_possible(self, make_verify):
        out = render_verify_table(
            make_verify(raw=comparison([480], [140]), predicted_saving_us=0)
        )

        assert "1 measured pair(s): a standard deviation needs at least 2" in out

    def test_a_crashed_run_warns_that_both_sides_may_be_incomplete(self, make_verify):
        out = render_verify_table(make_verify(returncodes=(0, 1, 0, 1)))

        assert "the entrypoint exited with a non-zero status [1]" in out

    def test_a_saving_larger_than_the_removable_cost_is_called_drift(self, make_verify):
        # Only 20 ms of the 60 ms profile is addressable, yet 340 ms was
        # measured: the two trees differ by more than the conversion explains.
        out = render_verify_table(
            make_verify(attributed_us=60_000, unaddressable_us=40_000)
        )

        assert "is larger than the 20.00 ms conversion could remove at most" in out

    def test_a_saving_within_the_removable_cost_raises_no_floor_warning(
        self, make_verify
    ):
        out = render_verify_table(make_verify(attributed_us=900_000))

        assert "conversion could remove at most" not in out

    def test_the_unaddressable_floor_is_stated(self, make_verify):
        out = render_verify_table(make_verify())

        assert "40.00 ms of the profile sits on the entrypoint itself" in out

    def test_a_missing_reference_subtree_is_called_out(self, make_verify):
        out = render_verify_table(make_verify(normalized=None))

        assert "no subtree stayed structurally identical" in out

    def test_the_reference_subtree_is_named_when_there_is_one(self, make_verify):
        out = render_verify_table(make_verify(normalized=RECOVERED))

        assert "'json' subtree" in out

    def test_a_significant_slowdown_is_reported_as_a_regression(self, make_verify):
        out = render_verify_table(
            make_verify(
                raw=comparison([140, 140, 140], [476, 480, 484]),
                predicted_saving_us=0,
            )
        )

        assert "verified regression: +340.00 ms" in out


class TestJson:
    def test_the_document_declares_its_kind_and_version(self, make_verify):
        document = to_verify_json_dict(make_verify())

        assert document["schema_version"] == SCHEMA_VERSION
        assert document["document"] == VERIFY_DOCUMENT

    def test_the_headline_numbers_are_flat_and_easy_to_gate_on(self, make_verify):
        document = to_verify_json_dict(make_verify())

        assert document["delta_ms"] == -340.0
        assert document["significant"] is True
        assert document["sd_ms"] == 4.0

    def test_raw_and_normalized_are_both_carried(self, make_verify):
        document = to_verify_json_dict(
            make_verify(raw=INCONCLUSIVE, normalized=RECOVERED, predicted_saving_us=0)
        )

        assert document["raw"]["delta_ms"] == -5.0
        assert document["raw"]["significant"] is False
        assert document["normalized"]["delta_ms"] == -5.0
        assert document["normalized"]["significant"] is True
        assert document["normalized"]["reference"] == "json"
        # The flat keys repeat whichever comparison the verdict rested on.
        assert document["significant"] is True

    def test_normalization_that_did_not_happen_is_null(self, make_verify):
        document = to_verify_json_dict(make_verify(normalized=None))

        assert document["normalized"] is None

    def test_the_schedule_proves_the_runs_were_interleaved(self, make_verify):
        document = to_verify_json_dict(make_verify())

        assert document["measurement"]["schedule"] == [
            "before",
            "after",
            "before",
            "after",
        ]

    def test_every_measured_sample_is_included(self, make_verify):
        document = to_verify_json_dict(make_verify())

        assert document["raw"]["samples"]["before_us"] == [
            480_000.0,
            480_000.0,
            480_000.0,
        ]
        assert document["raw"]["samples"]["after_us"] == [
            136_000.0,
            140_000.0,
            144_000.0,
        ]
        assert document["raw"]["pairs"] == 3

    def test_the_prediction_block_names_both_values(self, make_verify):
        document = to_verify_json_dict(make_verify(predicted_saving_us=200_000))[
            "prediction"
        ]

        assert document["predicted_ms"] == -200.0
        assert document["measured_ms"] == -340.0
        assert document["divergence_warning"] is True
        assert document["threshold"] == 0.3

    def test_a_matching_prediction_raises_no_warning(self, make_verify):
        document = to_verify_json_dict(make_verify())["prediction"]

        assert document["divergence_warning"] is False

    def test_the_rendered_json_round_trips(self, make_verify):
        document = json.loads(render_verify_json(make_verify()))

        assert document["entrypoint"] == {"target": "demopkg", "kind": "module"}
        assert document["plan"]["path"] == "plan.json"
        assert document["totals"]["unaddressable_us"] == 40_000
        assert document["conversion"] == {"converted_count": 3, "skipped_count": 1}
