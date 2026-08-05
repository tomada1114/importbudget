"""Render a profile as a human table or as the stable JSON contract.

The JSON document is what the later ``plan`` stage consumes, so it is
versioned with :data:`SCHEMA_VERSION` and documented here:

.. code-block:: text

    {
      "schema_version": 1,
      "document":    "profile",
      "tool":        {"name", "version"},
      "entrypoint":  {"target", "kind", "source_root"},
      "environment": {"python_version", "python_executable", "platform"},
      "measurement": {"runs", "warmup_runs", "returncodes",
                      "measured_us", "filtered_baseline_us",
                      "attributed_us", "attributed_share"},
      "statements": [
        {"key", "kind", "file", "line", "module", "source",
         "self_us", "self_ms", "cumulative_us", "cumulative_ms",
         "share", "modules": [...]}
      ],
      "warnings": ["..."],
      "stderr":   {"lines": ["..."], "suppressed": 0}
    }

Microseconds are canonical (integers, exactly as CPython reported them); the
millisecond fields are rounded conveniences and ``share`` is the row's fraction
of ``attributed_us``.  ``self_us`` sums to
``attributed_us`` across all rows, while ``cumulative_us`` is advisory: it is
the cost of everything the statement pulls in, and neighbouring rows overlap,
so summing that column double-counts.

``warnings`` holds importbudget's own diagnostics only.  Whatever the profiled
program wrote to its own stderr lands in ``stderr`` instead, deduplicated and
capped at :data:`~importbudget.stderr.MAX_STDERR_LINES` distinct lines with
the remainder counted in ``suppressed`` — otherwise any entrypoint using
``logging`` (which writes to stderr by default) buries the report in its own
output.  This channel split is why :data:`SCHEMA_VERSION` still reads 1: no
version of the document has shipped yet, so there is nothing to migrate.

``document`` names the document *kind*, so that ``plan`` (which emits its own
document under the same ``schema_version``) can reject the wrong input rather
than half-parse it.  It is purely additive: a reader that finds no ``document``
key alongside a ``schema_version`` of 1 is looking at a profile.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .attribute import AttributionKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .attribute import Attribution
    from .profiler import ProfileResult
    from .stderr import ForeignStderr

__all__ = [
    "APPLY_DOCUMENT",
    "PLAN_DOCUMENT",
    "PROFILE_DOCUMENT",
    "SCHEMA_VERSION",
    "render_json",
    "render_table",
    "to_json_dict",
]

#: Version of the JSON documents emitted by importbudget.
SCHEMA_VERSION = 1

#: ``document`` discriminator of the profile document.
PROFILE_DOCUMENT = "profile"

#: ``document`` discriminator of the plan document.
PLAN_DOCUMENT = "plan"

#: ``document`` discriminator of the apply document.
APPLY_DOCUMENT = "apply"

_US_PER_MS = 1000.0
_MAX_SOURCE_WIDTH = 48
_ADVISORY = (
    'the "potential" column is the full cost of everything a statement pulls '
    "in; rows overlap, so it must never be summed"
)


def to_json_dict(result: ProfileResult) -> dict[str, Any]:
    """Build the JSON document for a profile.

    Args:
        result: Profile to serialize. All rows are included, regardless of any
            display limit, because this document is the machine contract.

    Returns:
        A JSON-serializable mapping following :data:`SCHEMA_VERSION`.
    """
    measurement = result.measurement
    attribution = result.attribution
    total = attribution.attributed_us
    return {
        "schema_version": SCHEMA_VERSION,
        "document": PROFILE_DOCUMENT,
        "tool": {"name": "importbudget", "version": __version__},
        "entrypoint": {
            "target": result.entrypoint.target,
            "kind": str(result.entrypoint.kind),
            "source_root": str(result.source_root) if result.source_root else None,
        },
        "environment": {
            "python_version": measurement.python_version,
            "python_executable": measurement.python_executable,
            "platform": measurement.platform,
        },
        "measurement": _measurement_to_json(result),
        "statements": [_row_to_json(row, total) for row in attribution.rows],
        "warnings": list(result.warnings),
        "stderr": {
            "lines": list(result.stderr.lines),
            "suppressed": result.stderr.suppressed,
        },
    }


def _measurement_to_json(result: ProfileResult) -> dict[str, Any]:
    """Serialize the totals block, including the share of the measured time."""
    measurement = result.measurement
    attribution = result.attribution
    total = attribution.attributed_us
    return {
        "runs": measurement.runs,
        "warmup_runs": measurement.warmup_runs,
        "returncodes": list(measurement.returncodes),
        "measured_us": attribution.measured_us,
        "filtered_baseline_us": attribution.filtered_us,
        "attributed_us": total,
        "attributed_share": _share(total, attribution.net_measured_us),
    }


def render_json(result: ProfileResult, *, indent: int | None = 2) -> str:
    """Serialize a profile as JSON text.

    Args:
        result: Profile to serialize.
        indent: Indentation passed to :func:`json.dumps`.

    Returns:
        The JSON document as text.
    """
    return json.dumps(to_json_dict(result), indent=indent)


def render_table(result: ProfileResult, *, top: int = 10) -> str:
    """Render the human-readable attribution table.

    Args:
        result: Profile to render.
        top: Number of statements to show; non-positive means all of them.

    Returns:
        The report as text, without a trailing newline.
    """
    rows = result.top(top)
    total = result.attribution.attributed_us
    lines = [*_header_lines(result), ""]
    lines.extend(_table_lines(rows, total))
    lines.extend(["", *_footer_lines(result)])
    return "\n".join(lines)


def _header_lines(result: ProfileResult) -> list[str]:
    """Return the title block naming what was measured and how."""
    measurement = result.measurement
    runs = f"mean of {measurement.runs} run(s)"
    if measurement.warmup_runs:
        runs += f", {measurement.warmup_runs} discarded"
    return [
        f"importbudget profile: {result.entrypoint.target} ({result.entrypoint.kind})",
        f"python {measurement.python_version} on {measurement.platform} - {runs}",
    ]


def _table_lines(rows: Sequence[Attribution], total_us: int) -> list[str]:
    """Return the header, separator and one line per attribution row."""
    if not rows:
        return ["(no import statements were attributed)"]

    key_width = max(len("statement"), *(len(row.key) for row in rows))
    header = (
        f"{'self ms':>9}  {'share':>6}  {'potential':>10}  "
        f"{'statement':<{key_width}}  source"
    )
    lines = [header, "-" * len(header)]
    lines.extend(
        f"{row.self_us / _US_PER_MS:9.2f}  "
        f"{_share(row.self_us, total_us):>6.1%}  "
        f"{row.cumulative_us / _US_PER_MS:10.2f}  "
        f"{row.key:<{key_width}}  {_truncate(row.source)}".rstrip()
        for row in rows
    )
    return lines


def _footer_lines(result: ProfileResult) -> list[str]:
    """Return the totals, the advisory note, the warnings and program stderr."""
    attribution = result.attribution
    net = attribution.net_measured_us
    lines = [
        f"attributed {attribution.attributed_us / _US_PER_MS:.2f} ms of "
        f"{net / _US_PER_MS:.2f} ms measured "
        f"({_share(attribution.attributed_us, net):.1%}); "
        f"{attribution.filtered_us / _US_PER_MS:.2f} ms interpreter startup filtered",
        f"note: {_ADVISORY}",
    ]
    dynamic = sum(
        row.self_us for row in attribution.rows if row.kind is AttributionKind.DYNAMIC
    )
    if dynamic:
        lines.append(
            f"note: {dynamic / _US_PER_MS:.2f} ms came from dynamic imports that no "
            f"static statement explains"
        )
    lines.append(f"{len(attribution.rows)} statement(s) attributed")
    if result.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    lines.extend(_stderr_lines(result.stderr))
    return lines


def _stderr_lines(stderr: ForeignStderr) -> list[str]:
    """Return the profiled program's own stderr, clearly labelled as its own."""
    if not stderr:
        return []
    lines = ["entrypoint stderr:"]
    lines.extend(f"  - {line}" for line in stderr.lines)
    if stderr.suppressed:
        lines.append(f"  ... {stderr.suppressed} more distinct line(s) suppressed")
    return lines


def _row_to_json(row: Attribution, total_us: int) -> dict[str, Any]:
    """Serialize one attribution row."""
    return {
        "key": row.key,
        "kind": str(row.kind),
        "file": row.display_path,
        "line": row.lineno,
        "module": row.owner,
        "source": row.source,
        "self_us": row.self_us,
        "self_ms": round(row.self_us / _US_PER_MS, 3),
        "cumulative_us": row.cumulative_us,
        "cumulative_ms": round(row.cumulative_us / _US_PER_MS, 3),
        "share": round(_share(row.self_us, total_us), 6),
        "modules": list(row.modules),
    }


def _share(value_us: int, total_us: int) -> float:
    """Return ``value / total``, or 0.0 when the total is not positive."""
    if total_us <= 0:
        return 0.0
    return value_us / total_us


def _truncate(source: str | None) -> str:
    """Shorten a source line so the table stays readable."""
    if not source:
        return ""
    if len(source) <= _MAX_SOURCE_WIDTH:
        return source
    return source[: _MAX_SOURCE_WIDTH - 1] + "…"
