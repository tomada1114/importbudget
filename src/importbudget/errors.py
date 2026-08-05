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


class PlanInputError(ImportBudgetError):
    """A saved profile document could not be consumed by ``plan``."""


class ApplyInputError(ImportBudgetError):
    """A saved plan document could not be consumed by ``apply``."""


class CodemodError(ImportBudgetError):
    """The codemod was asked to convert a statement it must never convert.

    Raised for a broken invariant, not for user input: a plan entry marked
    ``safe`` that resolves to an import outside module top level (placement P1)
    contradicts the ``NON_TOPLEVEL`` rule, so the safe answer is to stop rather
    than to emit a ``SyntaxError`` into the user's source.
    """
