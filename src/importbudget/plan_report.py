"""Render a plan as a human table or as the versioned JSON contract.

The plan document shares :data:`~importbudget.report.SCHEMA_VERSION` with the
profile document and is told apart by the ``document`` discriminator:

.. code-block:: text

    {
      "schema_version": 1,
      "document":    "plan",
      "tool":        {"name", "version"},
      "entrypoint":  {"target", "kind", "source_root"},
      "environment": {"python_version", "platform"},
      "profile":     {"origin", "runs", "warmup_runs", "measured_us",
                      "filtered_baseline_us", "attributed_us"},
      "options":     {"min_us"},
      "statements": [
        {"key", "file", "line", "module", "source", "bound_names": [...],
         "verdict": "safe" | "excluded",
         "status":  "proposed" | "excluded" | "below_threshold",
         "reasons": [{"code", "message"}],
         "self_us", "self_ms", "cumulative_us", "cumulative_ms"}
      ],
      "totals": {"predicted_saving_us", "predicted_saving_ms", "safe_count",
                 "excluded_count", "below_threshold_count", "candidate_count",
                 "attributed_us", "unaddressable_us"},
      "notes":    ["..."],
      "warnings": ["..."]
    }

``verdict`` is the safety answer and ``status`` is the plan's decision; they
differ exactly for a safe statement that ``--min-ms`` dropped, which is why
both are carried rather than one.  ``origin`` is ``"measured"`` or the path of
the consumed profile document, so a plan says where its numbers came from.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .report import PLAN_DOCUMENT, SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .plans import PlanEntry, PlanResult

__all__ = ["render_plan_json", "render_plan_table", "to_plan_json_dict"]

_US_PER_MS = 1000.0
_MAX_SOURCE_WIDTH = 40

_PREDICTION_NOTE = (
    "the predicted saving is an upper bound. It is set-dependent (a module is "
    "paid for once, by whichever statement imports it first, so converting "
    "several statements saves less than the sum of their rows), and a "
    "statement whose name is reached by a function the module itself calls "
    "while importing reifies anyway, saving nothing. Treat it as a ranking, "
    "and measure the real difference after converting"
)
_WHITELIST_NOTE = (
    "excluded does not mean unsafe, it means not proven safe: importbudget "
    "refuses whenever a rule cannot decide"
)


def to_plan_json_dict(result: PlanResult) -> dict[str, Any]:
    """Build the JSON document for a plan.

    Args:
        result: Plan to serialize. Every statement is included regardless of
            any display limit, because this document is the machine contract.

    Returns:
        A JSON-serializable mapping following :data:`SCHEMA_VERSION`.
    """
    summary = result.profile
    return {
        "schema_version": SCHEMA_VERSION,
        "document": PLAN_DOCUMENT,
        "tool": {"name": "importbudget", "version": __version__},
        "entrypoint": {
            "target": summary.target,
            "kind": summary.kind,
            "source_root": str(result.source_root) if result.source_root else None,
        },
        "environment": {
            "python_version": summary.python_version,
            "platform": summary.platform,
        },
        "profile": {
            "origin": summary.origin,
            "runs": summary.runs,
            "warmup_runs": summary.warmup_runs,
            "measured_us": summary.measured_us,
            "filtered_baseline_us": summary.filtered_us,
            "attributed_us": summary.attributed_us,
        },
        "options": {"min_us": result.min_us},
        "statements": [_entry_to_json(entry) for entry in result.entries],
        "totals": _totals_to_json(result),
        "notes": [_PREDICTION_NOTE, _WHITELIST_NOTE],
        "warnings": list(result.warnings),
    }


def render_plan_json(result: PlanResult, *, indent: int | None = 2) -> str:
    """Serialize a plan as JSON text.

    Args:
        result: Plan to serialize.
        indent: Indentation passed to :func:`json.dumps`.

    Returns:
        The JSON document as text.
    """
    return json.dumps(to_plan_json_dict(result), indent=indent)


def render_plan_table(result: PlanResult, *, top: int = 10) -> str:
    """Render the human-readable plan.

    Args:
        result: Plan to render.
        top: Statements to show per section; non-positive means all of them.

    Returns:
        The report as text, without a trailing newline.
    """
    lines = [*_header_lines(result), ""]
    lines.extend(
        _section("proposed - proved safe to make lazy", result.proposed(), top)
    )
    lines.append("")
    lines.extend(_section("excluded - not proven safe", result.excluded(), top))
    below = result.below_threshold()
    if below:
        lines.append("")
        lines.extend(
            _section(f"below --min-ms {result.min_us / _US_PER_MS:g}", below, top)
        )
    lines.extend(["", *_footer_lines(result)])
    return "\n".join(lines)


def _header_lines(result: PlanResult) -> list[str]:
    """Return the title block naming what was planned and where it came from."""
    summary = result.profile
    runs = f"mean of {summary.runs} run(s)"
    if summary.warmup_runs:
        runs += f", {summary.warmup_runs} discarded"
    return [
        f"importbudget plan: {summary.target} ({summary.kind})",
        f"python {summary.python_version} on {summary.platform} - {runs}",
        f"profile source: {summary.origin}",
    ]


def _section(title: str, entries: Sequence[PlanEntry], top: int) -> list[str]:
    """Return one titled block of the table."""
    total_us = sum(entry.self_us for entry in entries)
    heading = f"{title} ({len(entries)} statement(s), {total_us / _US_PER_MS:.2f} ms):"
    if not entries:
        return [heading, "  (none)"]
    shown = entries if top <= 0 else entries[:top]
    width = max(len("statement"), *(len(entry.key) for entry in shown))
    lines = [heading, f"  {'self ms':>9}  {'statement':<{width}}  detail"]
    lines.extend(
        f"  {entry.self_us / _US_PER_MS:9.2f}  {entry.key:<{width}}  "
        f"{_detail(entry)}".rstrip()
        for entry in shown
    )
    if len(shown) < len(entries):
        lines.append(f"  ... {len(entries) - len(shown)} more (raise --top to see)")
    return lines


def _detail(entry: PlanEntry) -> str:
    """Return the right-hand column: reason codes for excluded rows, else source."""
    source = _truncate(entry.source)
    if not entry.reasons:
        return source
    codes = ",".join(str(code) for code in entry.codes)
    return f"{codes}  {source}".rstrip()


def _footer_lines(result: PlanResult) -> list[str]:
    """Return the one-line summary, the caveats and any warnings."""
    totals = result.totals
    lines = [
        f"{len(result.proposed())} proposed / {totals.excluded_count} excluded / "
        f"{totals.below_threshold_count} below threshold; predicted saving "
        f"{totals.predicted_saving_us / _US_PER_MS:.2f} ms of "
        f"{totals.attributed_us / _US_PER_MS:.2f} ms attributed",
    ]
    if totals.unaddressable_us:
        lines.append(
            f"note: {totals.unaddressable_us / _US_PER_MS:.2f} ms sits on the "
            f"entrypoint itself or on dynamic imports, which no statement "
            f"conversion can remove"
        )
    lines.append(f"note: {_PREDICTION_NOTE}")
    lines.append(f"note: {_WHITELIST_NOTE}")
    if result.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    return lines


def _entry_to_json(entry: PlanEntry) -> dict[str, Any]:
    """Serialize one plan entry."""
    return {
        "key": entry.key,
        "file": entry.display_path,
        "line": entry.lineno,
        "module": entry.module,
        "source": entry.source,
        "bound_names": list(entry.bound_names),
        "verdict": "safe" if entry.is_safe else "excluded",
        "status": str(entry.status),
        "reasons": [
            {"code": str(reason.code), "message": reason.message}
            for reason in entry.reasons
        ],
        "self_us": entry.self_us,
        "self_ms": round(entry.self_us / _US_PER_MS, 3),
        "cumulative_us": entry.cumulative_us,
        "cumulative_ms": round(entry.cumulative_us / _US_PER_MS, 3),
    }


def _truncate(source: str | None) -> str:
    """Shorten a source line so the table stays readable."""
    if not source:
        return ""
    if len(source) <= _MAX_SOURCE_WIDTH:
        return source
    return source[: _MAX_SOURCE_WIDTH - 1] + "…"


def _totals_to_json(result: PlanResult) -> dict[str, Any]:
    """Serialize the totals block."""
    totals = result.totals
    return {
        "predicted_saving_us": totals.predicted_saving_us,
        "predicted_saving_ms": round(totals.predicted_saving_us / _US_PER_MS, 3),
        "safe_count": totals.safe_count,
        "excluded_count": totals.excluded_count,
        "below_threshold_count": totals.below_threshold_count,
        "candidate_count": totals.candidate_count,
        "attributed_us": totals.attributed_us,
        "unaddressable_us": totals.unaddressable_us,
    }
