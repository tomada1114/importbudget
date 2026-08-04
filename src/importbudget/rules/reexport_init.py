"""Reject re-exports living in a package ``__init__.py``."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["ReexportInInitRule"]

_EXPORT_MESSAGE = (
    "`{name}` is listed in this package's `__all__`: an `__init__.py` export "
    "is public API, and a lazy `from ... import` does not publish the name on "
    "the parent module's dict the way re-export consumers expect (PEP 810 S9)"
)
_FROM_MESSAGE = (
    "a `from ... import` in a package `__init__.py` with no leading-underscore "
    "alias is a re-export, i.e. public API surface; laziness changes how the "
    "name appears on the package (PEP 810 S9). Alias it as `_name` if it is "
    "private"
)


@dataclass(frozen=True, slots=True)
class ReexportInInitRule:
    """A package ``__init__.py`` is the public API surface; leave it alone.

    ``lazy from mod import name`` does not publish ``name`` on ``mod.__dict__``
    (PEP 810 S9), and how ``__all__`` interacts with an unreified lazy name is
    listed as **UNVERIFIED** in ``docs/pep810-rules.md`` §4 — no primary source
    says whether ``from pkg import *`` reifies it.  Two unknowns stacked on the
    one surface users of a library actually touch is exactly where the plan
    (課題2) says to refuse.

    Fires when the file is an ``__init__.py`` **and** either the bound name is
    in ``__all__``, or the statement is a ``from`` import whose alias does not
    start with an underscore.  A plain ``import os`` used only inside functions
    stays convertible.
    """

    code: RuleCode = RuleCode.REEXPORT_IN_INIT

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire on an exported name or an unaliased from-import in ``__init__``."""
        if not context.is_package_init:
            return None
        exported = next(
            (
                name
                for name in context.bound_names(statement)
                if name in context.exported_names
            ),
            None,
        )
        if exported is not None:
            return Violation(
                code=self.code,
                message=_EXPORT_MESSAGE.format(name=exported),
            )
        node = context.node_for(statement)
        if isinstance(node, ast.ImportFrom) and _has_public_target(node):
            return Violation(code=self.code, message=_FROM_MESSAGE)
        return None


def _has_public_target(node: ast.ImportFrom) -> bool:
    """Report whether any imported target is published under a public name."""
    return any(
        alias.asname is None or not alias.asname.startswith("_") for alias in node.names
    )
