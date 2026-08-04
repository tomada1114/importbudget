"""Exception hierarchy for importbudget."""

from __future__ import annotations


class ImportBudgetError(Exception):
    """Base class for every error raised by importbudget."""


class EntrypointError(ImportBudgetError):
    """The entrypoint could not be interpreted or located."""


class MeasurementError(ImportBudgetError):
    """A measurement run produced unusable ``-X importtime`` output."""


class SourceScanError(ImportBudgetError):
    """A source file could not be parsed while collecting import statements."""
