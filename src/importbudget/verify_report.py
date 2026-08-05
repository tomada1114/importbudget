"""Render a verification as a human summary or as the versioned JSON contract.

The verify document shares :data:`~importbudget.report.SCHEMA_VERSION` with the
profile, plan, apply and check documents and is told apart by the ``document``
discriminator:

.. code-block:: text

    {
      "schema_version": 1,
      "document":    "verify",
      "tool":        {"name", "version"},
      "entrypoint":  {"target", "kind"},
      "plan":        {"path", "target_version"},
      "environment": {"python_version", "platform"},
      "measurement": {"pairs", "warmup_pairs", "schedule": ["before", "after", ...]},
      "conversion":  {"converted_count", "skipped_count"},
      "raw":         {"kind", "pairs", "before_ms", "after_ms", "delta_ms",
                      "sd_ms", "noise_floor_ms", "significant", "improvement",
                      "reference", "samples"},
      "normalized":  null | {... same shape, "reference": "json" ...},
      "delta_ms", "sd_ms", "significant",
      "prediction":  {"predicted_ms", "measured_ms", "divergence",
                      "threshold", "divergence_warning"},
      "totals":      {"attributed_us", "unaddressable_us"},
      "notes":       ["..."],
      "warnings":    ["..."]
    }

The three flat keys ``delta_ms`` / ``sd_ms`` / ``significant`` are the answer a
CI script wants and repeat whichever of ``raw`` and ``normalized`` the verdict
rested on, so nothing has to re-implement that choice.  Both blocks are always
present (``normalized`` may be ``null``) because refusing a claim on raw totals
and then recovering it through normalization is a result worth reading in full,
not an implementation detail.

``schedule`` is the order the runs actually executed in, not the order they are
reported in: it is the evidence that the session was interleaved.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .report import SCHEMA_VERSION, VERIFY_DOCUMENT
from .verifies import SIGNIFICANCE_SIGMA

if TYPE_CHECKING:
    from .verifies import Comparison, VerifyResult

__all__ = ["render_verify_json", "render_verify_table", "to_verify_json_dict"]

_US_PER_MS = 1000.0

_PREDICTION_NOTE = (
    "the plan's predicted saving is a set-dependent upper bound, not a "
    "forecast: a module is paid for once, by whichever statement imports it "
    "first. This measurement, not that number, is the result"
)
_INTERLEAVE_NOTE = (
    "runs are paired and strictly interleaved, and the statistic is the mean "
    "of the per-pair differences, so a machine that drifts during the session "
    "moves both sides of a pair rather than one arm of the comparison"
)
_SIGMA_NOTE = (
    f"an improvement is claimed only when {SIGNIFICANCE_SIGMA:g} sigma is "
    f"strictly below the absolute delta; a delta sitting exactly on the noise "
    f"floor is reported as no result rather than as a small win"
)
_NO_REFERENCE_NOTE = (
    "no subtree stayed structurally identical across every run, so the delta "
    "could not be normalized. Small deltas may be invisible in raw totals"
)


def to_verify_json_dict(result: VerifyResult) -> dict[str, Any]:
    """Build the JSON document for a verification.

    Args:
        result: Verification to serialize. Every measured run is included,
            because this document is the machine contract.

    Returns:
        A JSON-serializable mapping following :data:`SCHEMA_VERSION`.
    """
    decisive = result.decisive
    return {
        "schema_version": SCHEMA_VERSION,
        "document": VERIFY_DOCUMENT,
        "tool": {"name": "importbudget", "version": __version__},
        "entrypoint": {"target": result.target, "kind": result.kind},
        "plan": {"path": result.plan_path, "target_version": result.target_version},
        "environment": {
            "python_version": result.python_version,
            "platform": result.platform,
        },
        "measurement": {
            "pairs": result.runs,
            "warmup_pairs": result.warmup_runs,
            "schedule": [str(side) for side in result.schedule],
        },
        "conversion": {
            "converted_count": result.converted_count,
            "skipped_count": result.skipped_count,
        },
        "raw": _comparison_to_json(result.raw),
        "normalized": (
            None
            if result.normalized is None
            else _comparison_to_json(result.normalized)
        ),
        "delta_ms": _ms(decisive.delta_us),
        "sd_ms": _ms(decisive.sd_us),
        "significant": decisive.is_significant,
        "prediction": _prediction_to_json(result),
        "totals": {
            "attributed_us": result.attributed_us,
            "unaddressable_us": result.unaddressable_us,
        },
        "notes": notes_for(result),
        "warnings": list(result.warnings),
    }


def render_verify_json(result: VerifyResult, *, indent: int | None = 2) -> str:
    """Serialize a verification as JSON text.

    Args:
        result: Verification to serialize.
        indent: Indentation passed to :func:`json.dumps`.

    Returns:
        The JSON document as text.
    """
    return json.dumps(to_verify_json_dict(result), indent=indent)


def render_verify_table(result: VerifyResult) -> str:
    """Render the human-readable verification.

    Args:
        result: Verification to render.

    Returns:
        The report as text, without a trailing newline.
    """
    lines = [*_header_lines(result), "", *_table_lines(result), ""]
    lines.append(_verdict_line(result))
    lines.extend(f"note: {note}" for note in notes_for(result))
    if result.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    return "\n".join(lines)


def notes_for(result: VerifyResult) -> list[str]:
    """Return the standing caveats this verification earned.

    Args:
        result: Verification to describe.

    Returns:
        The notes, in the order a reader should meet them.
    """
    notes = [_INTERLEAVE_NOTE, _SIGMA_NOTE, _PREDICTION_NOTE]
    if result.normalized is None:
        notes.append(_NO_REFERENCE_NOTE)
    else:
        notes.append(
            f"the normalized figures divide each run's total by the "
            f"{result.normalized.reference!r} subtree measured in that same "
            f"run, which the conversion left structurally identical"
        )
    if result.unaddressable_us:
        notes.append(
            f"{result.unaddressable_us / _US_PER_MS:.2f} ms of the profile sits "
            f"on the entrypoint itself or on dynamic imports; no conversion can "
            f"remove it, so it is the floor this delta was always working above"
        )
    return notes


def _header_lines(result: VerifyResult) -> list[str]:
    """Return the title block, ending with the schedule that was executed."""
    pairs = f"{result.runs} interleaved pair(s)"
    if result.warmup_runs:
        pairs += f", {result.warmup_runs} discarded"
    return [
        f"importbudget verify: {result.target} ({result.kind}) from {result.plan_path}",
        f"python {result.python_version} on {result.platform} - {pairs}",
        f"target python {result.target_version} - "
        f"{result.converted_count} converted / {result.skipped_count} skipped",
        f"schedule: {' '.join(str(side) for side in result.schedule)}",
    ]


def _table_lines(result: VerifyResult) -> list[str]:
    """Return one row per comparison, raw first and normalized below it."""
    header = (
        f"{'comparison':<11}  {'before':>10}  {'after':>10}  {'delta':>10}  "
        f"{'sd':>9}  {'3 sigma':>9}  verdict"
    )
    rows = [comparison for comparison in (result.raw, result.normalized) if comparison]
    return [header, "-" * len(header), *(_row(comparison) for comparison in rows)]


def _row(comparison: Comparison) -> str:
    """Return one comparison as a table row."""
    verdict = "significant" if comparison.is_significant else "not significant"
    return (
        f"{comparison.kind!s:<11}  "
        f"{comparison.before_us / _US_PER_MS:10.2f}  "
        f"{comparison.after_us / _US_PER_MS:10.2f}  "
        f"{comparison.delta_us / _US_PER_MS:+10.2f}  "
        f"{comparison.sd_us / _US_PER_MS:9.2f}  "
        f"{comparison.noise_floor_us / _US_PER_MS:9.2f}  {verdict}"
    )


def _verdict_line(result: VerifyResult) -> str:
    """Return the single sentence the whole command exists to print."""
    decisive = result.decisive
    delta = decisive.delta_us / _US_PER_MS
    spread = decisive.sd_us / _US_PER_MS
    if not decisive.is_significant:
        return (
            f"no significant change: {delta:+.2f} ms +/- {spread:.2f} ms does "
            f"not clear the {decisive.noise_floor_us / _US_PER_MS:.2f} ms noise "
            f"floor, so no improvement is claimed"
        )
    headline = (
        "verified improvement" if decisive.is_improvement else "verified regression"
    )
    return (
        f"{headline}: {delta:+.2f} ms +/- {spread:.2f} ms "
        f"({decisive.before_us / _US_PER_MS:.2f} ms -> "
        f"{decisive.after_us / _US_PER_MS:.2f} ms){_predicted_clause(result)}"
    )


def _predicted_clause(result: VerifyResult) -> str:
    """Return the trailing ``, predicted ...`` clause, when there is one."""
    divergence = result.divergence
    if divergence is None:
        return ""
    verdict = (
        f"diverges by {divergence:.0%}, over the "
        f"{result.divergence_threshold:.0%} threshold"
        if result.has_divergence_warning
        else f"within the {result.divergence_threshold:.0%} threshold"
    )
    return f", predicted {result.predicted_delta_ms:+.2f} ms ({verdict})"


def _comparison_to_json(comparison: Comparison) -> dict[str, Any]:
    """Serialize one comparison, samples included so the numbers are checkable."""
    return {
        "kind": str(comparison.kind),
        "pairs": comparison.pairs,
        "reference": comparison.reference,
        "before_us": round(comparison.before_us, 1),
        "before_ms": _ms(comparison.before_us),
        "after_us": round(comparison.after_us, 1),
        "after_ms": _ms(comparison.after_us),
        "delta_us": round(comparison.delta_us, 1),
        "delta_ms": _ms(comparison.delta_us),
        "sd_us": round(comparison.sd_us, 1),
        "sd_ms": _ms(comparison.sd_us),
        "noise_floor_us": round(comparison.noise_floor_us, 1),
        "noise_floor_ms": _ms(comparison.noise_floor_us),
        "significant": comparison.is_significant,
        "improvement": comparison.is_improvement,
        "samples": {
            "before_us": [round(value, 1) for value in comparison.before],
            "after_us": [round(value, 1) for value in comparison.after],
        },
    }


def _prediction_to_json(result: VerifyResult) -> dict[str, Any]:
    """Serialize the plan's prediction beside what was actually measured."""
    return {
        "predicted_us": -result.predicted_saving_us,
        "predicted_ms": round(result.predicted_delta_ms, 3),
        "measured_us": round(result.measured_delta_us, 1),
        "measured_ms": _ms(result.measured_delta_us),
        "divergence": (
            None if result.divergence is None else round(result.divergence, 6)
        ),
        "threshold": result.divergence_threshold,
        "divergence_warning": result.has_divergence_warning,
    }


def _ms(value_us: float) -> float:
    """Convert microseconds to milliseconds, rounded for display."""
    return round(value_us / _US_PER_MS, 3)
