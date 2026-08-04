"""Reject statements inside ``try``/``except``/``else``/``finally``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._context import Placement
from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["TryExceptImportRule"]

_MESSAGE = (
    "inside a try/except block: CPython raises SyntaxError "
    "('lazy ... not allowed inside try/except blocks') (PEP 810 P4). This is "
    "usually the optional-dependency idiom, whose whole point is to observe "
    "the ImportError at import time — laziness would defer it to first use "
    "(S11), so the fallback branch would never run"
)


@dataclass(frozen=True, slots=True)
class TryExceptImportRule:
    """``try`` blocks are a hard syntax error under ``lazy`` (PEP 810 P4).

    ``ste_in_try_block`` is set by ``Try_kind`` *and* ``TryStar_kind`` and
    covers the whole statement including ``orelse`` and ``finalbody``, so every
    branch is rejected.

    This has its own code rather than folding into ``NON_TOPLEVEL`` because the
    ``try: import fast except ImportError: import slow`` idiom is common enough
    that a generic "not top level" message would read as a tool bug.
    """

    code: RuleCode = RuleCode.TRY_EXCEPT_IMPORT

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire when a ``try`` statement encloses this import."""
        if Placement.TRY in context.placement_of(statement):
            return Violation(code=self.code, message=_MESSAGE)
        return None
