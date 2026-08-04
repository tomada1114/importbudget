"""Tests for the public package interface."""

from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
from importlib.metadata import PackageNotFoundError, version

import importbudget
from importbudget import __all__, __version__


class TestPublicApi:
    def test_exports_the_profiling_entry_points(self):
        assert set(__all__) >= {
            "SCHEMA_VERSION",
            "Attribution",
            "AttributionKind",
            "AttributionResult",
            "Entrypoint",
            "EntrypointError",
            "EntrypointKind",
            "ForeignStderr",
            "ImportBudgetError",
            "Measurement",
            "MeasurementError",
            "ProfileResult",
            "RunOptions",
            "SourceScanError",
            "__version__",
            "profile",
            "render_json",
            "render_table",
            "to_json_dict",
        }

    def test_exports_the_planning_entry_points(self):
        assert set(__all__) >= {
            "PLAN_DOCUMENT",
            "PROFILE_DOCUMENT",
            "RULES",
            "Analyzer",
            "PlanEntry",
            "PlanInputError",
            "PlanOptions",
            "PlanResult",
            "PlanStatus",
            "PlanTotals",
            "ProfileSummary",
            "Rule",
            "RuleCode",
            "Verdict",
            "Violation",
            "analyze",
            "plan",
            "plan_from_profile",
            "render_plan_json",
            "render_plan_table",
            "to_plan_json_dict",
        }

    def test_the_public_surface_is_exactly_these_two_groups(self):
        assert len(__all__) == 40

    def test_every_exported_name_exists(self):
        for name in __all__:
            assert hasattr(importbudget, name), name

    def test_errors_share_one_base_class(self):
        assert issubclass(importbudget.MeasurementError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.EntrypointError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.SourceScanError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.PlanInputError, importbudget.ImportBudgetError)


class TestPackageMetadata:
    def test_version_matches_installed_metadata(self):
        assert __version__ == version("importbudget")

    def test_version_falls_back_when_package_not_installed(self, monkeypatch):
        module = importlib.import_module("importbudget._version")

        def fake_version(_: str) -> str:
            raise PackageNotFoundError

        with monkeypatch.context() as patched:
            patched.setattr(importlib_metadata, "version", fake_version)
            reloaded = importlib.reload(module)

        assert reloaded.__version__ == "0.0.0+unknown"
        importlib.reload(module)
