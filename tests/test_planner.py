"""Tests for joining safety verdicts with measured cost into a plan."""

from __future__ import annotations

import json

import pytest

from importbudget.attribute import attribute
from importbudget.errors import PlanInputError
from importbudget.planner import plan, plan_from_profile
from importbudget.plans import PlanOptions, PlanStatus
from importbudget.profiler import ProfileResult
from importbudget.report import to_json_dict

from .conftest import PROJECT_DIR

#: What the rule set must conclude about each costed demopkg statement.
GOLDEN_VERDICTS = {
    # `from . import slow_a`: named in __all__ of a package __init__.py.
    "demopkg/__init__.py:18": ({"MODULE_LEVEL_USE", "REEXPORT_IN_INIT"}),
    # `from typing import TYPE_CHECKING`: read by the module-level if below it.
    "demopkg/__init__.py:9": ({"MODULE_LEVEL_USE", "REEXPORT_IN_INIT"}),
    # `from .cli import main` / `from .report import render`: __all__ entries.
    "demopkg/__init__.py:19": ({"MODULE_LEVEL_USE", "REEXPORT_IN_INIT"}),
    "demopkg/__init__.py:20": ({"MODULE_LEVEL_USE", "REEXPORT_IN_INIT"}),
    # `import importlib`: importlib.import_module() runs at module level.
    "demopkg/report.py:9": ({"MODULE_LEVEL_USE"}),
    # The two ideal candidates: names read only inside function bodies.
    "demopkg/util.py:3": set(),
    "demopkg/cli.py:4": set(),
}


@pytest.fixture
def profile_result(demopkg_measurement, demopkg_index):
    return ProfileResult(
        entrypoint=demopkg_measurement.entrypoint,
        measurement=demopkg_measurement,
        attribution=attribute(demopkg_measurement, demopkg_index),
        source_root=PROJECT_DIR,
    )


@pytest.fixture
def profile_document(profile_result, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(to_json_dict(profile_result)), encoding="utf-8")
    return path


@pytest.fixture
def demopkg_plan(profile_document):
    return plan_from_profile(profile_document)


def codes_by_key(result):
    return {entry.key: set(map(str, entry.codes)) for entry in result.entries}


class TestGoldenVerdicts:
    def test_every_costed_statement_gets_the_expected_verdict(self, demopkg_plan):
        assert codes_by_key(demopkg_plan) == GOLDEN_VERDICTS

    def test_only_the_function_scoped_imports_are_proposed(self, demopkg_plan):
        assert {entry.key for entry in demopkg_plan.proposed()} == {
            "demopkg/util.py:3",
            "demopkg/cli.py:4",
        }

    def test_entries_are_ordered_by_descending_attributed_time(self, demopkg_plan):
        costs = [entry.self_us for entry in demopkg_plan.entries]

        assert costs == sorted(costs, reverse=True)

    def test_dynamic_and_entrypoint_rows_are_not_candidates(self, demopkg_plan):
        assert not any("<" in entry.key for entry in demopkg_plan.entries)

    def test_their_cost_is_reported_as_unaddressable_rather_than_dropped(
        self, demopkg_plan
    ):
        # <entrypoint> + both <dynamic> rows of the fixture.
        assert demopkg_plan.totals.unaddressable_us == 13430 + 25191 + 8077

    def test_the_predicted_saving_is_the_sum_of_the_proposed_rows(self, demopkg_plan):
        assert demopkg_plan.totals.predicted_saving_us == 4695 + 156

    def test_bound_names_are_reported_per_statement(self, demopkg_plan):
        entry = next(e for e in demopkg_plan.entries if e.key == "demopkg/util.py:3")

        assert entry.bound_names == ("decimal",)

    def test_totals_count_every_candidate(self, demopkg_plan):
        totals = demopkg_plan.totals

        assert totals.candidate_count == len(GOLDEN_VERDICTS)
        assert totals.safe_count == 2
        assert totals.excluded_count == 5
        assert totals.below_threshold_count == 0


class TestThreshold:
    def test_a_cheap_safe_statement_is_skipped_but_still_listed(self, profile_document):
        result = plan_from_profile(profile_document, PlanOptions(min_us=1000))

        assert {e.key for e in result.proposed()} == {"demopkg/util.py:3"}
        assert {e.key for e in result.below_threshold()} == {"demopkg/cli.py:4"}

    def test_a_skipped_statement_stays_safe_and_carries_no_reasons(
        self, profile_document
    ):
        result = plan_from_profile(profile_document, PlanOptions(min_us=1000))
        entry = next(iter(result.below_threshold()))

        assert entry.is_safe
        assert entry.reasons == ()

    def test_the_threshold_lowers_the_predicted_saving(self, profile_document):
        result = plan_from_profile(profile_document, PlanOptions(min_us=1000))

        assert result.totals.predicted_saving_us == 4695

    def test_a_negative_threshold_is_rejected(self):
        with pytest.raises(ValueError, match="min_us must be >= 0"):
            PlanOptions(min_us=-1)


class TestUnanalyzable:
    def test_a_row_whose_source_vanished_is_excluded_not_proposed(
        self, profile_result, tmp_path
    ):
        document = to_json_dict(profile_result)
        document["entrypoint"]["source_root"] = str(tmp_path / "gone")
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        result = plan_from_profile(path)

        assert result.proposed() == ()
        assert all(entry.codes == ("UNANALYZED",) for entry in result.entries)

    def test_an_unparsable_owned_file_becomes_a_warning_and_an_exclusion(
        self, profile_result, tmp_path
    ):
        root = tmp_path / "src"
        (root / "demopkg").mkdir(parents=True)
        for name in ("__init__.py", "cli.py", "util.py", "report.py"):
            (root / "demopkg" / name).write_text("import (\n", encoding="utf-8")
        document = to_json_dict(profile_result)
        document["entrypoint"]["source_root"] = str(root)
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        result = plan_from_profile(path)

        assert result.proposed() == ()
        assert any("could not scan" in warning for warning in result.warnings)

    def test_a_row_without_a_source_root_cannot_be_analyzed(
        self, profile_result, tmp_path
    ):
        document = to_json_dict(profile_result)
        document["entrypoint"]["source_root"] = None
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        result = plan_from_profile(path)

        assert result.proposed() == ()


def write_document(tmp_path, source_root, rows):
    """Write a minimal one-entrypoint profile document naming ``rows``."""
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document": "profile",
                "tool": {"name": "importbudget", "version": "0"},
                "entrypoint": {
                    "target": "mod",
                    "kind": "module",
                    "source_root": str(source_root),
                },
                "environment": {"python_version": "3.15", "platform": "darwin"},
                "measurement": {
                    "runs": 1,
                    "warmup_runs": 0,
                    "measured_us": 50000,
                    "filtered_baseline_us": 0,
                    "attributed_us": 50000,
                },
                "statements": rows,
            }
        ),
        encoding="utf-8",
    )
    return path


def statement_row(file, line, source, self_us=50000):
    """Build one costed statement row for :func:`write_document`."""
    return {
        "key": f"{file}:{line}",
        "kind": "statement",
        "self_us": self_us,
        "cumulative_us": self_us,
        "module": "mod",
        "file": file,
        "line": line,
        "source": source,
    }


class TestSameLineStatements:
    def test_a_line_holding_an_unsafe_neighbour_is_never_proposed(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "mod.py").write_text(
            "import os; import mypkg.plugins\n\n\ndef f():\n    return os.getcwd()\n",
            encoding="utf-8",
        )
        document = write_document(
            tmp_path,
            root,
            [statement_row("mod.py", 1, "import os; import mypkg.plugins")],
        )

        result = plan_from_profile(document)

        assert result.proposed() == ()
        assert result.entries[0].status is PlanStatus.EXCLUDED
        assert "UNUSED_IMPORT" in set(map(str, result.entries[0].codes))

    def test_such_a_line_contributes_nothing_to_the_predicted_saving(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "mod.py").write_text(
            "import os; import mypkg.plugins\n\n\ndef f():\n    return os.getcwd()\n",
            encoding="utf-8",
        )
        document = write_document(
            tmp_path,
            root,
            [statement_row("mod.py", 1, "import os; import mypkg.plugins")],
        )

        result = plan_from_profile(document)

        assert result.totals.predicted_saving_us == 0


class TestSourceRootContainment:
    @pytest.mark.parametrize(
        "file",
        [
            pytest.param("/etc/hosts", id="absolute"),
            pytest.param("../outside.py", id="parent-traversal"),
        ],
    )
    def test_a_file_outside_the_source_root_is_excluded(self, tmp_path, file):
        root = tmp_path / "src"
        root.mkdir()
        (tmp_path / "outside.py").write_text(
            "import decimal\n\n\ndef f():\n    return decimal\n", encoding="utf-8"
        )
        document = write_document(tmp_path, root, [statement_row(file, 1, "x")])

        result = plan_from_profile(document)

        assert result.proposed() == ()
        assert result.entries[0].codes == ("UNANALYZED",)

    def test_the_warning_names_the_path_without_echoing_file_contents(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        secret = tmp_path / "outside.py"
        secret.write_text("TOKEN = 'sekrit' !!not python!!\n", encoding="utf-8")
        document = write_document(
            tmp_path, root, [statement_row("../outside.py", 1, "x")]
        )

        result = plan_from_profile(document)

        assert any("outside its own source_root" in w for w in result.warnings)
        assert not any("sekrit" in w for w in result.warnings)
        assert not any("could not scan" in w for w in result.warnings)

    def test_a_file_inside_the_source_root_is_still_analyzed(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "mod.py").write_text(
            "import decimal\n\n\ndef f():\n    return decimal\n", encoding="utf-8"
        )
        document = write_document(
            tmp_path, root, [statement_row("mod.py", 1, "import decimal")]
        )

        result = plan_from_profile(document)

        assert result.entries[0].status is PlanStatus.PROPOSED


class TestProfileDocumentValidation:
    def test_a_missing_file_is_reported_clearly(self, tmp_path):
        with pytest.raises(PlanInputError, match="could not read profile document"):
            plan_from_profile(tmp_path / "nope.json")

    def test_invalid_json_is_reported_clearly(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(PlanInputError, match="is not valid JSON"):
            plan_from_profile(path)

    def test_a_json_array_is_not_a_document(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(PlanInputError, match="expected an object"):
            plan_from_profile(path)

    def test_a_plan_document_is_refused_as_input(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text('{"schema_version": 1, "document": "plan"}', encoding="utf-8")

        with pytest.raises(PlanInputError, match="is a 'plan' document"):
            plan_from_profile(path)

    def test_an_unsupported_schema_version_is_refused(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text(
            '{"document": "profile", "schema_version": 99}', encoding="utf-8"
        )

        with pytest.raises(PlanInputError, match="schema_version 99"):
            plan_from_profile(path)

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            pytest.param("entrypoint", "missing the 'entrypoint' object"),
            pytest.param("environment", "missing the 'environment' object"),
            pytest.param("measurement", "missing the 'measurement' object"),
            pytest.param("statements", "missing the 'statements' array"),
        ],
    )
    def test_a_truncated_document_names_the_missing_block(
        self, profile_result, tmp_path, field, expected
    ):
        document = to_json_dict(profile_result)
        del document[field]
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match=expected):
            plan_from_profile(path)

    def test_a_non_object_statement_entry_is_refused(self, profile_result, tmp_path):
        document = to_json_dict(profile_result)
        document["statements"] = ["oops"]
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="entries must be objects"):
            plan_from_profile(path)

    def test_an_unknown_statement_kind_is_refused(self, profile_result, tmp_path):
        document = to_json_dict(profile_result)
        document["statements"][0]["kind"] = "sideways"
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="unknown statement kind"):
            plan_from_profile(path)

    def test_a_mistyped_number_is_refused(self, profile_result, tmp_path):
        document = to_json_dict(profile_result)
        document["measurement"]["runs"] = "three"
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="must be an integer"):
            plan_from_profile(path)

    def test_a_mistyped_string_is_refused(self, profile_result, tmp_path):
        document = to_json_dict(profile_result)
        document["entrypoint"]["target"] = 7
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="must be a string"):
            plan_from_profile(path)

    def test_a_document_without_the_discriminator_is_refused(
        self, profile_result, tmp_path
    ):
        # No released schema version ever omitted `document`, so its absence
        # means a foreign file, not an older profile. Defaulting to "profile"
        # would plan against anything carrying a `statements` array.
        document = to_json_dict(profile_result)
        del document["document"]
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="is a None document, not a profile"):
            plan_from_profile(path)

    @pytest.mark.parametrize(
        ("block", "field"),
        [
            pytest.param("measurement", "runs", id="runs"),
            pytest.param("measurement", "warmup_runs", id="warmup_runs"),
            pytest.param("measurement", "measured_us", id="measured_us"),
            pytest.param("measurement", "filtered_baseline_us", id="filtered_us"),
            pytest.param("measurement", "attributed_us", id="attributed_us"),
        ],
    )
    def test_a_negative_measurement_number_is_refused(
        self, profile_result, tmp_path, block, field
    ):
        document = to_json_dict(profile_result)
        document[block][field] = -5
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match=f"{field!r} must be >="):
            plan_from_profile(path)

    @pytest.mark.parametrize(
        "field",
        [pytest.param("self_us"), pytest.param("cumulative_us")],
    )
    def test_a_negative_statement_number_is_refused(
        self, profile_result, tmp_path, field
    ):
        document = to_json_dict(profile_result)
        document["statements"][0][field] = -50
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match=f"{field!r} must be >= 0"):
            plan_from_profile(path)

    def test_zero_runs_is_refused_because_nothing_was_measured(
        self, profile_result, tmp_path
    ):
        # `runs` mirrors RunOptions, which the live path validates as >= 1.
        document = to_json_dict(profile_result)
        document["measurement"]["runs"] = 0
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(PlanInputError, match="'runs' must be >= 1, got 0"):
            plan_from_profile(path)

    def test_zero_is_accepted_where_zero_is_meaningful(self, profile_result, tmp_path):
        document = to_json_dict(profile_result)
        document["measurement"]["warmup_runs"] = 0
        document["statements"][0]["self_us"] = 0
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        assert plan_from_profile(path).profile.warmup_runs == 0


class TestProfileSummary:
    def test_a_loaded_plan_records_where_its_numbers_came_from(
        self, demopkg_plan, profile_document
    ):
        assert demopkg_plan.profile.origin == profile_document.as_posix()

    def test_the_measurement_facts_survive_the_round_trip(self, demopkg_plan):
        summary = demopkg_plan.profile

        assert (summary.target, summary.kind) == ("demopkg", "module")
        assert (summary.runs, summary.warmup_runs) == (1, 1)
        assert summary.attributed_us == 92326


class TestLivePlan:
    def test_measuring_and_planning_agree_with_the_saved_document(
        self, project_dir, monkeypatch
    ):
        monkeypatch.chdir(project_dir)

        result = plan("demopkg", PlanOptions())

        # A row only exists once some module was charged to it, so a very fast
        # machine may leave one out; every row that *is* there must match.
        assert codes_by_key(result).items() <= GOLDEN_VERDICTS.items()
        assert "demopkg/util.py:3" in {entry.key for entry in result.proposed()}
        assert result.profile.origin == "measured"

    def test_a_live_plan_reports_the_status_of_every_entry(
        self, project_dir, monkeypatch
    ):
        monkeypatch.chdir(project_dir)

        result = plan("demopkg")

        assert all(entry.status in set(PlanStatus) for entry in result.entries)
