"""Tests for the table and JSON renderings of a profile."""

from __future__ import annotations

import json

import pytest

from importbudget.attribute import (
    Attribution,
    AttributionKind,
    AttributionResult,
    attribute,
)
from importbudget.importtime import TOTAL_TOLERANCE
from importbudget.profiler import ProfileResult
from importbudget.report import (
    SCHEMA_VERSION,
    render_json,
    render_table,
    to_json_dict,
)
from importbudget.stderr import ForeignStderr

from .conftest import PROJECT_DIR


@pytest.fixture
def profile_result(demopkg_measurement, demopkg_index):
    return ProfileResult(
        entrypoint=demopkg_measurement.entrypoint,
        measurement=demopkg_measurement,
        attribution=attribute(demopkg_measurement, demopkg_index),
        source_root=PROJECT_DIR,
        warnings=("something odd happened",),
        stderr=ForeignStderr(lines=("hello from the app",), suppressed=4),
    )


@pytest.fixture
def empty_result(demopkg_measurement):
    return ProfileResult(
        entrypoint=demopkg_measurement.entrypoint,
        measurement=demopkg_measurement,
        attribution=AttributionResult(
            rows=(), attributed_us=0, filtered_us=0, measured_us=0
        ),
    )


class TestRenderTable:
    def test_report_names_what_was_measured(self, profile_result):
        text = render_table(profile_result)

        assert "importbudget profile: demopkg (module)" in text
        assert "mean of 1 run(s), 1 discarded" in text

    def test_top_limits_the_number_of_statement_rows(self, profile_result):
        text = render_table(profile_result, top=3)

        assert text.count("demopkg/") + text.count("<") >= 3
        body = [line for line in text.splitlines() if line.startswith(("    ", "   "))]
        assert len(body) == 3

    def test_zero_shows_every_statement(self, profile_result):
        full = render_table(profile_result, top=0)

        assert len(full) > len(render_table(profile_result, top=1))

    def test_cumulative_column_is_marked_as_advisory(self, profile_result):
        text = render_table(profile_result)

        assert "never be summed" in text

    def test_program_stderr_is_labelled_as_the_entrypoints_own(self, profile_result):
        text = render_table(profile_result)

        assert "entrypoint stderr:" in text
        assert "  - hello from the app" in text
        assert "4 more distinct line(s) suppressed" in text

    def test_warnings_are_surfaced(self, profile_result):
        text = render_table(profile_result)

        assert "  - something odd happened" in text

    def test_dynamic_cost_is_called_out(self, profile_result):
        assert "dynamic imports" in render_table(profile_result)

    def test_long_source_lines_are_truncated(self, demopkg_measurement):
        long_source = "import " + ", ".join(f"module_{index}" for index in range(20))
        result = ProfileResult(
            entrypoint=demopkg_measurement.entrypoint,
            measurement=demopkg_measurement,
            attribution=AttributionResult(
                rows=(
                    Attribution(
                        key="wide.py:1",
                        kind=AttributionKind.STATEMENT,
                        self_us=10,
                        cumulative_us=10,
                        modules=("module_0",),
                        source=long_source,
                    ),
                ),
                attributed_us=10,
                filtered_us=0,
                measured_us=10,
            ),
        )

        text = render_table(result)

        assert long_source not in text
        assert "\u2026" in text

    def test_empty_attribution_says_so(self, empty_result):
        assert "(no import statements were attributed)" in render_table(empty_result)


class TestJsonDocument:
    def test_document_is_versioned(self, profile_result):
        document = to_json_dict(profile_result)

        assert document["schema_version"] == SCHEMA_VERSION
        assert document["tool"]["name"] == "importbudget"

    def test_metadata_describes_the_run(self, profile_result):
        document = to_json_dict(profile_result)

        assert document["entrypoint"] == {
            "target": "demopkg",
            "kind": "module",
            "source_root": str(PROJECT_DIR),
        }
        assert document["environment"]["python_version"] == "3.12.0"
        assert document["measurement"]["runs"] == 1
        assert document["measurement"]["warmup_runs"] == 1
        assert document["measurement"]["returncodes"] == [0]

    def test_statement_self_times_sum_to_the_reported_total(self, profile_result):
        document = to_json_dict(profile_result)

        total = sum(row["self_us"] for row in document["statements"])

        assert total == document["measurement"]["attributed_us"]

    def test_shares_add_up_to_one(self, profile_result):
        document = to_json_dict(profile_result)

        assert sum(row["share"] for row in document["statements"]) == pytest.approx(
            1.0, abs=1e-3
        )

    def test_every_statement_row_carries_the_documented_fields(self, profile_result):
        document = to_json_dict(profile_result)

        assert set(document["statements"][0]) == {
            "key",
            "kind",
            "file",
            "line",
            "module",
            "source",
            "self_us",
            "self_ms",
            "cumulative_us",
            "cumulative_ms",
            "share",
            "modules",
        }

    def test_json_contains_every_statement_not_just_the_table_rows(
        self, profile_result
    ):
        document = to_json_dict(profile_result)

        assert len(document["statements"]) == len(profile_result.attribution.rows)

    def test_warnings_are_included(self, profile_result):
        assert to_json_dict(profile_result)["warnings"] == ["something odd happened"]

    def test_program_stderr_is_a_separate_channel_from_warnings(self, profile_result):
        document = to_json_dict(profile_result)

        assert document["stderr"] == {
            "lines": ["hello from the app"],
            "suppressed": 4,
        }
        assert "hello from the app" not in document["warnings"]

    def test_attributed_share_stays_within_the_measurement_tolerance(
        self, profile_result
    ):
        # This share cross-checks two independently derived totals: the summed
        # self times against the roots' cumulative time. It is therefore not
        # clamped to 1.0 -- but it must stay inside the tolerance that
        # `validate_totals` enforces, which per-module rounding used to breach.
        share = to_json_dict(profile_result)["measurement"]["attributed_share"]

        assert abs(share - 1.0) <= TOTAL_TOLERANCE

    def test_render_json_round_trips(self, profile_result):
        assert json.loads(render_json(profile_result)) == to_json_dict(profile_result)

    def test_zero_totals_do_not_divide_by_zero(self, empty_result):
        document = to_json_dict(empty_result)

        assert document["measurement"]["attributed_share"] == 0.0
        assert document["statements"] == []
