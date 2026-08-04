"""Tests for the table and JSON renderings of a plan."""

from __future__ import annotations

import json

import pytest

from importbudget.attribute import attribute
from importbudget.plan_report import (
    render_plan_json,
    render_plan_table,
    to_plan_json_dict,
)
from importbudget.planner import plan_from_profile
from importbudget.plans import (
    PlanEntry,
    PlanOptions,
    PlanResult,
    PlanStatus,
    PlanTotals,
)
from importbudget.profiler import ProfileResult
from importbudget.report import PLAN_DOCUMENT, SCHEMA_VERSION, to_json_dict

from .conftest import PROJECT_DIR


@pytest.fixture
def profile_document(demopkg_measurement, demopkg_index, tmp_path):
    result = ProfileResult(
        entrypoint=demopkg_measurement.entrypoint,
        measurement=demopkg_measurement,
        attribution=attribute(demopkg_measurement, demopkg_index),
        source_root=PROJECT_DIR,
    )
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(to_json_dict(result)), encoding="utf-8")
    return path


@pytest.fixture
def demopkg_plan(profile_document):
    return plan_from_profile(profile_document)


@pytest.fixture
def empty_plan(demopkg_plan):
    return PlanResult(
        profile=demopkg_plan.profile,
        entries=(),
        totals=PlanTotals(0, 0, 0, 0, 0, 0, 0),
    )


class TestRenderTable:
    def test_the_header_names_what_was_planned(self, demopkg_plan):
        text = render_plan_table(demopkg_plan)

        assert "importbudget plan: demopkg (module)" in text
        assert "mean of 1 run(s), 1 discarded" in text

    def test_the_header_records_where_the_numbers_came_from(
        self, demopkg_plan, profile_document
    ):
        assert profile_document.as_posix() in render_plan_table(demopkg_plan)

    def test_proposed_and_excluded_are_separate_sections(self, demopkg_plan):
        text = render_plan_table(demopkg_plan)

        assert "proposed - proved safe to make lazy (2 statement(s)" in text
        assert "excluded - not proven safe (5 statement(s)" in text

    def test_excluded_rows_carry_their_reason_codes(self, demopkg_plan):
        text = render_plan_table(demopkg_plan)

        assert "MODULE_LEVEL_USE,REEXPORT_IN_INIT" in text

    def test_proposed_rows_show_the_source_line_instead(self, demopkg_plan):
        text = render_plan_table(demopkg_plan)

        assert "import decimal" in text

    def test_the_summary_line_reports_the_predicted_saving(self, demopkg_plan):
        text = render_plan_table(demopkg_plan)

        assert "2 proposed / 5 excluded / 0 below threshold" in text
        assert "predicted saving 4.85 ms of 92.33 ms attributed" in text

    def test_the_set_dependence_caveat_is_always_present(self, demopkg_plan):
        assert "set-dependent" in render_plan_table(demopkg_plan)

    def test_excluded_is_explained_as_not_proven_rather_than_unsafe(self, demopkg_plan):
        assert "not proven safe: importbudget refuses" in render_plan_table(
            demopkg_plan
        )

    def test_unaddressable_cost_is_called_out(self, demopkg_plan):
        assert "which no statement conversion can remove" in render_plan_table(
            demopkg_plan
        )

    def test_a_threshold_section_appears_only_when_something_was_skipped(
        self, profile_document
    ):
        without = render_plan_table(plan_from_profile(profile_document))
        with_threshold = render_plan_table(
            plan_from_profile(profile_document, PlanOptions(min_us=1000))
        )

        assert "below --min-ms" not in without
        assert "below --min-ms 1 (1 statement(s)" in with_threshold

    def test_top_limits_each_section(self, demopkg_plan):
        text = render_plan_table(demopkg_plan, top=1)

        assert "... 4 more (raise --top to see)" in text

    def test_top_zero_shows_everything(self, demopkg_plan):
        assert "more (raise --top" not in render_plan_table(demopkg_plan, top=0)

    def test_an_empty_section_says_so(self, empty_plan):
        text = render_plan_table(empty_plan)

        assert text.count("(none)") == 2

    def test_a_long_source_line_is_shortened(self, demopkg_plan):
        wordy = PlanResult(
            profile=demopkg_plan.profile,
            entries=(
                PlanEntry(
                    key="wordy.py:1",
                    status=PlanStatus.PROPOSED,
                    self_us=1,
                    cumulative_us=1,
                    source="from a.very.long.dotted.path import something_verbose",
                ),
            ),
            totals=demopkg_plan.totals,
        )

        assert "…" in render_plan_table(wordy)

    def test_a_row_without_a_source_line_renders_blank(self, demopkg_plan):
        bare = PlanResult(
            profile=demopkg_plan.profile,
            entries=(
                PlanEntry(
                    key="bare.py:1",
                    status=PlanStatus.PROPOSED,
                    self_us=1,
                    cumulative_us=1,
                ),
            ),
            totals=demopkg_plan.totals,
        )

        assert "bare.py:1" in render_plan_table(bare)

    def test_warnings_are_listed(self, demopkg_plan):
        noisy = PlanResult(
            profile=demopkg_plan.profile,
            entries=demopkg_plan.entries,
            totals=demopkg_plan.totals,
            warnings=("something odd happened",),
        )

        assert "  - something odd happened" in render_plan_table(noisy)


class TestJsonDocument:
    def test_the_document_kind_is_explicit(self, demopkg_plan):
        document = to_plan_json_dict(demopkg_plan)

        assert document["document"] == PLAN_DOCUMENT
        assert document["schema_version"] == SCHEMA_VERSION

    def test_every_statement_is_present_regardless_of_display_limits(
        self, demopkg_plan
    ):
        document = to_plan_json_dict(demopkg_plan)

        assert len(document["statements"]) == len(demopkg_plan.entries)

    def test_a_statement_carries_its_verdict_status_and_reasons(self, demopkg_plan):
        document = to_plan_json_dict(demopkg_plan)
        excluded = next(s for s in document["statements"] if s["reasons"])

        assert excluded["verdict"] == "excluded"
        assert excluded["status"] == "excluded"
        assert {"code", "message"} == set(excluded["reasons"][0])

    def test_a_safe_statement_has_no_reasons(self, demopkg_plan):
        document = to_plan_json_dict(demopkg_plan)
        proposed = next(s for s in document["statements"] if s["status"] == "proposed")

        assert proposed["verdict"] == "safe"
        assert proposed["reasons"] == []
        assert proposed["bound_names"] == ["decimal"]

    def test_verdict_and_status_differ_only_below_the_threshold(self, profile_document):
        result = plan_from_profile(profile_document, PlanOptions(min_us=1000))
        document = to_plan_json_dict(result)
        skipped = next(
            s for s in document["statements"] if s["status"] == "below_threshold"
        )

        assert skipped["verdict"] == "safe"

    def test_microseconds_are_canonical_and_milliseconds_are_rounded(
        self, demopkg_plan
    ):
        document = to_plan_json_dict(demopkg_plan)
        row = next(s for s in document["statements"] if s["key"] == "demopkg/util.py:3")

        assert row["self_us"] == 4695
        assert row["self_ms"] == 4.695

    def test_the_totals_block_summarizes_the_plan(self, demopkg_plan):
        totals = to_plan_json_dict(demopkg_plan)["totals"]

        assert totals["predicted_saving_us"] == 4851
        assert totals["safe_count"] == 2
        assert totals["excluded_count"] == 5
        assert totals["candidate_count"] == 7

    def test_the_options_block_records_the_threshold(self, profile_document):
        result = plan_from_profile(profile_document, PlanOptions(min_us=1000))

        assert to_plan_json_dict(result)["options"]["min_us"] == 1000

    def test_the_caveats_travel_with_the_machine_document(self, demopkg_plan):
        notes = to_plan_json_dict(demopkg_plan)["notes"]

        assert any("set-dependent" in note for note in notes)

    def test_the_document_is_serializable(self, demopkg_plan):
        assert json.loads(render_plan_json(demopkg_plan))["document"] == PLAN_DOCUMENT

    def test_indent_none_produces_compact_json(self, demopkg_plan):
        assert "\n" not in render_plan_json(demopkg_plan, indent=None)
