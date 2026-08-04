"""Reject statements whose bound name is read while the module executes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["ModuleLevelUseRule"]

_USE_MESSAGE = (
    "the bound name {names} is read while the module itself executes "
    "(decorator, base class, call, annotation, `__all__` entry, ...): the "
    "proxy would reify immediately, so laziness buys nothing and only moves "
    "when ImportError surfaces (PEP 810 S11/S13)"
)
_DYNAMIC_EXPORT_MESSAGE = (
    "this module's `__all__` is not a literal list of strings, so no name can "
    "be proven unexported; a lazy `from ... import` does not publish the name "
    "the way a re-export consumer expects (PEP 810 S9)"
)


@dataclass(frozen=True, slots=True)
class ModuleLevelUseRule:
    """The bound name must not be touched before the module finishes importing.

    Reading an unreified proxy resolves it (S10), so a module-level use makes
    the import eager again — best case the conversion is pointless, worst case
    it only shifts an ``ImportError``/``AttributeError`` to a stranger place
    (S11) or defers a registration side effect (S13).

    "While the module executes" is read widely on purpose: class bodies,
    decorators, default arguments, annotations, module-level comprehensions and
    module-level ``lambda`` bodies all count.  What is tracked is the **bound**
    name — ``import a.b.c`` binds ``a``, so touching ``a.b`` at module level
    fires this rule (PEP 810 G4/S7/D4).
    """

    code: RuleCode = RuleCode.MODULE_LEVEL_USE

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire on any import-time read of a bound name."""
        if not context.has_literal_exports:
            return Violation(code=self.code, message=_DYNAMIC_EXPORT_MESSAGE)
        used = [
            name
            for name in context.bound_names(statement)
            if name in context.eager_names or name in context.exported_names
        ]
        if not used:
            return None
        names = ", ".join(f"`{name}`" for name in used)
        return Violation(code=self.code, message=_USE_MESSAGE.format(names=names))
