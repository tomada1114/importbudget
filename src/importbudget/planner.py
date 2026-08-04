"""Join safety verdicts with measured cost into a conversion plan.

A plan answers one question: *which import statements can I safely make lazy,
and what would that buy me?*  It is the attribution table filtered through the
whitelist in :mod:`importbudget.analyze`, plus a threshold.

Only real statement rows are candidates.  ``<entrypoint>`` is the interpreter
and the entrypoint's own work — unremovable, and no statement to convert.
``<dynamic>`` rows have no statement either; worse, S1 measured that an
``importlib.import_module`` target emits no ``-X importtime`` row at all, so
its cost arrives through whatever the target imports normally.  Both are
summed into :attr:`PlanTotals.unaddressable_us` so the report can be honest
about what the plan does *not* cover rather than hiding it.

The predicted saving is **set-dependent** (plan 課題1): a module is paid for
once, by whichever statement imports it first, so converting a set of
statements does not save the sum of their attributed costs.  The prediction is
a ranking aid; the later ``verify`` stage measures reality.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._plan_input import load_profile_document
from .analyze import Analyzer
from .attribute import AttributionKind
from .plans import (
    PlanEntry,
    PlanOptions,
    PlanResult,
    PlanStatus,
    PlanTotals,
    ProfileSummary,
)
from .profiler import profile
from .rules import RuleCode, Violation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .analyze import Verdict
    from .attribute import Attribution
    from .entrypoints import Entrypoint

__all__ = ["plan", "plan_from_profile"]

_UNANALYZED_MESSAGE = (
    "the statement behind this cost could not be read back from source, so no "
    "safety rule ran; importbudget never proposes what it did not analyze"
)
_OUTSIDE_ROOT_WARNING = (
    "profile document names a source file outside its own source_root, so it "
    "was not read and its row is excluded: {path!r}"
)


def plan(
    entrypoint: Entrypoint | str,
    options: PlanOptions | None = None,
) -> PlanResult:
    """Profile an entrypoint, then plan which of its imports can be lazy.

    Args:
        entrypoint: Module name, script path, or a prepared
            :class:`~importbudget.entrypoints.Entrypoint`.
        options: Threshold and measurement knobs.

    Returns:
        The conversion plan.

    Raises:
        EntrypointError: The entrypoint's source could not be located.
        MeasurementError: The entrypoint produced unusable importtime output.
    """
    opts = options or PlanOptions()
    result = profile(entrypoint, opts.run)
    return _build_plan(
        rows=result.attribution.rows,
        summary=ProfileSummary.from_result(result),
        source_root=result.source_root,
        options=opts,
        warnings=result.warnings,
    )


def plan_from_profile(
    document: Path | str,
    options: PlanOptions | None = None,
) -> PlanResult:
    """Plan from a saved ``importbudget profile --json`` document.

    Nothing is re-measured, which makes a plan reproducible and cheap: the
    costs come from the document and only the source files it names are read
    back, so the entrypoint does not even have to be importable any more.

    Args:
        document: Path to the profile JSON.
        options: Threshold knobs; ``run`` is ignored.

    Returns:
        The conversion plan.

    Raises:
        PlanInputError: The file is missing, is not JSON, or is not a profile
            document of a supported schema version.
    """
    opts = options or PlanOptions()
    path = Path(document)
    summary, rows, source_root = load_profile_document(path)
    return _build_plan(
        rows=rows,
        summary=summary,
        source_root=source_root,
        options=opts,
        warnings=(),
    )


def _build_plan(
    *,
    rows: Sequence[Attribution],
    summary: ProfileSummary,
    source_root: Path | None,
    options: PlanOptions,
    warnings: tuple[str, ...],
) -> PlanResult:
    """Turn attribution rows into decided plan entries plus their totals."""
    analyzer = Analyzer(root=source_root)
    entries: list[PlanEntry] = []
    rejected: list[str] = []
    unaddressable_us = 0
    for row in rows:
        if row.kind is not AttributionKind.STATEMENT:
            unaddressable_us += row.self_us
            continue
        entries.append(_entry_for(row, analyzer, source_root, options.min_us, rejected))
    entries.sort(key=lambda entry: (-entry.self_us, entry.key))
    return PlanResult(
        profile=summary,
        entries=tuple(entries),
        totals=_totals(entries, summary, unaddressable_us),
        source_root=source_root,
        min_us=options.min_us,
        warnings=tuple(dict.fromkeys(warnings + analyzer.warnings + tuple(rejected))),
    )


def _entry_for(
    row: Attribution,
    analyzer: Analyzer,
    source_root: Path | None,
    min_us: int,
    rejected: list[str],
) -> PlanEntry:
    """Decide one costed statement row."""
    verdict = _verdict_for(row, analyzer, source_root, rejected)
    reasons: tuple[Violation, ...]
    names: tuple[str, ...]
    if verdict is None:
        reasons = (Violation(code=RuleCode.UNANALYZED, message=_UNANALYZED_MESSAGE),)
        names = ()
    else:
        reasons = verdict.violations
        names = verdict.bound_names
    return PlanEntry(
        key=row.key,
        status=_status(reasons=reasons, self_us=row.self_us, min_us=min_us),
        self_us=row.self_us,
        cumulative_us=row.cumulative_us,
        reasons=reasons,
        bound_names=names,
        module=row.owner,
        display_path=row.display_path,
        lineno=row.lineno,
        source=row.source,
    )


def _verdict_for(
    row: Attribution,
    analyzer: Analyzer,
    source_root: Path | None,
    rejected: list[str],
) -> Verdict | None:
    """Look the row's statement up in its source file, if that is possible."""
    if source_root is None or row.display_path is None or row.lineno is None:
        return None
    path = _contained_path(source_root, row.display_path)
    if path is None:
        rejected.append(_OUTSIDE_ROOT_WARNING.format(path=row.display_path))
        return None
    return analyzer.find(path, row.owner or "", row.lineno)


def _contained_path(source_root: Path, display_path: str) -> Path | None:
    """Resolve a row's path, or return ``None`` when it escapes the scan root.

    A profile document is untrusted input: both halves of this join come out of
    the JSON, and an absolute ``file`` would replace the root outright while a
    ``../`` one would climb out of it.  Either way importbudget would read and
    parse a file the user never pointed it at, so the row is refused instead.
    """
    root = source_root.resolve()
    candidate = (root / display_path).resolve()
    return candidate if candidate.is_relative_to(root) else None


def _status(
    *,
    reasons: tuple[Violation, ...],
    self_us: int,
    min_us: int,
) -> PlanStatus:
    """Map a statement's violations and cost onto its plan status."""
    if reasons:
        return PlanStatus.EXCLUDED
    if self_us < min_us:
        return PlanStatus.BELOW_THRESHOLD
    return PlanStatus.PROPOSED


def _totals(
    entries: Sequence[PlanEntry],
    summary: ProfileSummary,
    unaddressable_us: int,
) -> PlanTotals:
    """Fold decided entries into the summary block."""
    proposed = [e for e in entries if e.status is PlanStatus.PROPOSED]
    below = [e for e in entries if e.status is PlanStatus.BELOW_THRESHOLD]
    excluded = [e for e in entries if e.status is PlanStatus.EXCLUDED]
    return PlanTotals(
        predicted_saving_us=sum(entry.self_us for entry in proposed),
        safe_count=len(proposed) + len(below),
        excluded_count=len(excluded),
        below_threshold_count=len(below),
        candidate_count=len(entries),
        attributed_us=summary.attributed_us,
        unaddressable_us=unaddressable_us,
    )
