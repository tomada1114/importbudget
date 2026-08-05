"""Value objects describing one ``importbudget apply`` run.

What to convert (:class:`ApplyOptions`), what happened to each statement the
plan proved safe (:class:`ApplyEntry`, :class:`ApplyStatus`, :class:`ApplyCode`,
:class:`FlagCode`), what a file looked like before and after
(:class:`FileEdit`) and the summary of all of it (:class:`ApplyResult`).

Kept apart from :mod:`importbudget.codemod`, which owns the LibCST machinery,
for the same reason :mod:`importbudget.plans` is kept apart from
:mod:`importbudget.planner`: the shapes crossing the public API carry no
machinery with them.

A conversion that did not happen is never silent.  Every safe statement the
codemod declined to touch keeps an :class:`ApplyCode` saying why, because
"we left it alone" and "we converted it" must be told apart by a script.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "FALLBACK_TARGET_VERSIONS",
    "NATIVE_TARGET_VERSION",
    "TARGET_VERSIONS",
    "ApplyCode",
    "ApplyEntry",
    "ApplyOptions",
    "ApplyResult",
    "ApplyStatus",
    "FileEdit",
    "FlagCode",
]

#: The only interpreter version that understands PEP 810 ``lazy`` syntax.
NATIVE_TARGET_VERSION = "3.15"

#: Versions for which the ``LazyLoader`` fallback emitter is used instead.
FALLBACK_TARGET_VERSIONS = ("3.11", "3.12", "3.13", "3.14")

#: Every value ``--target-version`` accepts.
TARGET_VERSIONS = (*FALLBACK_TARGET_VERSIONS, NATIVE_TARGET_VERSION)


class ApplyStatus(StrEnum):
    """What the codemod did with one statement the plan proved safe."""

    CONVERTED = "converted"
    """Rewritten to a lazy binding."""

    SKIPPED = "skipped"
    """Left exactly as it was; the entry's :attr:`ApplyEntry.reason` says why."""


class ApplyCode(StrEnum):
    """Machine-readable reason a safe statement was not converted."""

    UNSUPPORTED_FORM = "UNSUPPORTED_FORM"
    """Statement shape outside G1/G2/G6/G7, the only forms safe to emit.

    Multi-target (``import a, b`` — G3), dotted (``import a.b.c`` — G4/G5,
    which binds only ``a``, see S7), parenthesized (G8) and relative
    (``from . import x`` — G9/G10) statements can all be judged *semantically*
    safe by the rule engine, because the rules reason about names, not spelling.
    Emitting them is a separate decision, and this codemod declines it.
    """

    FALLBACK_UNSUPPORTED = "FALLBACK_UNSUPPORTED"
    """A ``from x import y`` statement under ``--target-version`` < 3.15.

    The fallback emitter binds whole modules through
    :class:`importlib.util.LazyLoader` and has no equivalent for a single
    imported name: the PEP 562 ``__getattr__`` trick does not fire for the
    module's own global lookups, so a naive rewrite raises :exc:`NameError`.
    """

    COMPOUND_LINE = "COMPOUND_LINE"
    """The physical line carries more than one statement (``import a; import b``).

    Rewriting one of them would have to re-lay-out the whole line, which
    conflicts with preserving untouched source byte-for-byte.
    """

    ALREADY_LAZY = "ALREADY_LAZY"
    """One of the statement's bound names is already bound lazily.

    Detected by CST node type (:class:`libcst.LazyImport` /
    :class:`libcst.LazyImportFrom`) and by the fallback binding's shape —
    never through ``ScopeProvider``, which records no assignment for a lazy
    import and would therefore call every converted file unconverted.
    """

    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    """The line no longer holds the statement the plan recorded there.

    The file changed after the plan was written (or shifted under an earlier
    ``apply``).  Converting whatever now sits on that line would rewrite a
    statement nobody proved safe.
    """

    NOT_FOUND = "NOT_FOUND"
    """The plan entry does not point at an import statement at all."""


class FlagCode(StrEnum):
    """An advisory the dry run raises about an otherwise convertible statement."""

    MODULE_LEVEL_CALL = "MODULE_LEVEL_CALL"
    """A module-level call reaches this name through a function of this module.

    The ``MODULE_LEVEL_USE`` rule only sees names read *directly* while the
    module executes.  When the module instead calls one of its own functions at
    import time, and that function reads the name, the proxy reifies during
    import anyway and the conversion saves nothing.  Analysing this properly is
    tracked in issue #17; until then the statement is converted and flagged.
    """


@dataclass(frozen=True, slots=True)
class ApplyOptions:
    """Knobs that shape a conversion.

    Attributes:
        target_version: Interpreter the converted source must run on. The
            default emits native PEP 810 syntax; anything in
            :data:`FALLBACK_TARGET_VERSIONS` selects the fallback emitter.
        write: Write the converted files to disk. False — the default — leaves
            every file untouched and only reports the diff.
    """

    target_version: str = NATIVE_TARGET_VERSION
    write: bool = False

    def __post_init__(self) -> None:
        """Reject a target version this codemod has no emitter for.

        Raises:
            ValueError: ``target_version`` is not in :data:`TARGET_VERSIONS`.
        """
        if self.target_version not in TARGET_VERSIONS:
            supported = ", ".join(TARGET_VERSIONS)
            msg = (
                f"unsupported --target-version {self.target_version!r}; "
                f"expected one of {supported}"
            )
            raise ValueError(msg)

    @property
    def is_native(self) -> bool:
        """True when PEP 810 ``lazy`` syntax may be emitted."""
        return self.target_version == NATIVE_TARGET_VERSION


@dataclass(frozen=True, slots=True)
class ApplyEntry:
    """One safe plan statement and what the codemod did with it.

    Attributes:
        key: ``file:line``, the same key the plan and attribution tables use.
        status: Converted, or skipped.
        reason: Why it was skipped; ``None`` for a converted statement.
        flags: Advisories that do not block conversion.
        display_path: Source file, relative to the plan's source root.
        lineno: 1-based line number the plan recorded.
        before: The statement as it was, when one was found there.
        after: The statement as emitted; ``None`` when nothing was converted.
    """

    key: str
    status: ApplyStatus
    reason: ApplyCode | None = None
    flags: tuple[FlagCode, ...] = ()
    display_path: str | None = None
    lineno: int | None = None
    before: str | None = None
    after: str | None = None

    @property
    def is_converted(self) -> bool:
        """True when this statement was rewritten."""
        return self.status is ApplyStatus.CONVERTED


@dataclass(frozen=True, slots=True)
class FileEdit:
    """One file's text before and after conversion.

    Attributes:
        path: Absolute path of the source file.
        display_path: Path shown to users, relative to the source root.
        before: The file as it was read.
        after: The file as the codemod would write it.
    """

    path: Path
    display_path: str
    before: str
    after: str

    @property
    def is_changed(self) -> bool:
        """True when conversion produced different text."""
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Everything one ``importbudget apply`` run produced.

    Attributes:
        entries: Every safe plan statement, in plan order.
        edits: One per file the plan pointed at, changed or not.
        target_version: The interpreter the emitted code targets.
        is_native: True when native PEP 810 syntax was emitted.
        written: True when the edits were written to disk.
        plan_path: The plan document the run consumed.
        source_root: Directory the reported source paths are relative to.
        warnings: importbudget's own diagnostics from the run.
    """

    entries: tuple[ApplyEntry, ...]
    edits: tuple[FileEdit, ...]
    target_version: str
    is_native: bool
    written: bool
    plan_path: str
    source_root: Path | None = None
    warnings: tuple[str, ...] = ()

    def converted(self) -> tuple[ApplyEntry, ...]:
        """Return the statements that were rewritten."""
        return tuple(e for e in self.entries if e.is_converted)

    def skipped(self) -> tuple[ApplyEntry, ...]:
        """Return the statements that were left alone, each with its reason."""
        return tuple(e for e in self.entries if not e.is_converted)

    def flagged(self) -> tuple[ApplyEntry, ...]:
        """Return the statements carrying at least one advisory."""
        return tuple(e for e in self.entries if e.flags)

    def changed_edits(self) -> tuple[FileEdit, ...]:
        """Return only the files whose text conversion actually changed."""
        return tuple(edit for edit in self.edits if edit.is_changed)
