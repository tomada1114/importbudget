"""Tests for the public package interface."""

from __future__ import annotations

import ast
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
            "ModuleContext",
            "Placement",
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
            "build_context",
            "plan",
            "plan_from_profile",
            "render_plan_json",
            "render_plan_table",
            "to_plan_json_dict",
        }

    def test_exports_the_conversion_entry_points(self):
        assert set(__all__) >= {
            "APPLY_DOCUMENT",
            "ApplyCode",
            "ApplyEntry",
            "ApplyInputError",
            "ApplyOptions",
            "ApplyResult",
            "ApplyStatus",
            "CodemodError",
            "FALLBACK_TARGET_VERSIONS",
            "FileEdit",
            "FlagCode",
            "NATIVE_TARGET_VERSION",
            "TARGET_VERSIONS",
            "apply",
            "render_apply_diff",
            "render_apply_json",
            "render_apply_table",
            "to_apply_json_dict",
        }

    def test_exports_the_verification_entry_points(self):
        assert set(__all__) >= {
            "CHECK_DOCUMENT",
            "SIGNIFICANCE_SIGMA",
            "VERIFY_DOCUMENT",
            "Budget",
            "CheckOptions",
            "CheckOutcome",
            "CheckResult",
            "Comparison",
            "ComparisonKind",
            "Side",
            "VerifyInputError",
            "VerifyOptions",
            "VerifyResult",
            "check",
            "render_check_json",
            "render_check_table",
            "render_verify_json",
            "render_verify_table",
            "to_check_json_dict",
            "to_verify_json_dict",
            "verify",
        }

    def test_the_public_surface_is_exactly_these_four_groups(self):
        assert len(__all__) == 82

    def test_the_rule_protocol_can_be_implemented_from_the_top_level_alone(
        self, tmp_path
    ):
        # `Rule.check` takes a ModuleContext and reads Placement off it, so a
        # third-party rule importing only from `importbudget` needs both.
        class NoBlockImportsRule:
            code = importbudget.RuleCode.NON_TOPLEVEL

            def check(self, statement, context):
                if importbudget.Placement.BLOCK not in context.placement_of(statement):
                    return None
                return importbudget.Violation(code=self.code, message="inside a block")

        path = tmp_path / "sample.py"
        path.write_text("if True:\n    import decimal\n", encoding="utf-8")

        verdicts = importbudget.analyze(path, "sample", rules=(NoBlockImportsRule(),))

        assert [str(code) for code in verdicts[0].codes] == ["NON_TOPLEVEL"]

    def test_build_context_is_reachable_from_the_top_level(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\n", encoding="utf-8")

        context = importbudget.build_context(
            path, "sample", ast.parse("import decimal")
        )

        assert isinstance(context, importbudget.ModuleContext)
        assert context.module == "sample"

    def test_every_exported_name_exists(self):
        for name in __all__:
            assert hasattr(importbudget, name), name

    def test_errors_share_one_base_class(self):
        assert issubclass(importbudget.MeasurementError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.EntrypointError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.SourceScanError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.PlanInputError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.ApplyInputError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.CodemodError, importbudget.ImportBudgetError)
        assert issubclass(importbudget.VerifyInputError, importbudget.ImportBudgetError)


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
