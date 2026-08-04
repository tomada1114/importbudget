"""Reject ``from x import *``."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._rule import RuleCode, Violation

if TYPE_CHECKING:
    from ..sources import ImportStatement
    from ._context import ModuleContext

__all__ = ["StarImportRule"]

_MESSAGE = (
    "`from ... import *` cannot be made lazy: CPython raises "
    "SyntaxError('lazy from ... import * is not allowed') (PEP 810 G11)"
)


@dataclass(frozen=True, slots=True)
class StarImportRule:
    """Star imports are a hard syntax error under ``lazy`` (PEP 810 G11).

    Two independent CPython layers reject it — ``symtable.c`` and, as a
    secondary check, ``codegen.c`` — so there is no spelling that works.
    """

    code: RuleCode = RuleCode.STAR_IMPORT

    def check(
        self,
        statement: ImportStatement,
        context: ModuleContext,
    ) -> Violation | None:
        """Fire when any imported alias is ``*``."""
        node = context.node_for(statement)
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "*" for alias in node.names
        ):
            return Violation(code=self.code, message=_MESSAGE)
        return None
