"""Reject ``from __future__ import ...``."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["FutureImportRule"]

_FUTURE_MODULE = "__future__"

_MESSAGE = (
    "`from __future__ import ...` cannot be made lazy: the parser raises "
    "SyntaxError('lazy from __future__ import is not allowed') (PEP 810 G12)"
)


@dataclass(frozen=True, slots=True)
class FutureImportRule:
    """Future imports are a hard syntax error under ``lazy`` (PEP 810 G12).

    Rejected in ``Parser/action_helpers.c`` before the symbol table is even
    built, and a future statement has to run at compile time by definition.
    """

    code: RuleCode = RuleCode.FUTURE_IMPORT

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire when the statement imports from ``__future__``."""
        node = context.node_for(statement)
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == _FUTURE_MODULE
        ):
            return Violation(code=self.code, message=_MESSAGE)
        return None
