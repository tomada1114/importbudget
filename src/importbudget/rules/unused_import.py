"""Reject statements whose bound name is never read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["UnusedImportRule"]

_MESSAGE = (
    "the bound name {names} is never read in this module, so the import "
    "exists for its side effects (plugin registration, monkey-patching, "
    "codec/driver setup). Deferring it would move those side effects to a "
    "first use that never happens (PEP 810 S13)"
)
_STAR_MESSAGE = (
    "the statement binds no inspectable name, so its use cannot be proven (PEP 810 S13)"
)


@dataclass(frozen=True, slots=True)
class UnusedImportRule:
    """An import nobody reads is a side-effect import, and side effects must stay.

    Import-time registration is the primary correctness hazard of laziness
    (PEP 810 S13): the target module simply never loads if the name is never
    touched.  Statically we cannot tell "dead import someone forgot to delete"
    from "``import mypkg.plugins.postgres`` that registers a driver", so the
    whitelist refuses both.

    The inverse — a name read **only inside function bodies** — is the ideal
    lazy candidate and passes this rule.
    """

    code: RuleCode = RuleCode.UNUSED_IMPORT

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire when any bound name is never referenced anywhere in the module."""
        names = context.bound_names(statement)
        if not names:
            return Violation(code=self.code, message=_STAR_MESSAGE)
        unused = [name for name in names if name not in context.referenced_names]
        if not unused:
            return None
        listed = ", ".join(f"`{name}`" for name in unused)
        return Violation(code=self.code, message=_MESSAGE.format(names=listed))
