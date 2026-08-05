"""Attribute Python startup import time to the statements that caused it.

The public surface is deliberately small: profile an entrypoint, plan which of
its imports can safely become PEP 810 ``lazy`` imports, apply that plan to the
source, verify the change by measuring both sides, check the result against a
budget, and render every result as a table or as the versioned JSON contract
that the next stage consumes.

Example:
    >>> from importbudget import RunOptions, profile, render_table
    >>> result = profile("mypackage", RunOptions(runs=3))  # doctest: +SKIP
    >>> print(render_table(result, top=5))  # doctest: +SKIP

    >>> from importbudget import plan, render_plan_table
    >>> proposal = plan("mypackage")  # doctest: +SKIP
    >>> print(render_plan_table(proposal))  # doctest: +SKIP

    >>> from importbudget import ApplyOptions, apply, render_apply_diff
    >>> conversion = apply("plan.json", ApplyOptions(write=True))  # doctest: +SKIP
    >>> print(render_apply_diff(conversion))  # doctest: +SKIP

    >>> from importbudget import render_verify_table, verify
    >>> print(render_verify_table(verify("plan.json")))  # doctest: +SKIP

    >>> from importbudget import Budget, check
    >>> check("mypackage", Budget.parse("150ms")).exit_code  # doctest: +SKIP
    0
"""

from __future__ import annotations

from ._version import __version__
from .analyze import Analyzer, Verdict, analyze
from .applies import (
    FALLBACK_TARGET_VERSIONS,
    NATIVE_TARGET_VERSION,
    TARGET_VERSIONS,
    ApplyCode,
    ApplyEntry,
    ApplyOptions,
    ApplyResult,
    ApplyStatus,
    FileEdit,
    FlagCode,
)
from .apply_report import (
    render_apply_diff,
    render_apply_json,
    render_apply_table,
    to_apply_json_dict,
)
from .attribute import Attribution, AttributionKind, AttributionResult
from .budgets import Budget, CheckOptions, CheckOutcome, CheckResult
from .check import check
from .check_report import render_check_json, render_check_table, to_check_json_dict
from .codemod import apply
from .errors import (
    ApplyInputError,
    CodemodError,
    EntrypointError,
    ImportBudgetError,
    MeasurementError,
    PlanInputError,
    SourceScanError,
    VerifyInputError,
)
from .measure import Entrypoint, EntrypointKind, Measurement, RunOptions
from .plan_report import render_plan_json, render_plan_table, to_plan_json_dict
from .planner import plan, plan_from_profile
from .plans import (
    PlanEntry,
    PlanOptions,
    PlanResult,
    PlanStatus,
    PlanTotals,
    ProfileSummary,
)
from .profiler import ProfileResult, profile
from .report import (
    APPLY_DOCUMENT,
    CHECK_DOCUMENT,
    PLAN_DOCUMENT,
    PROFILE_DOCUMENT,
    SCHEMA_VERSION,
    VERIFY_DOCUMENT,
    render_json,
    render_table,
    to_json_dict,
)
from .rules import (
    RULES,
    ModuleContext,
    Placement,
    Rule,
    RuleCode,
    Violation,
    build_context,
)
from .stderr import ForeignStderr
from .verifies import (
    SIGNIFICANCE_SIGMA,
    Comparison,
    ComparisonKind,
    Side,
    VerifyOptions,
    VerifyResult,
)
from .verify import verify
from .verify_report import (
    render_verify_json,
    render_verify_table,
    to_verify_json_dict,
)

__all__ = [
    "APPLY_DOCUMENT",
    "CHECK_DOCUMENT",
    "FALLBACK_TARGET_VERSIONS",
    "NATIVE_TARGET_VERSION",
    "PLAN_DOCUMENT",
    "PROFILE_DOCUMENT",
    "RULES",
    "SCHEMA_VERSION",
    "SIGNIFICANCE_SIGMA",
    "TARGET_VERSIONS",
    "VERIFY_DOCUMENT",
    "Analyzer",
    "ApplyCode",
    "ApplyEntry",
    "ApplyInputError",
    "ApplyOptions",
    "ApplyResult",
    "ApplyStatus",
    "Attribution",
    "AttributionKind",
    "AttributionResult",
    "Budget",
    "CheckOptions",
    "CheckOutcome",
    "CheckResult",
    "CodemodError",
    "Comparison",
    "ComparisonKind",
    "Entrypoint",
    "EntrypointError",
    "EntrypointKind",
    "FileEdit",
    "FlagCode",
    "ForeignStderr",
    "ImportBudgetError",
    "Measurement",
    "MeasurementError",
    "ModuleContext",
    "Placement",
    "PlanEntry",
    "PlanInputError",
    "PlanOptions",
    "PlanResult",
    "PlanStatus",
    "PlanTotals",
    "ProfileResult",
    "ProfileSummary",
    "Rule",
    "RuleCode",
    "RunOptions",
    "Side",
    "SourceScanError",
    "Verdict",
    "VerifyInputError",
    "VerifyOptions",
    "VerifyResult",
    "Violation",
    "__version__",
    "analyze",
    "apply",
    "build_context",
    "check",
    "plan",
    "plan_from_profile",
    "profile",
    "render_apply_diff",
    "render_apply_json",
    "render_apply_table",
    "render_check_json",
    "render_check_table",
    "render_json",
    "render_plan_json",
    "render_plan_table",
    "render_table",
    "render_verify_json",
    "render_verify_table",
    "to_apply_json_dict",
    "to_check_json_dict",
    "to_json_dict",
    "to_plan_json_dict",
    "to_verify_json_dict",
    "verify",
]
