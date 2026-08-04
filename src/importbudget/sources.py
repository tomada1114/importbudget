"""Collect import statements from one owned source file with :mod:`ast`.

Attribution needs to know which statement *could* have imported a given module,
so every statement carries a candidate name set: ``import a.b.c`` is a
candidate for ``a``, ``a.b`` and ``a.b.c``, and ``from .x import y`` inside
``pkg.sub`` resolves to ``pkg.sub.x`` and ``pkg.sub.x.y``.

Two classifications matter beyond the names:

* Statements under ``if TYPE_CHECKING:`` / ``if False:`` are **dead** — they
  never execute and must never receive attribution. In the PoC a dead
  ``TYPE_CHECKING`` import silently stole 40ms from the real import below it.
* Statements inside a function body are not top-level; they may still run
  during import, so they stay as fallback candidates only.

Rolling these per-file results up into the set of modules we own is the job of
:mod:`importbudget.index`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._names import candidates
from .errors import SourceScanError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "DynamicImport",
    "ImportStatement",
    "scan_source",
]

#: Callables treated as dynamic imports when they appear in owned source.
_DYNAMIC_FUNCTIONS = frozenset({"import_module", "__import__"})

#: Placeholder for a dynamic import whose argument is not a string literal.
_UNKNOWN_DYNAMIC_NAME = "?"


@dataclass(frozen=True, slots=True)
class ImportStatement:
    """A single ``import`` / ``from ... import`` statement in owned source.

    Attributes:
        module: Dotted name of the module containing the statement.
        path: Absolute path of the source file.
        display_path: Path shown to users, relative to the scan root.
        lineno: 1-based line number.
        col: 0-based column, used only to order statements.
        source: The source line, stripped.
        candidates: Module names this statement can be responsible for.
        is_toplevel: False when the statement sits inside a function body.
        is_dead: True under ``if TYPE_CHECKING:`` / ``if False:``; such a
            statement never runs and is never eligible for attribution.
    """

    module: str
    path: Path
    display_path: str
    lineno: int
    col: int
    source: str
    candidates: frozenset[str]
    is_toplevel: bool = True
    is_dead: bool = False

    @property
    def location(self) -> str:
        """``file:line`` label used as the attribution key."""
        return f"{self.display_path}:{self.lineno}"


@dataclass(frozen=True, slots=True)
class DynamicImport:
    """An ``importlib.import_module`` / ``__import__`` call site.

    Attributes:
        module: Dotted name of the module containing the call.
        path: Absolute path of the source file.
        display_path: Path shown to users, relative to the scan root.
        lineno: 1-based line number.
        source: The source line, stripped.
        name: The imported module when the argument is a string literal.
    """

    module: str
    path: Path
    display_path: str
    lineno: int
    source: str
    name: str | None = None

    @property
    def location(self) -> str:
        """``file:line`` label used as the attribution key."""
        return f"{self.display_path}:{self.lineno}"


def scan_source(
    path: Path,
    module: str,
    *,
    root: Path | None = None,
) -> tuple[tuple[ImportStatement, ...], tuple[DynamicImport, ...]]:
    """Collect import statements and dynamic import calls from one file.

    Args:
        path: Source file to parse.
        module: Dotted name of that file's module (for a package
            ``__init__.py`` pass the package name itself).
        root: Directory display paths are made relative to.

    Returns:
        The file's import statements and dynamic import call sites, each in
        source order.

    Raises:
        SourceScanError: The file could not be read or parsed.
    """
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError, ValueError) as exc:
        msg = f"could not scan {path}: {exc}"
        raise SourceScanError(msg) from exc

    context = _FileContext(
        path=path,
        module=module,
        display_path=_display_path(path, root),
        lines=text.splitlines(),
    )
    return (
        tuple(sorted(_import_statements(tree, context), key=_position)),
        tuple(sorted(_dynamic_imports(tree, context), key=lambda c: c.lineno)),
    )


@dataclass(frozen=True, slots=True)
class _FileContext:
    """The per-file facts every statement and call site is stamped with."""

    path: Path
    module: str
    display_path: str
    lines: Sequence[str]

    @property
    def is_package(self) -> bool:
        """True when this file is a package ``__init__.py``."""
        return self.path.name == "__init__.py"


def _position(statement: ImportStatement) -> tuple[int, int]:
    """Sort key placing statements in source order."""
    return (statement.lineno, statement.col)


def _display_path(path: Path, root: Path | None) -> str:
    """Return a stable, user-facing path for a source file."""
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _import_statements(
    tree: ast.Module,
    context: _FileContext,
) -> list[ImportStatement]:
    """Collect every ``import`` statement, classified but not yet ordered."""
    dead = _dead_import_ids(tree)
    lazy = _function_scoped_import_ids(tree)
    return [
        ImportStatement(
            module=context.module,
            path=context.path,
            display_path=context.display_path,
            lineno=node.lineno,
            col=node.col_offset,
            source=_source_line(context.lines, node.lineno),
            candidates=frozenset(
                candidates(node, module=context.module, is_package=context.is_package)
            ),
            is_toplevel=id(node) not in lazy,
            is_dead=id(node) in dead,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]


def _dynamic_imports(tree: ast.Module, context: _FileContext) -> list[DynamicImport]:
    """Collect every ``import_module`` / ``__import__`` call site."""
    return [
        DynamicImport(
            module=context.module,
            path=context.path,
            display_path=context.display_path,
            lineno=node.lineno,
            source=_source_line(context.lines, node.lineno),
            name=None if name == _UNKNOWN_DYNAMIC_NAME else name,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _dynamic_call_name(node))
    ]


def _dead_import_ids(tree: ast.Module) -> frozenset[int]:
    """Return ids of import nodes under ``if TYPE_CHECKING:`` / ``if False:``."""
    dead: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_never_taken(node.test):
            # Only the body is dead; an `else:` branch does execute.
            for statement in node.body:
                dead.update(
                    id(sub)
                    for sub in ast.walk(statement)
                    if isinstance(sub, ast.Import | ast.ImportFrom)
                )
    return frozenset(dead)


def _function_scoped_import_ids(tree: ast.Module) -> frozenset[int]:
    """Return ids of import nodes inside a function body."""
    lazy: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lazy.update(
                id(sub)
                for sub in ast.walk(node)
                if isinstance(sub, ast.Import | ast.ImportFrom)
            )
    return frozenset(lazy)


def _is_never_taken(test: ast.expr) -> bool:
    """Report whether an ``if`` test guards a branch that never executes."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return isinstance(test, ast.Constant) and test.value is False


def _dynamic_call_name(node: ast.Call) -> str | None:
    """Return the imported name of a dynamic import call.

    Args:
        node: Call expression to inspect.

    Returns:
        The literal module name, ``"?"`` when the argument is not a literal, or
        ``None`` when the call is not a dynamic import at all.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        called = func.attr
    elif isinstance(func, ast.Name):
        called = func.id
    else:
        return None
    if called not in _DYNAMIC_FUNCTIONS:
        return None
    if node.args and isinstance(node.args[0], ast.Constant):
        value = node.args[0].value
        if isinstance(value, str):
            return value
    return _UNKNOWN_DYNAMIC_NAME


def _source_line(lines: Sequence[str], lineno: int) -> str:
    """Return the stripped source line, or an empty string when out of range."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ""
