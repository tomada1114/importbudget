"""AST-derived facts the safety rules share, computed once per module.

Every rule needs some slice of the same three questions:

* **Where does the statement sit?** — PEP 810 allows ``lazy`` only at module
  top level (P1); functions, classes and ``try`` blocks are hard
  ``SyntaxError``s (P2-P4), and module-level ``if``/``with``/``for``/``while``/
  ``match`` blocks are legal but undocumented (P6-P9, D2).
* **What name does it bind?** — ``import a.b.c`` binds ``a``, not ``a.b.c``
  (G4/S7/D4); a codemod that tracks the dotted path mis-reads every use.
* **Where is that name read?** — a read that happens while the module itself
  executes reifies the proxy immediately, so laziness buys nothing and only
  shifts error timing (S11/S13).

"While the module executes" is drawn deliberately wide: class bodies,
decorators, default arguments, annotations, module-level comprehensions and
module-level ``lambda`` bodies all count as **eager**.  Only the body of a
``def``/``async def`` is treated as deferred.  Annotations in particular are
lazy in practice under PEP 649, but proving that per project is out of scope
for v0.1, and the whitelist resolves every doubt against conversion.

**Known limitation — module-level calls are not modelled.**  A ``def`` body is
treated as deferred even when the module calls that function while it is still
executing (``def _setup(): heavy.init()`` followed by a bare ``_setup()``), so
such a statement is judged safe.  Converting it is not a syntax error and does
not change what the module ends up doing, but the proxy reifies before the
module finishes: the conversion saves nothing and merely moves the load, and
any ``ImportError``, to the call site — an import-order shift in the sense of
S14.  Detecting it needs a module-level call graph; until then the predicted
saving is an upper bound, which is what :mod:`importbudget.plan_report` says.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ..sources import ImportStatement

__all__ = ["ModuleContext", "Placement", "build_context"]

_EXPORT_NAME = "__all__"

_BLOCK_TYPES = (
    ast.If,
    ast.With,
    ast.AsyncWith,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Match,
)


class Placement(StrEnum):
    """Enclosing construct that makes a statement something other than P1."""

    FUNCTION = "function body"
    CLASS = "class body"
    TRY = "try/except block"
    BLOCK = "conditional or loop block"


@dataclass(frozen=True, slots=True)
class ModuleContext:
    """Everything the rules need to judge one module's import statements.

    Attributes:
        module: Dotted name of the module.
        path: Absolute path of its source file.
        is_package_init: True when the file is a package ``__init__.py``.
        nodes: ``(line, col)`` -> the statement's AST node.
        placements: ``(line, col)`` -> the constructs enclosing that statement.
        eager_names: Names read while the module itself executes.
        referenced_names: Names read anywhere, including inside functions.
        exported_names: String entries of a module-level ``__all__``.
        has_literal_exports: False when an ``__all__`` assignment could not be
            read statically, in which case no name can be proven unexported.
    """

    module: str
    path: Path
    is_package_init: bool
    nodes: Mapping[tuple[int, int], ast.Import | ast.ImportFrom]
    placements: Mapping[tuple[int, int], frozenset[Placement]]
    eager_names: frozenset[str]
    referenced_names: frozenset[str]
    exported_names: frozenset[str]
    has_literal_exports: bool

    def has_node(self, statement: ImportStatement) -> bool:
        """Report whether this statement was found in the parsed module."""
        return _key(statement) in self.nodes

    def node_for(self, statement: ImportStatement) -> ast.Import | ast.ImportFrom:
        """Return the statement's AST node.

        Raises:
            KeyError: The statement does not belong to this module. Callers
                filter with :meth:`has_node` first; reaching this is a bug.
        """
        return self.nodes[_key(statement)]

    def placement_of(self, statement: ImportStatement) -> frozenset[Placement]:
        """Return the constructs enclosing a statement; empty means P1."""
        return self.placements.get(_key(statement), frozenset())

    def bound_names(self, statement: ImportStatement) -> tuple[str, ...]:
        """Return the names a statement binds, in source order.

        ``import a.b.c`` binds ``a`` (G4/S7); ``from x import *`` binds nothing
        this analysis can name.
        """
        return bound_names(self.node_for(statement))


def build_context(path: Path, module: str, tree: ast.Module) -> ModuleContext:
    """Derive every shared fact about one parsed module.

    Args:
        path: Source file the tree came from.
        module: Dotted name of that module.
        tree: The parsed module, as returned by
            :func:`~importbudget.sources.scan_module`.

    Returns:
        The context every rule is handed.
    """
    nodes: dict[tuple[int, int], ast.Import | ast.ImportFrom] = {}
    placements: dict[tuple[int, int], frozenset[Placement]] = {}
    _collect_imports(tree, frozenset(), nodes, placements)

    eager: set[str] = set()
    deferred: set[str] = set()
    _collect_names(tree, is_eager=True, eager=eager, deferred=deferred)
    exported, has_literal_exports = _module_exports(tree)

    return ModuleContext(
        module=module,
        path=path,
        is_package_init=path.name == "__init__.py",
        nodes=nodes,
        placements=placements,
        eager_names=frozenset(eager),
        referenced_names=frozenset(eager | deferred | exported),
        exported_names=exported,
        has_literal_exports=has_literal_exports,
    )


def bound_names(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    """Return the module-global names an import statement binds.

    ``import a.b.c`` binds only ``a`` — the same codegen path a lazy dotted
    import takes (G4/S7/D4) — while ``import a.b.c as d`` binds ``d``.
    """
    names: list[str] = []
    for alias in node.names:
        if alias.asname:
            names.append(alias.asname)
        elif alias.name != "*":
            names.append(
                alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
            )
    return tuple(names)


def _key(statement: ImportStatement) -> tuple[int, int]:
    """Return the position that identifies a statement inside its module."""
    return (statement.lineno, statement.col)


def _collect_imports(
    node: ast.AST,
    flags: frozenset[Placement],
    nodes: dict[tuple[int, int], ast.Import | ast.ImportFrom],
    placements: dict[tuple[int, int], frozenset[Placement]],
) -> None:
    """Record every import node together with the constructs enclosing it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Import | ast.ImportFrom):
            key = (child.lineno, child.col_offset)
            nodes[key] = child
            placements[key] = flags
            continue
        _collect_imports(child, flags | _flags_for(child), nodes, placements)


def _flags_for(node: ast.AST) -> frozenset[Placement]:
    """Return the placement a node adds to everything nested inside it."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        return frozenset({Placement.FUNCTION})
    if isinstance(node, ast.ClassDef):
        return frozenset({Placement.CLASS})
    if isinstance(node, ast.Try | ast.TryStar):
        return frozenset({Placement.TRY})
    if isinstance(node, _BLOCK_TYPES):
        return frozenset({Placement.BLOCK})
    return frozenset()


def _collect_names(
    node: ast.AST,
    *,
    is_eager: bool,
    eager: set[str],
    deferred: set[str],
) -> None:
    """Split every name reference into "runs at import time" and "does not"."""
    if isinstance(node, ast.Name):
        (eager if is_eager else deferred).add(node.id)
        return
    if isinstance(node, ast.Global | ast.Nonlocal):
        # A rebinding declared anywhere makes the global's identity unstable,
        # so it is treated as an import-time use regardless of where it sits.
        eager.update(node.names)
        return
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        for header in (*node.decorator_list, node.args, *_returns(node)):
            _collect_names(header, is_eager=is_eager, eager=eager, deferred=deferred)
        for statement in node.body:
            _collect_names(statement, is_eager=False, eager=eager, deferred=deferred)
        return
    for child in ast.iter_child_nodes(node):
        _collect_names(child, is_eager=is_eager, eager=eager, deferred=deferred)


def _returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.expr, ...]:
    """Return the annotation on a function's return type, if it has one."""
    return () if node.returns is None else (node.returns,)


def _module_exports(tree: ast.Module) -> tuple[frozenset[str], bool]:
    """Return the names listed in ``__all__`` and whether all of them are literal."""
    names: set[str] = set()
    is_literal = True
    assigned: set[int] = set()
    for node in ast.walk(tree):
        value = _export_value(node, assigned)
        if value is None:
            continue
        if not isinstance(value, ast.List | ast.Tuple | ast.Set):
            is_literal = False
            continue
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.add(element.value)
            else:
                is_literal = False
    if _is_mutated_opaquely(tree, assigned):
        is_literal = False
    return frozenset(names), is_literal


def _export_value(node: ast.AST, assigned: set[int]) -> ast.expr | None:
    """Return the RHS of an ``__all__`` assignment, recording the target node.

    ``assigned`` is what :func:`_is_mutated_opaquely` subtracts, leaving only
    the ``__all__`` references this function could not read.
    """
    if isinstance(node, ast.Assign):
        targets: list[ast.expr] = list(node.targets)
    elif isinstance(node, ast.AugAssign | ast.AnnAssign):
        targets = [node.target]
    else:
        return None
    found = [
        target
        for target in targets
        if isinstance(target, ast.Name) and target.id == _EXPORT_NAME
    ]
    if not found:
        return None
    assigned.update(id(target) for target in found)
    return node.value


def _is_mutated_opaquely(tree: ast.Module, assigned: set[int]) -> bool:
    """Report whether ``__all__`` is touched other than by a whole assignment.

    ``__all__.append(...)``, ``__all__.extend(...)``, ``__all__[0] = ...`` and
    ``register(__all__)`` all change the export list in ways this analysis
    cannot read back.  Since ``docs/pep810-rules.md`` §4 lists the ``__all__``
    interaction with lazy imports as **UNVERIFIED**, one such reference means
    no name in the module can be proven unexported, and the whitelist refuses
    the whole file rather than guess at its public surface.
    """
    return any(
        isinstance(node, ast.Name)
        and node.id == _EXPORT_NAME
        and id(node) not in assigned
        for node in ast.walk(tree)
    )
