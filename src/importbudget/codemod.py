"""Convert the statements a plan proved safe into lazy imports.

:func:`apply` is the only part of importbudget that changes a user's source, so
it is built to be boring:

* it reads a plan document and looks only at ``verdict: "safe"`` statements;
* it converts only placement **P1**, statements sitting directly in the module
  body (``docs/pep810-rules.md`` §3) — a ``safe`` entry that resolves anywhere
  else is a broken invariant and raises
  :class:`~importbudget.errors.CodemodError` instead of being converted;
* it emits only grammar forms G1/G2/G6/G7, as LibCST nodes (§2, and see
  :mod:`importbudget._emit` for why nodes and not text);
* it never rewrites a physical line carrying more than one statement;
* it re-checks that the line still holds the statement the plan recorded, so a
  file edited since the plan was written is skipped, not guessed at;
* it writes nothing unless :attr:`~importbudget.applies.ApplyOptions.write` is
  set.

Everything else LibCST round-trips byte-for-byte, which is what keeps comments,
blank lines, quote styles and ``__all__`` untouched.

Re-running is a no-op.  Already-converted statements are recognised by CST node
type — :class:`libcst.LazyImport` / :class:`libcst.LazyImportFrom`, or the
fallback binding's shape — because LibCST's ``ScopeProvider`` records no
assignment for a lazy import and would report a fully converted module as
having bound nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from . import _emit
from ._apply_input import load_plan_document
from ._lazy_gap import names_reached_during_import
from .applies import (
    ApplyCode,
    ApplyEntry,
    ApplyOptions,
    ApplyResult,
    ApplyStatus,
    FileEdit,
    FlagCode,
)
from .errors import ApplyInputError, CodemodError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ._apply_input import PlanTarget

__all__ = ["apply"]

_IMPORT_NODES = (cst.Import, cst.ImportFrom, cst.LazyImport, cst.LazyImportFrom)


def apply(
    plan_path: Path | str,
    options: ApplyOptions | None = None,
) -> ApplyResult:
    """Convert every statement a plan proved safe, or report why it could not.

    Args:
        plan_path: A document written by ``importbudget plan --json``.
        options: Target version and whether to write; the default is a dry run
            emitting native PEP 810 syntax.

    Returns:
        Every safe statement's outcome plus each touched file's before/after
        text, whether or not anything was written.

    Raises:
        ApplyInputError: The plan document is unreadable or malformed, or a
            source file it names could not be read or parsed.
        CodemodError: A ``safe`` statement resolved to an import outside module
            top level, which no plan should ever produce.
    """
    resolved = ApplyOptions() if options is None else options
    path = Path(plan_path)
    targets, root = load_plan_document(path)
    entries: list[ApplyEntry] = []
    edits: list[FileEdit] = []
    for file_path, group in _by_file(targets):
        edit, file_entries = _convert_file(file_path, group, resolved, root)
        edits.append(edit)
        entries.extend(file_entries)
    if resolved.write:
        _write(edits)
    return ApplyResult(
        entries=tuple(entries),
        edits=tuple(edits),
        target_version=resolved.target_version,
        is_native=resolved.is_native,
        written=resolved.write,
        plan_path=path.as_posix(),
        source_root=root,
    )


def _by_file(
    targets: Sequence[PlanTarget],
) -> list[tuple[Path, list[PlanTarget]]]:
    """Group targets by source file, keeping first-seen file order."""
    grouped: dict[Path, list[PlanTarget]] = {}
    for target in targets:
        grouped.setdefault(target.path, []).append(target)
    return list(grouped.items())


def _write(edits: Iterable[FileEdit]) -> None:
    """Write every changed file back to disk.

    Raises:
        ApplyInputError: A converted file could not be written.
    """
    for edit in edits:
        if not edit.is_changed:
            continue
        try:
            edit.path.write_text(edit.after, encoding="utf-8")
        except OSError as exc:
            msg = f"could not write {edit.path}: {exc}"
            raise ApplyInputError(msg) from exc


class _ModuleView:
    """One parsed source file, indexed the way the codemod interrogates it."""

    def __init__(self, module: cst.Module, source: str) -> None:
        """Resolve positions once and derive every lookup from them."""
        self.module = module
        self.lines = source.splitlines()
        positions = MetadataWrapper(module, unsafe_skip_copy=True).resolve(
            PositionProvider
        )
        self.import_lines = frozenset(
            position.start.line
            for node, position in positions.items()
            if isinstance(node, _IMPORT_NODES)
        )
        self.lazy_names = frozenset(
            name for node in positions for name in _emit.lazy_bindings(node)
        )
        self.sites: dict[int, int] = {}
        for index, statement in enumerate(module.body):
            if isinstance(statement, cst.SimpleStatementLine) and statement.body:
                self.sites[positions[statement.body[0]].start.line] = index
        self.reached = names_reached_during_import(module)

    def raw_line(self, lineno: int) -> str | None:
        """Return the stripped physical line, the form the plan recorded."""
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return None

    def line_at(self, lineno: int) -> cst.SimpleStatementLine | None:
        """Return the module-top-level line starting at ``lineno``."""
        index = self.sites.get(lineno)
        if index is None:
            return None
        line = self.module.body[index]
        return line if isinstance(line, cst.SimpleStatementLine) else None

    def has_helper(self) -> bool:
        """Report whether the fallback helper is already defined here."""
        return any(
            isinstance(node, cst.FunctionDef) and node.name.value == _emit.HELPER_NAME
            for node in self.module.body
        )


def _convert_file(
    path: Path,
    targets: Sequence[PlanTarget],
    options: ApplyOptions,
    root: Path | None,
) -> tuple[FileEdit, list[ApplyEntry]]:
    """Convert one file's targets and return its edit plus one entry each.

    Raises:
        ApplyInputError: The file could not be read or parsed.
    """
    before = _read(path)
    try:
        module = cst.parse_module(before)
    except cst.ParserSyntaxError as exc:
        msg = f"could not parse {path}: {exc}"
        raise ApplyInputError(msg) from exc
    view = _ModuleView(module, before)
    entries: list[ApplyEntry] = []
    replacements: dict[int, cst.SimpleStatementLine] = {}
    for target in targets:
        entry, replacement = _convert_one(view, target, options)
        entries.append(entry)
        if replacement is not None:
            replacements[view.sites[target.lineno]] = replacement
    after = _render(view, replacements, is_native=options.is_native)
    display = path.relative_to(root).as_posix() if root else path.as_posix()
    return FileEdit(
        path=path, display_path=display, before=before, after=after
    ), entries


def _read(path: Path) -> str:
    """Read a source file.

    Raises:
        ApplyInputError: The file could not be read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"could not read {path}: {exc}"
        raise ApplyInputError(msg) from exc


def _render(
    view: _ModuleView,
    replacements: dict[int, cst.SimpleStatementLine],
    *,
    is_native: bool,
) -> str:
    """Splice the replacements back into the module and render it."""
    if not replacements:
        return view.module.code
    body = list(view.module.body)
    for index, replacement in replacements.items():
        body[index] = replacement
    if not is_native and not view.has_helper():
        first = min(replacements)
        body[first] = _spaced(replacements[first])
        body.insert(first, _emit.helper_definition())
    return view.module.with_changes(body=body).code


def _spaced(statement: cst.SimpleStatementLine) -> cst.SimpleStatementLine:
    """Put exactly two blank lines between the injected helper and what follows.

    Blank lines directly above the injection point are normalized rather than
    kept, since the helper now sits in them; comment lines are carried over
    untouched so they stay attached to the statement they annotate.
    """
    kept = [line for line in statement.leading_lines if line.comment is not None]
    return statement.with_changes(
        leading_lines=[cst.EmptyLine(), cst.EmptyLine(), *kept],
    )


def _convert_one(
    view: _ModuleView,
    target: PlanTarget,
    options: ApplyOptions,
) -> tuple[ApplyEntry, cst.SimpleStatementLine | None]:
    """Decide what happens to one safe statement.

    Raises:
        CodemodError: The statement is an import outside module top level (P1),
            which the ``NON_TOPLEVEL`` rule should already have excluded.
    """
    if view.lazy_names & set(target.bound_names):
        return _skipped(target, ApplyCode.ALREADY_LAZY), None
    # Everything below trusts the plan's line number, so prove first that the
    # line still holds what the plan saw there. Without this an earlier `apply`
    # that injected the fallback helper -- or any hand edit -- would shift the
    # file under the plan and the codemod would judge some innocent bystander.
    if view.raw_line(target.lineno) != target.source:
        return _skipped(target, ApplyCode.SOURCE_MISMATCH), None
    line = view.line_at(target.lineno)
    if line is None:
        if target.lineno in view.import_lines:
            msg = (
                f"{target.key} is marked safe but is not a module-top-level "
                f"import (PEP 810 placement P1): {target.source!r}"
            )
            raise CodemodError(msg)
        return _skipped(target, ApplyCode.NOT_FOUND), None
    return _convert_line(view, target, line, options)


def _convert_line(
    view: _ModuleView,
    target: PlanTarget,
    line: cst.SimpleStatementLine,
    options: ApplyOptions,
) -> tuple[ApplyEntry, cst.SimpleStatementLine | None]:
    """Gate one resolved line and, if every gate passes, rewrite it."""
    before = view.module.code_for_node(line).strip()
    if len(line.body) > 1:
        return _skipped(target, ApplyCode.COMPOUND_LINE, before), None
    statement = line.body[0]
    if isinstance(statement, _emit.LAZY_NODES):
        return _skipped(target, ApplyCode.ALREADY_LAZY, before), None
    if not isinstance(statement, cst.Import | cst.ImportFrom):
        return _skipped(target, ApplyCode.NOT_FOUND, before), None
    rewritten = _emit.rewrite(line, statement, is_native=options.is_native)
    if isinstance(rewritten, ApplyCode):
        return _skipped(target, rewritten, before), None
    entry = ApplyEntry(
        key=target.key,
        status=ApplyStatus.CONVERTED,
        flags=_flags(view, target),
        display_path=target.display_path,
        lineno=target.lineno,
        before=before,
        after=view.module.code_for_node(rewritten).strip(),
    )
    return entry, rewritten


def _flags(view: _ModuleView, target: PlanTarget) -> tuple[FlagCode, ...]:
    """Return the advisories a conversion carries (see issue #17)."""
    if view.reached & set(target.bound_names):
        return (FlagCode.MODULE_LEVEL_CALL,)
    return ()


def _skipped(
    target: PlanTarget,
    reason: ApplyCode,
    before: str | None = None,
) -> ApplyEntry:
    """Build the entry for a statement that was left exactly as it was."""
    return ApplyEntry(
        key=target.key,
        status=ApplyStatus.SKIPPED,
        reason=reason,
        display_path=target.display_path,
        lineno=target.lineno,
        before=before if before is not None else target.source,
    )
