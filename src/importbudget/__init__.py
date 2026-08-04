"""Attribute Python startup import time to the statements that caused it.

The public surface is deliberately small: profile an entrypoint, then render
the result as a table or as the versioned JSON contract that later stages
consume.

Example:
    >>> from importbudget import RunOptions, profile, render_table
    >>> result = profile("mypackage", RunOptions(runs=3))  # doctest: +SKIP
    >>> print(render_table(result, top=5))  # doctest: +SKIP
"""

from __future__ import annotations

from ._version import __version__
from .attribute import Attribution, AttributionKind, AttributionResult
from .errors import (
    EntrypointError,
    ImportBudgetError,
    MeasurementError,
    SourceScanError,
)
from .measure import Entrypoint, EntrypointKind, Measurement, RunOptions
from .profiler import ProfileResult, profile
from .report import SCHEMA_VERSION, render_json, render_table, to_json_dict
from .stderr import ForeignStderr

__all__ = [
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
]
