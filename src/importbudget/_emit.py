"""Turn one eager import statement into a lazy one, or refuse to.

Two emitters live here and they share a gate.

The **native** emitter builds :class:`libcst.LazyImport` and
:class:`libcst.LazyImportFrom` nodes.  Building nodes rather than splicing the
word ``lazy`` into text is what structurally rules out G14
(``from . lazy import x``), which CPython accepts as an import of a relative
module literally named ``lazy`` and merely *warns* about
(``docs/pep810-rules.md`` §6 D3) — a silently wrong conversion, the one failure
mode this tool cannot afford.

The **fallback** emitter targets 3.11-3.14, where the syntax does not exist.  It
binds whole modules through :class:`importlib.util.LazyLoader` and refuses
``from x import y`` outright (:data:`~importbudget.applies.ApplyCode`
``FALLBACK_UNSUPPORTED``): the PEP 562 module ``__getattr__`` trick that would
be needed does not fire for the module's *own* global lookups, so the rewrite
would raise :exc:`NameError` inside the very module it converted.

The gate admits only grammar forms G1, G2, G6 and G7 (§2).  Forms G3-G5 and
G8-G10 are legal Python but carry extra semantics — ``import a.b.c`` binds only
``a`` (S7), ``lazy from x import a, b`` binds one proxy per name (S3) — and
forms G11-G14 are hard rejects.  A statement the rules proved *semantically*
safe can still be spelled in any of them, so shape is checked here, separately
from safety.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import libcst as cst

from .applies import ApplyCode

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "HELPER_NAME",
    "HELPER_SOURCE",
    "helper_definition",
    "is_helper_binding",
    "lazy_bindings",
    "rewrite",
]

#: Name of the fallback helper injected into a converted module.
HELPER_NAME = "_importbudget_lazy_module"

#: The fallback helper, following the ``LazyLoader`` recipe in the stdlib docs.
HELPER_SOURCE = '''\
def _importbudget_lazy_module(name):
    """Bind a module lazily through importlib.util.LazyLoader (importbudget)."""
    import importlib.util
    import sys

    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        raise ImportError(f"no module named {name!r}")
    spec.loader = importlib.util.LazyLoader(spec.loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''

#: The two node types that mean "this statement is already lazy".
LAZY_NODES = (cst.LazyImport, cst.LazyImportFrom)

_FUTURE_MODULE = "__future__"


def rewrite(
    line: cst.SimpleStatementLine,
    statement: cst.Import | cst.ImportFrom,
    *,
    is_native: bool,
) -> cst.SimpleStatementLine | ApplyCode:
    """Rewrite a one-statement line as a lazy binding.

    Args:
        line: A module-top-level line holding exactly one import statement.
        statement: That statement, already narrowed by the caller.
        is_native: Emit PEP 810 syntax rather than the ``LazyLoader`` fallback.

    Returns:
        The replacement line, or the :class:`~importbudget.applies.ApplyCode`
        explaining why this statement must be left alone.
    """
    if is_native:
        emitted = _native(statement)
        if isinstance(emitted, ApplyCode):
            return emitted
        return line.with_changes(body=[emitted])
    return _fallback(line, statement)


def _native(
    statement: cst.Import | cst.ImportFrom,
) -> cst.BaseSmallStatement | ApplyCode:
    """Build the G1/G2 or G6/G7 node for a statement, or say why not."""
    if isinstance(statement, cst.Import):
        return _lazy_import(statement)
    return _lazy_import_from(statement)


def _lazy_import(node: cst.Import) -> cst.LazyImport | ApplyCode:
    """Emit G1 (``lazy import x``) or G2 (``lazy import x as y``)."""
    if _sole_plain_alias(node.names) is None:
        return ApplyCode.UNSUPPORTED_FORM
    return cst.LazyImport(
        names=node.names,
        semicolon=node.semicolon,
        whitespace_after_import=node.whitespace_after_import,
    )


def _lazy_import_from(node: cst.ImportFrom) -> cst.LazyImportFrom | ApplyCode:
    """Emit G6 (``lazy from x import y``) or G7 (``... as z``)."""
    if node.relative or node.module is None:
        # G9/G10: relative imports are also the shapes the G14 trap lives on.
        return ApplyCode.UNSUPPORTED_FORM
    if node.lpar is not None or node.rpar is not None:
        return ApplyCode.UNSUPPORTED_FORM  # G8, parenthesized
    if isinstance(node.names, cst.ImportStar):
        return ApplyCode.UNSUPPORTED_FORM  # G11, a SyntaxError when lazy
    if _sole_alias(node.names) is None:
        return ApplyCode.UNSUPPORTED_FORM  # more than one imported name (S3)
    if _dotted(node.module) == _FUTURE_MODULE:
        return ApplyCode.UNSUPPORTED_FORM  # G12, a SyntaxError when lazy
    return cst.LazyImportFrom(
        module=node.module,
        names=node.names,
        semicolon=node.semicolon,
        whitespace_after_from=node.whitespace_after_from,
        whitespace_before_import=node.whitespace_before_import,
        whitespace_after_import=node.whitespace_after_import,
    )


def _fallback(
    line: cst.SimpleStatementLine,
    statement: cst.Import | cst.ImportFrom,
) -> cst.SimpleStatementLine | ApplyCode:
    """Emit the ``LazyLoader`` binding that stands in for G1/G2 before 3.15."""
    if isinstance(statement, cst.ImportFrom):
        return ApplyCode.FALLBACK_UNSUPPORTED
    alias = _sole_plain_alias(statement.names)
    if alias is None:
        return ApplyCode.UNSUPPORTED_FORM
    module = _dotted(alias.name)
    bound = _as_name(alias) or module
    return line.with_changes(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(bound))],
                value=cst.Call(
                    func=cst.Name(HELPER_NAME),
                    args=[cst.Arg(value=cst.SimpleString(f'"{module}"'))],
                ),
            )
        ],
    )


def helper_definition() -> cst.FunctionDef:
    """Return the fallback helper as a node, spaced to sit between statements."""
    definition = cst.parse_module(HELPER_SOURCE).body[0]
    if not isinstance(definition, cst.FunctionDef):  # pragma: no cover - constant
        msg = "the fallback helper source must define exactly one function"
        raise TypeError(msg)
    return definition.with_changes(leading_lines=[cst.EmptyLine(), cst.EmptyLine()])


def is_helper_binding(statement: cst.BaseSmallStatement) -> str | None:
    """Return the name a fallback binding binds, or ``None`` for anything else."""
    if not isinstance(statement, cst.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0].target
    call = statement.value
    if not isinstance(target, cst.Name) or not isinstance(call, cst.Call):
        return None
    if isinstance(call.func, cst.Name) and call.func.value == HELPER_NAME:
        return target.value
    return None


def lazy_bindings(node: cst.CSTNode) -> frozenset[str]:
    """Return the module-global names a node binds lazily.

    Matching is on node type, never on ``ScopeProvider``: LibCST records no
    assignment for a lazy import, so scope analysis reports a fully converted
    module as having bound nothing and every re-run would convert it again.
    """
    if isinstance(node, cst.LazyImport):
        return _alias_bindings(node.names)
    if isinstance(node, cst.LazyImportFrom) and not isinstance(
        node.names, cst.ImportStar
    ):
        return _alias_bindings(node.names)
    if isinstance(node, cst.BaseSmallStatement) and (bound := is_helper_binding(node)):
        return frozenset({bound})
    return frozenset()


def _alias_bindings(aliases: Sequence[cst.ImportAlias]) -> frozenset[str]:
    """Return the names a list of import aliases binds.

    ``import a.b.c`` binds ``a`` alone (S7), so an unaliased dotted name
    contributes only its first component.
    """
    bound: set[str] = set()
    for alias in aliases:
        as_name = _as_name(alias)
        if as_name is not None:
            bound.add(as_name)
        else:
            bound.add(_dotted(alias.name).split(".", 1)[0])
    return frozenset(bound)


def _sole_plain_alias(aliases: Sequence[cst.ImportAlias]) -> cst.ImportAlias | None:
    """Return the one alias of an undotted single-target ``import`` statement."""
    alias = _sole_alias(aliases)
    if alias is None or not isinstance(alias.name, cst.Name):
        return None  # G3 (several targets) or G4/G5 (dotted, binds only the head)
    return alias


def _sole_alias(aliases: Sequence[cst.ImportAlias]) -> cst.ImportAlias | None:
    """Return the single alias of a statement, or ``None`` when there is not one."""
    if len(aliases) != 1:
        return None
    alias = aliases[0]
    if alias.asname is not None and not isinstance(alias.asname.name, cst.Name):
        return None  # pragma: no cover - the grammar admits only a Name here
    return alias


def _as_name(alias: cst.ImportAlias) -> str | None:
    """Return an alias's ``as`` name when it is a plain identifier."""
    if alias.asname is not None and isinstance(alias.asname.name, cst.Name):
        return alias.asname.name.value
    return None


def _dotted(node: cst.BaseExpression) -> str:
    """Render a (possibly dotted) module reference back to its source spelling."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted(node.value)}.{node.attr.value}"
    return ""  # pragma: no cover - the grammar admits nothing else here
