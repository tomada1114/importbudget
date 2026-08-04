"""Tests for the capped capture of the profiled program's own stderr."""

from __future__ import annotations

from importbudget.stderr import MAX_STDERR_LINES, ForeignStderr


class TestCollect:
    def test_distinct_lines_are_kept_in_order(self):
        stderr = ForeignStderr.collect(["first", "second", "third"])

        assert stderr.lines == ("first", "second", "third")
        assert stderr.suppressed == 0

    def test_duplicates_collapse_without_counting_as_suppressed(self):
        stderr = ForeignStderr.collect(["same"] * 20)

        assert stderr.lines == ("same",)
        assert stderr.suppressed == 0

    def test_lines_past_the_limit_are_counted_not_kept(self):
        stderr = ForeignStderr.collect(f"line {index}" for index in range(25))

        assert len(stderr.lines) == MAX_STDERR_LINES
        assert stderr.suppressed == 25 - MAX_STDERR_LINES

    def test_empty_input_is_falsy(self):
        assert not ForeignStderr.collect([])

    def test_suppressed_only_capture_is_truthy(self):
        assert ForeignStderr(lines=(), suppressed=3)


class TestMerge:
    def test_cap_survives_merging_several_runs(self):
        parts = [
            ForeignStderr.collect([f"run {run} line {index}" for index in range(8)])
            for run in range(3)
        ]

        merged = ForeignStderr.merge(parts)

        assert len(merged.lines) == MAX_STDERR_LINES
        assert merged.suppressed == 24 - MAX_STDERR_LINES

    def test_identical_runs_do_not_inflate_the_capture(self):
        part = ForeignStderr.collect(["a", "b"])

        merged = ForeignStderr.merge([part, part, part])

        assert merged.lines == ("a", "b")
        assert merged.suppressed == 0

    def test_already_suppressed_counts_carry_over(self):
        merged = ForeignStderr.merge(
            [ForeignStderr(lines=("a",), suppressed=5), ForeignStderr(("b",), 2)]
        )

        assert merged.lines == ("a", "b")
        assert merged.suppressed == 7

    def test_merging_nothing_is_empty(self):
        assert not ForeignStderr.merge([])
