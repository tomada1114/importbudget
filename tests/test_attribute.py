"""Tests for statement-level attribution.

These use the committed importtime capture of ``tests/fixtures/project`` so the
per-line expectations are exact. Total-sum checks alone do not catch
misattribution (the cost merely moves between lines), which is why the dead
import, duplicate import and dynamic import rules get their own assertions.
"""

from __future__ import annotations

import pytest

from importbudget.attribute import ENTRYPOINT_KEY, AttributionKind, attribute
from importbudget.entrypoints import Entrypoint, EntrypointKind, Measurement
from importbudget.importtime import parse_importtime
from importbudget.index import SCRIPT_MODULE, SourceIndex, scan_package, scan_script

from .conftest import PROJECT_DIR, node_by_name, row_by_key, row_keys

HEADER = "import time: self [us] | cumulative | imported package"

TYPE_CHECKING_LINE = 14
REAL_IMPORT_LINE = 18
DUPLICATE_IMPORT_KEY = "demopkg/cli.py:3"


def make_plain_measurement(
    text: str, *, baseline: frozenset[str] | None = None
) -> Measurement:
    """Build a measurement from hand-written importtime text."""
    tree = parse_importtime(text)
    return Measurement(
        entrypoint=Entrypoint("demopkg"),
        tree=tree,
        baseline_modules=baseline or frozenset(),
        runs=1,
        warmup_runs=0,
        returncodes=(0,),
        python_executable="python",
        python_version="3.12.0",
        platform="test-platform",
    )


def with_root_owner(index: SourceIndex, owner: str) -> SourceIndex:
    """Return a copy of an index whose root rows belong to ``owner``."""
    return SourceIndex(
        statements=index.statements,
        dynamic=index.dynamic,
        root_owner=owner,
        root=index.root,
    )


@pytest.fixture
def demopkg_attribution(demopkg_measurement, demopkg_index):
    return attribute(demopkg_measurement, demopkg_index)


class TestStatementAttribution:
    def test_type_checking_import_receives_nothing(self, demopkg_attribution):
        assert f"demopkg/__init__.py:{TYPE_CHECKING_LINE}" not in row_keys(
            demopkg_attribution
        )

    def test_the_executed_import_owns_the_whole_cost(
        self, demopkg_attribution, demopkg_tree
    ):
        row = row_by_key(demopkg_attribution, f"demopkg/__init__.py:{REAL_IMPORT_LINE}")

        assert row.self_us == node_by_name(demopkg_tree, "demopkg.slow_a").self_us
        assert row.source == "from . import slow_a"
        assert row.kind is AttributionKind.STATEMENT

    def test_duplicate_import_statement_receives_zero(self, demopkg_attribution):
        # demopkg/cli.py:3 repeats `from . import slow_a`; the first occurrence
        # in demopkg/__init__.py already paid for it.
        assert DUPLICATE_IMPORT_KEY not in row_keys(demopkg_attribution)

    def test_rows_are_sorted_by_descending_self_time(self, demopkg_attribution):
        self_times = [row.self_us for row in demopkg_attribution.rows]

        assert self_times == sorted(self_times, reverse=True)

    def test_every_row_records_the_modules_it_rolled_up(self, demopkg_attribution):
        row = row_by_key(demopkg_attribution, "demopkg/util.py:3")

        assert set(row.modules) >= {"decimal", "_decimal"}

    def test_function_level_import_is_used_as_a_fallback(self, tmp_path):
        package = tmp_path / "lazypkg"
        package.mkdir()
        (package / "__init__.py").write_text(
            "def build():\n    import decimal\n    return decimal\n",
            encoding="utf-8",
        )
        text = "\n".join(
            [
                HEADER,
                "import time:       500 |        500 |   decimal",
                "import time:       100 |        600 | lazypkg",
            ]
        )

        result = attribute(
            make_plain_measurement(text), scan_package(tmp_path, "lazypkg")
        )

        assert row_by_key(result, "lazypkg/__init__.py:2").self_us == 500


class TestDynamicImports:
    def test_import_module_target_surfaces_as_a_module_level_row(
        self, demopkg_attribution, demopkg_tree
    ):
        # `importlib.import_module` bypasses the C import path, so the target
        # gets no row of its own; what it imports lands under the caller.
        row = row_by_key(demopkg_attribution, "<dynamic in demopkg.report>")

        assert row.kind is AttributionKind.DYNAMIC
        assert row.self_us == node_by_name(demopkg_tree, "demopkg.slow_b").self_us
        assert row.lineno is None

    def test_literal_argument_names_the_call_site(self, demopkg_attribution):
        row = row_by_key(demopkg_attribution, "<dynamic> demopkg/report.py:12")

        assert row.kind is AttributionKind.DYNAMIC
        assert row.lineno == 12
        assert row.source == '_forced = __import__("gzip")'

    def test_dynamic_cost_is_visible_not_dropped(self, demopkg_attribution):
        dynamic = sum(
            row.self_us
            for row in demopkg_attribution.rows
            if row.kind is AttributionKind.DYNAMIC
        )

        assert dynamic > 0
        assert dynamic < demopkg_attribution.attributed_us


class TestTotals:
    def test_attributed_and_filtered_cover_every_measured_microsecond(
        self, demopkg_attribution, demopkg_tree
    ):
        total = demopkg_attribution.attributed_us + demopkg_attribution.filtered_us

        assert total == demopkg_tree.total_self_us

    def test_attribution_stays_within_ten_percent_of_the_measured_total(
        self, demopkg_attribution
    ):
        net = demopkg_attribution.net_measured_us
        error = abs(demopkg_attribution.attributed_us - net) / net

        assert error < 0.10

    def test_cumulative_column_overlaps_and_must_not_be_summed(
        self, demopkg_attribution
    ):
        summed = sum(row.cumulative_us for row in demopkg_attribution.rows)

        assert summed > demopkg_attribution.measured_us

    def test_entrypoint_row_collects_work_with_no_owning_statement(
        self, demopkg_attribution
    ):
        row = row_by_key(demopkg_attribution, ENTRYPOINT_KEY)

        assert row.kind is AttributionKind.ENTRYPOINT
        assert "demopkg" in row.modules


class TestBaselineFiltering:
    def test_bootstrap_modules_are_dropped(self, demopkg_attribution):
        attributed = {
            module for row in demopkg_attribution.rows for module in row.modules
        }

        assert demopkg_attribution.filtered_us > 0
        assert not attributed & {"site", "encodings", "codecs", "sitecustomize"}

    def test_a_baseline_module_imported_by_our_code_is_kept(
        self, make_measurement, demopkg_capture, baseline_modules, demopkg_index
    ):
        # `decimal` is pretended to be part of the interpreter baseline; it is
        # still charged to demopkg/util.py because an owned module imports it.
        measurement = make_measurement(
            demopkg_capture, baseline=baseline_modules | {"decimal"}
        )

        result = attribute(measurement, demopkg_index)

        assert row_by_key(result, "demopkg/util.py:3").self_us > 0

    def test_the_same_module_is_dropped_where_no_owned_module_imports_it(
        self, demopkg_index
    ):
        # `pickle` twice: once as interpreter bootstrap, once below demopkg.
        text = "\n".join(
            [
                HEADER,
                "import time:       300 |        300 | pickle",
                "import time:       100 |        100 |   pickle",
                "import time:       200 |        300 | demopkg",
            ]
        )

        result = attribute(
            make_plain_measurement(text, baseline=frozenset({"pickle"})),
            demopkg_index,
        )

        assert result.filtered_us == 300
        assert result.attributed_us == 300


class TestRootOwners:
    def test_script_entrypoint_attributes_its_own_imports(
        self, make_measurement, demopkg_capture, baseline_modules
    ):
        index = scan_script(PROJECT_DIR / "run_demo.py").merged_with(
            scan_package(PROJECT_DIR, "demopkg")
        )
        measurement = make_measurement(
            demopkg_capture,
            baseline=baseline_modules,
            entrypoint=Entrypoint("run_demo.py", EntrypointKind.SCRIPT),
        )

        result = attribute(measurement, index)

        row = row_by_key(result, "run_demo.py:3")
        assert row.source == "import demopkg"
        assert row.owner == SCRIPT_MODULE
        assert ENTRYPOINT_KEY not in row_keys(result)

    def test_module_run_entrypoint_keeps_its_own_row_out_of_the_table(
        self, demopkg_index
    ):
        text = "\n".join(
            [
                HEADER,
                "import time:       200 |        200 | demopkg.cli",
                "import time:       300 |        300 | demopkg",
            ]
        )

        result = attribute(
            make_plain_measurement(text),
            with_root_owner(demopkg_index, "demopkg.cli"),
        )

        assert row_by_key(result, ENTRYPOINT_KEY).self_us == 200
        assert row_by_key(result, "demopkg/cli.py:3").self_us == 300
