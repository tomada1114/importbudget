"""Render a conversion as a unified diff, a human table, or the JSON contract.

The apply document shares :data:`~importbudget.report.SCHEMA_VERSION` with the
profile and plan documents and is told apart by the ``document`` discriminator:

.. code-block:: text

    {
      "schema_version": 1,
      "document":  "apply",
      "tool":      {"name", "version"},
      "plan":      {"path", "source_root"},
      "options":   {"target_version", "native", "write"},
      "statements": [
        {"key", "file", "line", "status": "converted" | "skipped",
         "reason": "UNSUPPORTED_FORM" | ... | null,
         "flags": ["MODULE_LEVEL_CALL"], "before", "after"}
      ],
      "files":  [{"file", "changed"}],
      "diff":   "--- a/... ",
      "totals": {"converted_count", "skipped_count", "flagged_count",
                 "file_count", "changed_file_count"},
      "notes":    ["..."],
      "warnings": ["..."]
    }

Every statement the plan proved safe appears exactly once, converted or not,
with ``reason`` carrying the machine-readable code for the ones left alone.
``flags`` is advisory: those statements *were* converted, and the flag says the
saving may not materialize.
"""

from __future__ import annotations

import difflib
import json
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .report import APPLY_DOCUMENT, SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .applies import ApplyEntry, ApplyResult

__all__ = [
    "render_apply_diff",
    "render_apply_json",
    "render_apply_table",
    "to_apply_json_dict",
]

_TEST_SUITE_NUDGE = (
    "run your project's test suite before committing. A lazy import defers "
    "the target module's import-time side effects and moves any ImportError "
    "to the first use of the name (PEP 810 S11/S13), which no static rule can "
    "prove harmless"
)
_DRY_RUN_NOTE = "dry run: nothing was written. Re-run with --write to apply"
_GAP_NOTE = (
    "MODULE_LEVEL_CALL marks a converted statement whose name is reached by a "
    "function this module calls while importing, so the proxy reifies anyway "
    "and the saving is likely zero. The conversion is still safe; measuring "
    "this properly is tracked in issue #17"
)


def to_apply_json_dict(result: ApplyResult) -> dict[str, Any]:
    """Build the JSON document for a conversion.

    Args:
        result: Conversion to serialize. Every safe statement is included,
            converted or not, because this document is the machine contract.

    Returns:
        A JSON-serializable mapping following :data:`SCHEMA_VERSION`.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "document": APPLY_DOCUMENT,
        "tool": {"name": "importbudget", "version": __version__},
        "plan": {
            "path": result.plan_path,
            "source_root": str(result.source_root) if result.source_root else None,
        },
        "options": {
            "target_version": result.target_version,
            "native": result.is_native,
            "write": result.written,
        },
        "statements": [_entry_to_json(entry) for entry in result.entries],
        "files": [
            {"file": edit.display_path, "changed": edit.is_changed}
            for edit in result.edits
        ],
        "diff": render_apply_diff(result),
        "totals": _totals_to_json(result),
        "notes": _notes(result),
        "warnings": list(result.warnings),
    }


def render_apply_json(result: ApplyResult, *, indent: int | None = 2) -> str:
    """Serialize a conversion as JSON text.

    Args:
        result: Conversion to serialize.
        indent: Indentation passed to :func:`json.dumps`.

    Returns:
        The JSON document as text.
    """
    return json.dumps(to_apply_json_dict(result), indent=indent)


def render_apply_diff(result: ApplyResult) -> str:
    """Render every changed file as one unified diff.

    Args:
        result: Conversion to render.

    Returns:
        The diff as text, empty when nothing changed.
    """
    chunks = [
        "".join(
            difflib.unified_diff(
                edit.before.splitlines(keepends=True),
                edit.after.splitlines(keepends=True),
                fromfile=f"a/{edit.display_path}",
                tofile=f"b/{edit.display_path}",
            )
        )
        for edit in result.changed_edits()
    ]
    return "".join(chunks).rstrip("\n")


def render_apply_table(result: ApplyResult) -> str:
    """Render the human-readable conversion report.

    Args:
        result: Conversion to render.

    Returns:
        The report as text, without a trailing newline.
    """
    lines = [*_header_lines(result), ""]
    diff = render_apply_diff(result)
    if diff:
        lines.extend([diff, ""])
    lines.extend(_converted_section(result.converted()))
    lines.append("")
    lines.extend(_skipped_section(result.skipped()))
    flagged = result.flagged()
    if flagged:
        lines.extend(["", *_flagged_section(flagged)])
    lines.extend(["", *_footer_lines(result)])
    return "\n".join(lines)


def _header_lines(result: ApplyResult) -> list[str]:
    """Return the title block naming the plan and the emitter that was used."""
    emitter = (
        "native PEP 810 lazy syntax"
        if result.is_native
        else "importlib.util.LazyLoader fallback"
    )
    return [
        f"importbudget apply: {result.plan_path}",
        f"target python {result.target_version} - {emitter}",
    ]


def _converted_section(entries: Sequence[ApplyEntry]) -> list[str]:
    """Return the block listing what was rewritten."""
    heading = f"converted ({len(entries)} statement(s)):"
    if not entries:
        return [heading, "  (none)"]
    return [
        heading,
        *(f"  {entry.key}  {entry.before}  ->  {entry.after}" for entry in entries),
    ]


def _skipped_section(entries: Sequence[ApplyEntry]) -> list[str]:
    """Return the block listing what was left alone, and why."""
    heading = f"skipped ({len(entries)} statement(s)):"
    if not entries:
        return [heading, "  (none)"]
    width = max(len(str(entry.reason)) for entry in entries)
    return [
        heading,
        *(
            f"  {entry.reason!s:<{width}}  {entry.key}  {entry.before}"
            for entry in entries
        ),
    ]


def _flagged_section(entries: Sequence[ApplyEntry]) -> list[str]:
    """Return the block listing converted statements carrying an advisory."""
    return [
        f"flagged ({len(entries)} statement(s), converted anyway):",
        *(
            f"  {','.join(str(flag) for flag in entry.flags)}  "
            f"{entry.key}  {entry.before}"
            for entry in entries
        ),
    ]


def _footer_lines(result: ApplyResult) -> list[str]:
    """Return the one-line summary, the caveats and any warnings."""
    changed = len(result.changed_edits())
    lines = [
        f"{len(result.converted())} converted / {len(result.skipped())} skipped "
        f"across {len(result.edits)} file(s), {changed} changed",
    ]
    lines.extend(f"note: {note}" for note in _notes(result))
    if result.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    return lines


def _notes(result: ApplyResult) -> list[str]:
    """Return the caveats this particular run earned."""
    notes: list[str] = []
    if result.flagged():
        notes.append(_GAP_NOTE)
    if not result.written:
        notes.append(_DRY_RUN_NOTE)
    elif result.converted():
        notes.append(_TEST_SUITE_NUDGE)
    return notes


def _entry_to_json(entry: ApplyEntry) -> dict[str, Any]:
    """Serialize one statement's outcome."""
    return {
        "key": entry.key,
        "file": entry.display_path,
        "line": entry.lineno,
        "status": str(entry.status),
        "reason": str(entry.reason) if entry.reason else None,
        "flags": [str(flag) for flag in entry.flags],
        "before": entry.before,
        "after": entry.after,
    }


def _totals_to_json(result: ApplyResult) -> dict[str, Any]:
    """Serialize the totals block."""
    return {
        "converted_count": len(result.converted()),
        "skipped_count": len(result.skipped()),
        "flagged_count": len(result.flagged()),
        "file_count": len(result.edits),
        "changed_file_count": len(result.changed_edits()),
    }
