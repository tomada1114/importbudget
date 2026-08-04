"""Tests for averaging several runs into one tree.

The invariant under test is exactness: ``sum(self) == sum(root cumulative)``
must survive averaging, because every share the report prints is computed
against that total.
"""

from __future__ import annotations

import pytest

from importbudget.averaging import largest_remainder, mean_tree
from importbudget.importtime import ImportTree, parse_importtime

from .conftest import node_by_name

HEADER = "import time: self [us] | cumulative | imported package"


def tree_of(*rows: tuple[int, int, int, str]) -> ImportTree:
    """Build a tree from ``(self, cumulative, depth, name)`` rows, post-order."""
    lines = [HEADER]
    lines.extend(
        f"import time: {self_us:9d} | {cumulative:10d} | {' ' * (2 * depth)}{name}"
        for self_us, cumulative, depth, name in rows
    )
    return parse_importtime("\n".join(lines))


class TestLargestRemainder:
    @pytest.mark.parametrize(
        ("values", "expected_total"),
        [
            pytest.param([0.5, 0.5, 0.5, 0.5], 2, id="all-halves"),
            pytest.param([1.4, 1.4, 1.4], 4, id="repeating-remainder"),
            pytest.param([10.0, 20.0], 30, id="already-integral"),
            pytest.param([0.0], 0, id="zero"),
            pytest.param([], 0, id="empty"),
        ],
    )
    def test_rounded_values_sum_to_the_rounded_total(self, values, expected_total):
        assert sum(largest_remainder(values)) == expected_total

    def test_each_value_stays_within_one_of_its_input(self):
        values = [3.7, 2.2, 9.9, 0.4]

        rounded = largest_remainder(values)

        assert all(abs(out - raw) < 1 for out, raw in zip(rounded, values, strict=True))

    def test_the_largest_remainder_receives_the_residual(self):
        # 1.9 + 1.1 = 3.0; only one value can round up and it must be the 1.9.
        assert largest_remainder([1.9, 1.1]) == [2, 1]


class TestMeanTree:
    def test_single_run_is_returned_unchanged(self):
        tree = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))

        assert mean_tree([tree]) is tree

    def test_totals_stay_exact_when_means_do_not_divide_evenly(self):
        # Per-module means of 15.5 and 10.5 cannot both round without a residual.
        first = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))
        second = tree_of((21, 42, 1, "child"), (21, 42, 0, "root"))

        averaged = mean_tree([first, second])

        assert averaged.total_self_us == averaged.total_root_cumulative_us

    def test_cumulative_is_rebuilt_from_the_rounded_self_times(self):
        first = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))
        second = tree_of((21, 42, 1, "child"), (21, 42, 0, "root"))

        averaged = mean_tree([first, second])
        root = node_by_name(averaged, "root")

        assert (
            root.cumulative_us
            == root.self_us + node_by_name(averaged, "child").cumulative_us
        )

    def test_module_missing_from_a_run_is_averaged_over_the_runs_that_had_it(self):
        both = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))
        without = tree_of((40, 40, 0, "root"))

        averaged = mean_tree([both, without])

        # `child` ran once at 10us, so it is reported at 10us, not 5us.
        assert node_by_name(averaged, "child").self_us == 10

    def test_module_absent_from_the_reference_run_is_reported_as_a_warning(self):
        rich = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))
        poor = tree_of((5, 15, 1, "other"), (10, 15, 0, "root"))

        averaged = mean_tree([rich, poor])

        assert any("module set differed" in w for w in averaged.warnings)
        assert "other" not in averaged.names()

    def test_identical_runs_average_to_themselves(self):
        tree = tree_of((10, 30, 1, "child"), (20, 30, 0, "root"))

        averaged = mean_tree([tree, tree, tree])

        assert averaged.total_self_us == tree.total_self_us
        assert node_by_name(averaged, "child").self_us == 10
