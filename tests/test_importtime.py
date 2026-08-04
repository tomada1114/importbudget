"""Tests for the ``-X importtime`` parser."""

from __future__ import annotations

import pytest

from importbudget.errors import MeasurementError
from importbudget.importtime import parse_importtime, validate_totals
from importbudget.stderr import MAX_STDERR_LINES

from .conftest import node_by_name

HEADER = "import time: self [us] | cumulative | imported package"

# A hand-written stream: `demo` imports `child`, which imports `grandchild`.
NESTED = "\n".join(
    [
        HEADER,
        "import time:        10 |         10 |       grandchild",
        "import time:        20 |         30 |     child",
        "import time:        40 |         70 |   demo.sub",
        "import time:        30 |        100 | demo",
    ]
)


class TestParseImporttime:
    def test_indentation_two_spaces_per_level_builds_the_tree(self):
        tree = parse_importtime(NESTED)

        assert [root.name for root in tree.roots] == ["demo"]
        demo = node_by_name(tree, "demo")
        sub = node_by_name(tree, "demo.sub")
        child = node_by_name(tree, "child")
        grandchild = node_by_name(tree, "grandchild")
        assert (demo.depth, sub.depth, child.depth, grandchild.depth) == (0, 1, 2, 3)
        assert demo.children == [sub]
        assert child.parent is sub
        assert grandchild.parent is child

    def test_post_order_rows_are_reattached_to_the_later_parent(self, demopkg_tree):
        decimal = node_by_name(demopkg_tree, "decimal")

        # `_decimal` is printed before `decimal` yet belongs underneath it.
        assert "_decimal" in {child.name for child in decimal.children}
        assert [node.name for node in decimal.iter_ancestors()] == [
            "demopkg.util",
            "demopkg.cli",
            "demopkg",
        ]

    def test_parent_package_nests_under_the_submodule(self, demopkg_tree):
        # `import demopkg` reports the package as the root, and the modules its
        # __init__ imports hang below it.
        root = node_by_name(demopkg_tree, "demopkg")

        assert root.parent is None
        assert "demopkg.slow_a" in {child.name for child in root.children}

    def test_self_times_sum_to_the_root_cumulative(self, demopkg_tree):
        total = demopkg_tree.total_root_cumulative_us
        error = abs(demopkg_tree.total_self_us - total) / total

        assert error < 0.01

    def test_captured_run_passes_validation(self, demopkg_tree):
        validate_totals(demopkg_tree)

    def test_subtree_iteration_yields_parents_before_children(self):
        tree = parse_importtime(NESTED)

        names = [node.name for node in tree.roots[0].iter_subtree()]

        assert names == ["demo", "demo.sub", "child", "grandchild"]

    def test_names_returns_every_module(self):
        assert parse_importtime(NESTED).names() == {
            "demo",
            "demo.sub",
            "child",
            "grandchild",
        }


class TestForgedRows:
    """A profiled program shares stderr with CPython, so it can write rows."""

    def test_row_with_the_wrong_column_widths_is_not_a_row(self, demopkg_capture):
        # Correct shape, but CPython pads self to 9 and cumulative to 10.
        forged = "import time: 999999 | 999999 | evil.module"

        tree = parse_importtime(f"{demopkg_capture}\n{forged}")
        clean = parse_importtime(demopkg_capture)

        assert tree.names() == clean.names()
        assert tree.total_self_us == clean.total_self_us
        assert forged in tree.stderr.lines

    def test_well_formed_row_with_impossible_numbers_is_rejected(self, demopkg_capture):
        # Exact `%9ld | %10ld` widths, digits in both fields -- but no import
        # can spend more time on itself than in total, so this cannot be real.
        forged = "import time:       500 |        100 | forged.module"

        tree = parse_importtime(f"{demopkg_capture}\n{forged}")

        assert "forged.module" not in tree.names()
        assert any("impossible importtime row" in w for w in tree.warnings)

    def test_row_whose_subtree_does_not_add_up_is_warned_about(self):
        # `demo` claims 100us cumulative while its own subtree accounts for
        # 10 + 1000: something injected or mis-nested the child row.
        text = "\n".join(
            [
                HEADER,
                "import time:      1000 |       1000 |   injected",
                "import time:        10 |        100 | demo",
            ]
        )

        tree = parse_importtime(text)

        assert any("does not add up" in warning for warning in tree.warnings)

    def test_real_capture_raises_no_arithmetic_warning(self, demopkg_capture):
        # CPython's own microsecond truncation must stay inside the tolerance.
        assert parse_importtime(demopkg_capture).warnings == ()


class TestForeignStderr:
    def test_program_output_is_isolated_from_the_tree(self, demopkg_capture):
        noisy = "\n".join(
            [
                "warming up",
                demopkg_capture,
                "Traceback (most recent call last):",
                "",
            ]
        )

        tree = parse_importtime(noisy)
        clean = parse_importtime(demopkg_capture)

        assert len(tree.nodes) == len(clean.nodes)
        assert tree.total_self_us == clean.total_self_us
        assert tree.warnings == ()
        assert tree.stderr.lines == (
            "warming up",
            "Traceback (most recent call last):",
        )

    def test_chatty_program_is_capped_not_dumped(self, demopkg_capture):
        chatter = "\n".join(f"log line {index}" for index in range(50))

        stderr = parse_importtime(f"{demopkg_capture}\n{chatter}").stderr

        assert len(stderr.lines) == MAX_STDERR_LINES
        assert stderr.suppressed == 50 - MAX_STDERR_LINES

    def test_repeated_line_counts_once(self, demopkg_capture):
        chatter = "\n".join(["the same warning"] * 40)

        stderr = parse_importtime(f"{demopkg_capture}\n{chatter}").stderr

        assert stderr.lines == ("the same warning",)
        assert stderr.suppressed == 0

    def test_silent_program_is_falsy(self, demopkg_capture):
        assert not parse_importtime(demopkg_capture).stderr

    def test_odd_indentation_is_reported_as_a_warning(self):
        text = "\n".join(
            [
                HEADER,
                "import time:        10 |         10 |    odd",
                "import time:        20 |         30 | root",
            ]
        )

        tree = parse_importtime(text)

        assert any("odd indentation" in warning for warning in tree.warnings)


class TestValidateTotals:
    def test_missing_child_row_is_rejected(self):
        # `demo` claims 100us cumulative but only 30us of self time is present.
        text = "\n".join([HEADER, "import time:        30 |        100 | demo"])

        with pytest.raises(MeasurementError, match=r"Import tree is inconsistent"):
            validate_totals(parse_importtime(text))

    def test_empty_output_is_rejected(self):
        with pytest.raises(MeasurementError, match=r"No `-X importtime` rows"):
            validate_totals(parse_importtime(""))

    def test_zero_cumulative_output_is_rejected(self):
        text = "\n".join([HEADER, "import time:         0 |          0 | demo"])

        with pytest.raises(MeasurementError, match=r"non-positive cumulative"):
            validate_totals(parse_importtime(text))

    def test_tolerance_is_configurable(self):
        text = "\n".join(
            [
                HEADER,
                "import time:        49 |         49 |   child",
                "import time:        50 |        100 | demo",
            ]
        )

        validate_totals(parse_importtime(text), tolerance=0.02)
        with pytest.raises(MeasurementError):
            validate_totals(parse_importtime(text), tolerance=0.001)
