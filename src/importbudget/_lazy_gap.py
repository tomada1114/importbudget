"""Find the names a module reaches *indirectly* while it is still importing.

The ``MODULE_LEVEL_USE`` rule rejects a statement whose bound name is read
directly in module-level code.  It cannot see the other shape:

.. code-block:: python

    import numpy

    def _build():
        return numpy.zeros(3)

    TABLE = _build()          # runs during import, and reads `numpy`

Making ``import numpy`` lazy here is *safe* — nothing breaks — but it is
pointless: the proxy reifies before the import statement's module has finished
executing, so the measured saving is zero.  Closing this gap properly (a real
call graph, including methods, decorators and comprehension scopes) is tracked
in issue #21; this module only answers the cheap question well enough to raise
an advisory in the dry run.

The answer is a reachability set: start from the locally defined functions
called by module-level code, follow calls to other locally defined functions,
and collect every name those bodies mention.  It over-approximates (a name in a
branch that never runs still counts), which is the right direction for an
advisory: warning about a conversion that would have worked costs a line of
output, staying silent about one that saves nothing costs trust in the numbers.
"""

from __future__ import annotations

import libcst as cst

__all__ = ["names_reached_during_import"]


def names_reached_during_import(module: cst.Module) -> frozenset[str]:
    """Return every name a module-level call can reach through its own functions.

    Args:
        module: The parsed module.

    Returns:
        The names mentioned by any locally defined function that module-level
        code calls, directly or through other local functions.
    """
    functions = _local_functions(module)
    pending = [name for name in _module_level_calls(module) if name in functions]
    seen: set[str] = set()
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        mentioned = _names_in(functions[name])
        reached |= mentioned
        # Mutual recursion re-queues a name that is already seen; the guard
        # above discards it, which is what stops the walk from looping.
        pending.extend(mentioned & functions.keys())
    return frozenset(reached)


def _local_functions(module: cst.Module) -> dict[str, cst.FunctionDef]:
    """Return the module's top-level function definitions by name."""
    return {
        node.name.value: node
        for node in module.body
        if isinstance(node, cst.FunctionDef)
    }


def _module_level_calls(module: cst.Module) -> frozenset[str]:
    """Return the plain names called by statements in the module body itself.

    Bodies of top-level ``def`` and ``class`` statements are skipped: those run
    later, if at all.  Everything else in the module body — including ``if``,
    ``for`` and ``with`` blocks — runs during import.
    """
    collector = _CallCollector()
    for node in module.body:
        if isinstance(node, cst.FunctionDef | cst.ClassDef):
            continue
        node.visit(collector)
    return frozenset(collector.called)


def _names_in(node: cst.CSTNode) -> frozenset[str]:
    """Return every plain name mentioned anywhere inside a node."""
    collector = _NameCollector()
    node.visit(collector)
    return frozenset(collector.names)


class _CallCollector(cst.CSTVisitor):
    """Collect the identifiers appearing in call position."""

    def __init__(self) -> None:
        """Start with nothing called."""
        self.called: set[str] = set()

    def visit_Call(self, node: cst.Call) -> None:  # noqa: N802 - LibCST dispatches on the node type name
        """Record a call to a plain name; ignore ``a.b()`` and expressions."""
        if isinstance(node.func, cst.Name):
            self.called.add(node.func.value)


class _NameCollector(cst.CSTVisitor):
    """Collect every identifier mentioned in a subtree."""

    def __init__(self) -> None:
        """Start with nothing mentioned."""
        self.names: set[str] = set()

    def visit_Name(self, node: cst.Name) -> None:  # noqa: N802 - LibCST dispatches on the node type name
        """Record one identifier."""
        self.names.add(node.value)
