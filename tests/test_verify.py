"""Tests for the paired before/after measurement and its statistics."""

from __future__ import annotations

import json

import pytest

from importbudget._subtrees import normalize
from importbudget.entrypoints import RunOptions
from importbudget.errors import VerifyInputError
from importbudget.importtime import ImportNode, ImportTree
from importbudget.plan_report import render_plan_json
from importbudget.planner import plan
from importbudget.plans import PlanOptions
from importbudget.verifies import (
    Comparison,
    ComparisonKind,
    Side,
    VerifyOptions,
    VerifyResult,
)
from importbudget.verify import verify

US_PER_MS = 1000


def raw(before, after):
    """Build a raw comparison from millisecond figures."""
    return Comparison(
        kind=ComparisonKind.RAW,
        before=tuple(float(value * US_PER_MS) for value in before),
        after=tuple(float(value * US_PER_MS) for value in after),
    )


def tree(total_us: int, reference_us: int, *, extra: str = "") -> ImportTree:
    """Build a two-node tree whose reference subtree has a known cost.

    ``extra`` names a module that exists only in this tree, which is how a
    subtree the conversion changed is simulated.
    """
    leaf = ImportNode(
        name="reference.leaf", self_us=reference_us, cumulative_us=reference_us, depth=1
    )
    reference = ImportNode(
        name="reference", self_us=0, cumulative_us=reference_us, depth=0
    )
    reference.children = [leaf]
    leaf.parent = reference
    rest = ImportNode(
        name="entrypoint",
        self_us=total_us - reference_us,
        cumulative_us=total_us - reference_us,
        depth=0,
    )
    nodes = [leaf, reference, rest]
    if extra:
        child = ImportNode(name=extra, self_us=0, cumulative_us=0, depth=1)
        child.parent = rest
        rest.children = [child]
        nodes.append(child)
    return ImportTree(roots=(reference, rest), nodes=tuple(nodes))


class TestComparison:
    def test_the_delta_is_the_mean_of_the_paired_differences(self):
        comparison = raw([100, 110, 120], [90, 100, 110])

        assert comparison.delta_us == pytest.approx(-10 * US_PER_MS)
        assert comparison.before_us == pytest.approx(110 * US_PER_MS)
        assert comparison.after_us == pytest.approx(100 * US_PER_MS)

    def test_the_standard_deviation_is_of_the_differences_not_the_totals(self):
        # Totals swing by 20 ms; every pair differs by exactly -10 ms, so the
        # paired design sees no spread at all.
        comparison = raw([100, 120], [90, 110])

        assert comparison.sd_us == pytest.approx(0.0)

    def test_a_delta_far_above_the_noise_is_an_improvement(self):
        comparison = raw([480, 480, 481, 479], [140, 140, 141, 139])

        assert comparison.is_significant
        assert comparison.is_improvement
        assert comparison.delta_us == pytest.approx(-340 * US_PER_MS)

    def test_a_delta_below_three_sigma_claims_nothing(self):
        # Deltas of -11, -5 and +1 ms: a mean of -5 ms on an sd of exactly
        # 6 ms, so 3 sigma is 18 ms and the 5 ms delta is nowhere near it.
        comparison = raw([110, 110, 110], [99, 105, 111])

        assert comparison.delta_us == pytest.approx(-5_000.0)
        assert comparison.sd_us == pytest.approx(6_000.0)
        assert comparison.noise_floor_us == pytest.approx(18_000.0)
        assert not comparison.is_significant
        assert not comparison.is_improvement

    def test_a_delta_of_exactly_three_sigma_is_not_yet_significant(self):
        # Deltas of -24, -18 and -12 ms: a mean of -18 ms on an sd of exactly
        # 6 ms, so the delta lands precisely on 3 sigma. The comparison is
        # strict, so this is still no result.
        comparison = raw([110, 110, 110], [86, 92, 98])

        assert comparison.delta_us == pytest.approx(-18_000.0)
        assert comparison.sd_us == pytest.approx(6_000.0)
        assert comparison.noise_floor_us == abs(comparison.delta_us)
        assert not comparison.is_significant

    def test_a_significant_slowdown_is_not_called_an_improvement(self):
        comparison = raw([100, 100, 101, 99], [140, 140, 141, 139])

        assert comparison.is_significant
        assert not comparison.is_improvement

    def test_a_single_pair_has_no_standard_deviation_and_so_no_claim(self):
        comparison = raw([480], [140])

        assert comparison.sd_us == 0.0
        assert not comparison.is_significant

    def test_unpaired_samples_are_rejected(self):
        with pytest.raises(ValueError, match=r"needs paired runs"):
            Comparison(kind=ComparisonKind.RAW, before=(1.0, 2.0), after=(1.0,))


class TestNormalize:
    def test_a_delta_lost_in_the_noise_is_recovered_by_normalization(self):
        # Every run is scaled by its own machine-speed factor, independently on
        # each side of a pair. Raw totals drown the 5 ms delta; dividing by the
        # 80 ms subtree measured in the same run brings it back.
        before_scale = (1.00, 1.20, 0.90, 1.10, 1.05)
        after_scale = (1.15, 0.95, 1.25, 0.90, 1.00)
        jitter = (0, 200, -100, 150, -50)
        before = [
            (tree(round(110_000 * s), round(80_000 * s)), round(110_000 * s) + noise)
            for s, noise in zip(before_scale, jitter, strict=True)
        ]
        after = [
            (tree(round(105_000 * s), round(80_000 * s)), round(105_000 * s) + noise)
            for s, noise in zip(after_scale, jitter, strict=True)
        ]
        raw_comparison = Comparison(
            kind=ComparisonKind.RAW,
            before=tuple(float(total) for _, total in before),
            after=tuple(float(total) for _, total in after),
        )

        normalized = normalize(before, after)

        assert not raw_comparison.is_significant
        assert normalized is not None
        assert normalized.reference is not None
        assert normalized.reference.startswith("reference")
        assert normalized.is_significant
        assert normalized.is_improvement
        assert normalized.delta_us == pytest.approx(-5_200, rel=0.05)

    def test_a_subtree_the_conversion_changed_is_never_the_reference(self):
        # Making an import lazy removes its module from under the statement
        # that used to import it, so the changed subtree's module set differs
        # between the two sides and cannot be mistaken for a stable one.
        before = [(tree(110_000, 80_000, extra="gone"), 110_000) for _ in range(3)]
        after = [(tree(105_000, 80_000), 105_000) for _ in range(3)]

        normalized = normalize(before, after)

        assert normalized is not None
        assert normalized.reference is not None
        assert normalized.reference.startswith("reference")

    def test_no_usable_subtree_means_no_normalized_comparison(self):
        # The only stable subtree is far below the floor worth dividing by.
        before = [(tree(110_000, 10, extra="gone"), 110_000) for _ in range(3)]
        after = [(tree(105_000, 10), 105_000) for _ in range(3)]

        assert normalize(before, after) is None

    def test_normalization_needs_at_least_one_run(self):
        assert normalize([], []) is None


class TestDecisive:
    def make(self, raw_comparison, normalized=None):
        return VerifyResult(
            target="demopkg",
            kind="module",
            plan_path="plan.json",
            target_version="3.15",
            schedule=(Side.BEFORE, Side.AFTER),
            raw=raw_comparison,
            normalized=normalized,
        )

    def test_a_significant_raw_delta_answers_on_its_own(self):
        significant = raw([480, 480, 481, 479], [140, 140, 141, 139])
        result = self.make(significant, normalized=raw([1], [1]))

        assert result.decisive is significant

    def test_normalization_answers_only_when_the_raw_totals_refused(self):
        inconclusive = raw([110, 110, 110], [105, 96, 114])
        recovered = Comparison(
            kind=ComparisonKind.NORMALIZED,
            before=(110_000.0, 110_000.0, 110_000.0),
            after=(105_000.0, 104_900.0, 105_100.0),
            reference="json",
        )
        result = self.make(inconclusive, normalized=recovered)

        assert not inconclusive.is_significant
        assert result.decisive is recovered
        assert result.decisive.is_improvement

    def test_an_inconclusive_normalization_falls_back_to_the_raw_figures(self):
        inconclusive = raw([110, 110, 110], [105, 96, 114])
        also_inconclusive = Comparison(
            kind=ComparisonKind.NORMALIZED,
            before=(110_000.0, 110_000.0, 110_000.0),
            after=(105_000.0, 90_000.0, 120_000.0),
            reference="json",
        )
        result = self.make(inconclusive, normalized=also_inconclusive)

        assert result.decisive is inconclusive


class TestDivergence:
    def make(self, measured_ms, predicted_saving_us, threshold=0.3):
        # Four tight pairs so the delta is unambiguously significant.
        before = [200, 200, 201, 199]
        after = [value + measured_ms for value in before]
        return VerifyResult(
            target="demopkg",
            kind="module",
            plan_path="plan.json",
            target_version="3.15",
            schedule=(Side.BEFORE, Side.AFTER),
            raw=raw(before, after),
            predicted_saving_us=predicted_saving_us,
            divergence_threshold=threshold,
        )

    def test_a_prediction_far_from_the_measurement_warns(self):
        result = self.make(measured_ms=-40, predicted_saving_us=200_000)

        assert result.decisive.is_improvement
        assert result.divergence == pytest.approx(0.8)
        assert result.has_divergence_warning
        assert result.predicted_delta_ms == pytest.approx(-200.0)

    def test_a_prediction_close_to_the_measurement_does_not_warn(self):
        result = self.make(measured_ms=-340, predicted_saving_us=320_000)

        assert result.divergence == pytest.approx(0.0625)
        assert not result.has_divergence_warning

    def test_divergence_is_undefined_when_nothing_was_predicted(self):
        result = self.make(measured_ms=-40, predicted_saving_us=0)

        assert result.divergence is None
        assert not result.has_divergence_warning


class TestVerifyOptions:
    def test_a_negative_divergence_threshold_is_rejected(self):
        with pytest.raises(ValueError, match=r"divergence_threshold must be >= 0"):
            VerifyOptions(divergence_threshold=-0.1)


class TestVerifySession:
    def test_runs_strictly_alternate_between_the_two_trees(self, make_plan):
        plan_path = make_plan("import decimal\n\nVALUE = 1\n")

        result = verify(
            plan_path,
            VerifyOptions(
                run=RunOptions(runs=3, warmup=0),
                target_version="3.12",
            ),
        )

        assert result.schedule == (
            Side.BEFORE,
            Side.AFTER,
            Side.BEFORE,
            Side.AFTER,
            Side.BEFORE,
            Side.AFTER,
        )
        assert result.raw.pairs == 3

    def test_warmup_pairs_are_discarded_from_the_schedule(self, make_plan):
        plan_path = make_plan("import decimal\n\nVALUE = 1\n")

        result = verify(
            plan_path,
            VerifyOptions(
                run=RunOptions(runs=1, warmup=1),
                target_version="3.12",
            ),
        )

        assert result.schedule == (Side.BEFORE, Side.AFTER)
        assert result.warmup_runs == 1

    def test_the_users_own_source_is_never_touched(self, make_plan, tmp_path):
        plan_path = make_plan("import decimal\n\nVALUE = 1\n")
        before = (tmp_path / "sample.py").read_text(encoding="utf-8")

        verify(
            plan_path,
            VerifyOptions(run=RunOptions(runs=1, warmup=0), target_version="3.12"),
        )

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == before

    def test_a_plan_that_converts_nothing_has_nothing_to_verify(self, make_plan):
        plan_path = make_plan("import decimal\n", excluded=["import decimal"])

        with pytest.raises(VerifyInputError, match=r"converts no source"):
            verify(plan_path)

    def test_a_document_that_is_not_a_plan_is_refused(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"document": "profile"}), encoding="utf-8")

        with pytest.raises(VerifyInputError, match=r"is not a plan document"):
            verify(path)

    def test_unreadable_json_is_refused(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(VerifyInputError, match=r"is not valid JSON"):
            verify(path)

    def test_a_missing_document_is_refused(self, tmp_path):
        with pytest.raises(VerifyInputError, match=r"cannot read the plan document"):
            verify(tmp_path / "absent.json")


class TestVerifyRealConversion:
    """The one test that drives plan, apply and verify end to end."""

    def test_a_real_plan_is_measured_on_both_trees(
        self, project_dir, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(project_dir)
        plan_result = plan(
            "demopkg",
            PlanOptions(run=RunOptions(runs=1, warmup=0, cwd=project_dir)),
        )
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(render_plan_json(plan_result), encoding="utf-8")

        result = verify(
            plan_path,
            VerifyOptions(
                run=RunOptions(runs=2, warmup=0),
                target_version="3.12",
            ),
        )

        assert result.target == "demopkg"
        assert result.converted_count >= 1
        assert result.schedule == (Side.BEFORE, Side.AFTER, Side.BEFORE, Side.AFTER)
        assert result.raw.before_us > 0
        assert result.raw.after_us > 0
        # The plan's own totals travel with it, so the measurement can be held
        # against what was predicted.
        assert result.predicted_saving_us == plan_result.totals.predicted_saving_us
        assert result.unaddressable_us == plan_result.totals.unaddressable_us

    def test_the_fixture_package_still_imports_after_conversion(
        self, project_dir, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(project_dir)
        plan_result = plan(
            "demopkg",
            PlanOptions(run=RunOptions(runs=1, warmup=0, cwd=project_dir)),
        )
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(render_plan_json(plan_result), encoding="utf-8")

        result = verify(
            plan_path,
            VerifyOptions(
                run=RunOptions(runs=2, warmup=0),
                target_version="3.12",
            ),
        )

        # A converted tree that crashed would leave a non-zero status warning
        # behind; the comparison would then be between two broken runs.
        assert not any("non-zero status" in warning for warning in result.warnings)
