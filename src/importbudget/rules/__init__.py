"""The safety rule set: one rule per file, each with a machine-readable code.

The engine is a **whitelist**.  A statement is proposed for conversion only
when it *proves* safe — that is, when every rule in :data:`RULES` stays silent.
Every rule that fires excludes the statement, and a rule that cannot decide
must fire rather than abstain.  Judging one statement safe that is not (a
"false safe") is the failure mode that ends the tool's usefulness; missing a
convertible statement only costs milliseconds.

Rules are evaluated in the order below and *all* of them run, because a user
looking at an excluded statement needs the full set of reasons, not the first
one.  The order is: hard PEP 810 syntax errors first (they can never be
converted by any codemod), then placement, then semantics.

Every rule's docstring cites the constraint IDs it rests on from
``docs/pep810-rules.md`` — the verified constraint table.  No rule may invent
semantics beyond that document.
"""

from __future__ import annotations

from ._context import ModuleContext, Placement, build_context
from ._rule import Rule, RuleCode, Violation
from .future_import import FutureImportRule
from .module_level_use import ModuleLevelUseRule
from .non_toplevel import NonToplevelRule
from .opaque_exports import OpaqueExportsRule
from .reexport_init import ReexportInInitRule
from .star_import import StarImportRule
from .try_except import TryExceptImportRule
from .unused_import import UnusedImportRule

__all__ = [
    "RULES",
    "FutureImportRule",
    "ModuleContext",
    "ModuleLevelUseRule",
    "NonToplevelRule",
    "OpaqueExportsRule",
    "Placement",
    "ReexportInInitRule",
    "Rule",
    "RuleCode",
    "StarImportRule",
    "TryExceptImportRule",
    "UnusedImportRule",
    "Violation",
    "build_context",
]

#: Every rule a statement has to survive to be proposed for conversion.
RULES: tuple[Rule, ...] = (
    StarImportRule(),
    FutureImportRule(),
    NonToplevelRule(),
    TryExceptImportRule(),
    ModuleLevelUseRule(),
    ReexportInInitRule(),
    OpaqueExportsRule(),
    UnusedImportRule(),
)
