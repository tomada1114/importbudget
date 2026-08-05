"""Render a budget check as a human summary or as the versioned JSON contract.

The check document shares :data:`~importbudget.report.SCHEMA_VERSION` with the
profile, plan and apply documents and is told apart by the ``document``
discriminator:

.. code-block:: text

    {
      "schema_version": 1,
      "document":    "check",
      "tool":        {"name", "version"},
      "entrypoint":  {"target", "kind"},
      "environment": {"python_version", "platform"},
      "measurement": {"runs", "warmup_runs", "returncodes",
                      "measured_us", "filtered_baseline_us"},
      "budget":      {"max_us", "max_ms"},
      "cost_us", "cost_ms", "headroom_us", "headroom_ms",
      "outcome":   "within" | "over" | "failed",
      "exit_code": 0 | 1 | 2,
      "failure":   null | "...",
      "warnings":  ["..."],
      "stderr":    {"lines": ["..."], "suppressed": 0}
    }

``cost_us`` is the measured total minus interpreter bootstrap, and it is the
one number the budget is compared against; it is ``null`` exactly when
``outcome`` is ``"failed"``, so a consumer never has to read a zero as a
measurement.  ``exit_code`` is carried in the document as well as returned by
the process, so a job that captures the JSON does not have to re-derive it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .budgets import CheckOutcome
from .report import CHECK_DOCUMENT, SCHEMA_VERSION

if TYPE_CHECKING:
    from .budgets import CheckResult

__all__ = ["render_check_json", "render_check_table", "to_check_json_dict"]

_US_PER_MS = 1000.0

_FAILURE_NOTE = "the budget was never tested: this is neither a pass nor a regression"


def to_check_json_dict(result: CheckResult) -> dict[str, Any]:
    """Build the JSON document for a budget check.

    Args:
        result: Verdict to serialize.

    Returns:
        A JSON-serializable mapping following :data:`SCHEMA_VERSION`.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "document": CHECK_DOCUMENT,
        "tool": {"name": "importbudget", "version": __version__},
        "entrypoint": {"target": result.target, "kind": result.kind},
        "environment": {
            "python_version": result.python_version,
            "platform": result.platform,
        },
        "measurement": {
            "runs": result.runs,
            "warmup_runs": result.warmup_runs,
            "returncodes": list(result.returncodes),
            "measured_us": result.measured_us,
            "filtered_baseline_us": result.filtered_us,
        },
        "budget": {"max_us": result.budget.us, "max_ms": result.budget.ms},
        "cost_us": result.cost_us,
        "cost_ms": _ms(result.cost_us),
        "headroom_us": result.headroom_us,
        "headroom_ms": _ms(result.headroom_us),
        "outcome": str(result.outcome),
        "exit_code": result.exit_code,
        "failure": result.failure,
        "warnings": list(result.warnings),
        "stderr": {
            "lines": list(result.stderr.lines),
            "suppressed": result.stderr.suppressed,
        },
    }


def render_check_json(result: CheckResult, *, indent: int | None = 2) -> str:
    """Serialize a budget check as JSON text.

    Args:
        result: Verdict to serialize.
        indent: Indentation passed to :func:`json.dumps`.

    Returns:
        The JSON document as text.
    """
    return json.dumps(to_check_json_dict(result), indent=indent)


def render_check_table(result: CheckResult) -> str:
    """Render the human-readable budget verdict.

    Args:
        result: Verdict to render.

    Returns:
        The report as text, without a trailing newline.
    """
    lines = [*_header_lines(result), "", *_verdict_lines(result)]
    if result.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    lines.extend(_stderr_lines(result))
    return "\n".join(lines)


def _header_lines(result: CheckResult) -> list[str]:
    """Return the title block naming what was measured and how."""
    where = f"{result.target} ({result.kind})" if result.kind else result.target
    runs = f"mean of {result.runs} run(s)"
    if result.warmup_runs:
        runs += f", {result.warmup_runs} discarded"
    environment = (
        f"python {result.python_version} on {result.platform} - {runs}"
        if result.python_version
        else runs
    )
    return [f"importbudget check: {where}", environment]


def _verdict_lines(result: CheckResult) -> list[str]:
    """Return the one-line answer, in the wording each outcome deserves."""
    budget = f"budget {result.budget.ms:.2f} ms"
    if result.outcome is CheckOutcome.FAILED:
        return [
            f"could not measure {result.target}: {result.failure}",
            f"note: {_FAILURE_NOTE} ({budget} was not applied)",
        ]

    # cost_us and headroom_us are only None for a FAILED outcome, handled above.
    cost_us = result.cost_us or 0
    headroom_us = result.headroom_us or 0
    cost = f"import cost {cost_us / _US_PER_MS:.2f} ms"
    if result.outcome is CheckOutcome.OVER:
        return [f"{cost}, {budget} - OVER BUDGET by {-headroom_us / _US_PER_MS:.2f} ms"]
    return [
        f"{cost}, {budget} - within budget ({headroom_us / _US_PER_MS:.2f} ms to spare)"
    ]


def _stderr_lines(result: CheckResult) -> list[str]:
    """Return the measured program's own stderr, clearly labelled as its own."""
    stderr = result.stderr
    if not stderr:
        return []
    lines = ["entrypoint stderr:"]
    lines.extend(f"  - {line}" for line in stderr.lines)
    if stderr.suppressed:
        lines.append(f"  ... {stderr.suppressed} more distinct line(s) suppressed")
    return lines


def _ms(value_us: int | None) -> float | None:
    """Convert microseconds to milliseconds, keeping ``None`` as ``None``."""
    if value_us is None:
        return None
    return round(value_us / _US_PER_MS, 3)
