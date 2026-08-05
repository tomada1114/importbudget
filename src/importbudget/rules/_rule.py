"""The uniform shape every safety rule has.

A rule is a small, stateless object with a machine-readable :class:`RuleCode`
and a single :meth:`Rule.check` method.  It returns a :class:`Violation` when
the statement is *not* provably safe to make lazy, and ``None`` when this
particular rule has nothing to say.

The engine is a **whitelist**: a statement is convertible only when *every*
rule stays silent.  A rule that cannot decide must return a violation, never
``None`` — a false "safe" verdict is the one failure mode that destroys trust
in the tool, and coverage is the cheap thing to trade away for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["Rule", "RuleCode", "Violation"]


class RuleCode(StrEnum):
    """Machine-readable reason a statement was refused."""

    STAR_IMPORT = "STAR_IMPORT"
    """``from x import *`` — a ``SyntaxError`` when made lazy (PEP 810 G11)."""

    FUTURE_IMPORT = "FUTURE_IMPORT"
    """``from __future__ import ...`` — likewise a ``SyntaxError`` (G12)."""

    NON_TOPLEVEL = "NON_TOPLEVEL"
    """Not a statement directly in the module body (P2/P3, and P6-P9)."""

    TRY_EXCEPT_IMPORT = "TRY_EXCEPT_IMPORT"
    """Inside ``try``/``except``/``else``/``finally`` — a ``SyntaxError`` (P4)."""

    MODULE_LEVEL_USE = "MODULE_LEVEL_USE"
    """The bound name is read while the module itself executes (S11/S13)."""

    REEXPORT_IN_INIT = "REEXPORT_IN_INIT"
    """A package ``__init__.py`` re-export, i.e. public API surface (S9)."""

    OPAQUE_EXPORTS = "OPAQUE_EXPORTS"
    """The module's ``__all__`` cannot be read statically, so nothing is provable.

    Distinct from :attr:`MODULE_LEVEL_USE` on purpose: nothing need be read
    while the module executes; what cannot be established is which names the
    module publishes (S9).
    """

    UNUSED_IMPORT = "UNUSED_IMPORT"
    """The bound name is never read: presumed imported for side effects (S13)."""

    UNANALYZED = "UNANALYZED"
    """No rule ran at all — the source could not be read back and matched.

    Not produced by any rule.  :mod:`importbudget.planner` attaches it to a
    costed row whose statement it could not re-find, because "we did not look"
    must never be reported as "we proved it safe".
    """


@dataclass(frozen=True, slots=True)
class Violation:
    """One reason a statement was excluded.

    Attributes:
        code: Stable reason code, safe to branch on in scripts and CI.
        message: One-line human explanation, citing the constraint it rests on.
    """

    code: RuleCode
    message: str


class Rule(Protocol):
    """Structural type every rule in :data:`~importbudget.rules.RULES` satisfies."""

    @property
    def code(self) -> RuleCode:
        """The reason code this rule reports."""
        ...  # pragma: no cover - protocol declaration

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Return a violation when the statement is not provably safe.

        Args:
            statement: The scanned statement under judgement.
            context: AST-derived facts about the module holding it.

        Returns:
            A :class:`Violation`, or ``None`` when this rule sees no problem.
        """
        ...  # pragma: no cover - protocol declaration
