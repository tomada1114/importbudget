"""Attribute Python startup import time to the statements that caused it.

The public surface is deliberately small: profile an entrypoint, plan which of
its imports can safely become PEP 810 ``lazy`` imports, then render either
result as a table or as the versioned JSON contract that later stages consume.

Example:
    >>> from importbudget import RunOptions, profile, render_table
    >>> result = profile("mypackage", RunOptions(runs=3))  # doctest: +SKIP
    >>> print(render_table(result, top=5))  # doctest: +SKIP

    >>> from importbudget import plan, render_plan_table
    >>> proposal = plan("mypackage")  # doctest: +SKIP
    >>> print(render_plan_table(proposal))  # doctest: +SKIP
"""

from __future__ import annotations

from ._version import __version__
from .analyze import Analyzer, Verdict, analyze
from .attribute import Attribution, AttributionKind, AttributionResult
from .errors import (
    EntrypointError,
    ImportBudgetError,
    MeasurementError,
    PlanInputError,
    SourceScanError,
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
    PLAN_DOCUMENT,
    PROFILE_DOCUMENT,
    SCHEMA_VERSION,
    render_json,
    render_table,
    to_json_dict,
)
from .rules import RULES, Rule, RuleCode, Violation
from .stderr import ForeignStderr

__all__ = [
    "PLAN_DOCUMENT",
    "PROFILE_DOCUMENT",
    "RULES",
    "SCHEMA_VERSION",
    "Analyzer",
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
    "SourceScanError",
    "Verdict",
    "Violation",
    "__version__",
    "analyze",
    "plan",
    "plan_from_profile",
    "profile",
    "render_json",
    "render_plan_json",
    "render_plan_table",
    "render_table",
    "to_json_dict",
    "to_plan_json_dict",
]
