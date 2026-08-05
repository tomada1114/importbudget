"""Reject every statement of a module whose ``__all__`` cannot be read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["OpaqueExportsRule"]

_MESSAGE = (
    "this module's `__all__` is not a literal list of strings, so no name can "
    "be proven unexported; a lazy `from ... import` does not publish the name "
    "the way a re-export consumer expects (PEP 810 S9)"
)


@dataclass(frozen=True, slots=True)
class OpaqueExportsRule:
    """An unreadable ``__all__`` makes every name in the module unprovable.

    ``__all__ = sorted(dir())``, ``__all__.append(...)``, ``__all__[0] = ...``
    and ``register(__all__)`` all leave the export list beyond static reading.
    Since ``docs/pep810-rules.md`` §4 lists the ``__all__`` interaction with
    lazy imports as **UNVERIFIED**, a name that *might* be exported cannot be
    proven safe, so the whole file is refused rather than guessed at (S9).

    This is deliberately its own reason code rather than a
    :attr:`~importbudget.rules.RuleCode.MODULE_LEVEL_USE`: no name is read at
    import time here, and a script branching on codes should be able to tell
    "you use this at module level" from "I cannot see your public surface".
    """

    code: RuleCode = RuleCode.OPAQUE_EXPORTS

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire on every statement of a module with an unreadable ``__all__``."""
        del statement  # The verdict is a property of the module, not the line.
        if context.has_literal_exports:
            return None
        return Violation(code=self.code, message=_MESSAGE)
