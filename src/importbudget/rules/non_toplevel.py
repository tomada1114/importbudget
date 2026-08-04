"""Reject statements that are not directly in the module body."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._context import Placement
from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["NonToplevelRule"]

#: Placements this rule reports. ``try`` has its own code, so it is not here.
_REJECTED = (Placement.FUNCTION, Placement.CLASS, Placement.BLOCK)

_ILLEGAL_MESSAGE = (
    "inside a {where}: lazy imports are only permitted at module scope, and "
    "CPython raises SyntaxError here (PEP 810 P2/P3)"
)
_CONSERVATIVE_MESSAGE = (
    "inside a {where}: legal but undocumented (PEP 810 P6-P9 rest on reading "
    "CPython's C source, not on any documented guarantee), so importbudget "
    "converts module top level only (D2)"
)


@dataclass(frozen=True, slots=True)
class NonToplevelRule:
    """Only P1 — a statement directly in the module body — is convertible.

    Function and class bodies are hard ``SyntaxError``s (PEP 810 P2/P3).
    Module-level ``if``/``with``/``for``/``while``/``match`` blocks (P6-P9) are
    legal in the shipped implementation but documented nowhere, and the docs'
    wording "only permitted at module scope" could be tightened later, so the
    conservative codemod rule in ``docs/pep810-rules.md`` §3 applies: convert
    P1 and nothing else.

    Statements under ``if TYPE_CHECKING:`` never reach this rule — they are
    dead, cost nothing, and :mod:`importbudget.analyze` drops them before any
    rule runs rather than reporting them as excluded.
    """

    code: RuleCode = RuleCode.NON_TOPLEVEL

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire when any enclosing construct other than ``try`` is present."""
        placement = context.placement_of(statement)
        found = [entry for entry in _REJECTED if entry in placement]
        if not found:
            return None
        where = " inside a ".join(str(entry) for entry in found)
        template = (
            _CONSERVATIVE_MESSAGE if found == [Placement.BLOCK] else _ILLEGAL_MESSAGE
        )
        return Violation(code=self.code, message=template.format(where=where))
