"""Value objects describing a conversion plan.

What to plan (:class:`PlanOptions`), where the numbers came from
(:class:`ProfileSummary`), what was decided about each statement
(:class:`PlanEntry`, :class:`PlanStatus`) and the summary of all of it
(:class:`PlanTotals`, :class:`PlanResult`).  Kept apart from
:mod:`importbudget.planner`, which owns the analysis and the joining, so that
the shapes crossing the public API carry no machinery with them — the same
split :mod:`importbudget.entrypoints` makes against
:mod:`importbudget.measure`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .entrypoints import RunOptions
    from .profiler import ProfileResult
    from .rules import RuleCode, Violation

__all__ = [
    "MEASURED_ORIGIN",
    "PlanEntry",
    "PlanOptions",
    "PlanResult",
    "PlanStatus",
    "PlanTotals",
    "ProfileSummary",
]

#: Recorded as a plan's origin when it measured the entrypoint itself.
MEASURED_ORIGIN = "measured"


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    """The measurement facts a plan reports, whatever its origin.

    Attributes:
        target: The profiled entrypoint.
        kind: How that entrypoint was executed.
        origin: :data:`MEASURED_ORIGIN`, or the path of the profile document
            consumed, so a plan always says where its numbers came from.
        python_version: Interpreter the measurement ran on.
        platform: Host platform of the measurement.
        runs: Measured runs behind the numbers.
        warmup_runs: Runs discarded before measuring.
        measured_us: Total import time reported by the measurement.
        filtered_us: Interpreter bootstrap time removed as noise.
        attributed_us: Total time attributed to rows.
    """

    target: str
    kind: str
    origin: str
    python_version: str
    platform: str
    runs: int
    warmup_runs: int
    measured_us: int
    filtered_us: int
    attributed_us: int

    @classmethod
    def from_result(cls, result: ProfileResult) -> ProfileSummary:
        """Summarize a profile this process just measured."""
        attribution = result.attribution
        return cls(
            target=result.entrypoint.target,
            kind=str(result.entrypoint.kind),
            origin=MEASURED_ORIGIN,
            python_version=result.measurement.python_version,
            platform=result.measurement.platform,
            runs=result.measurement.runs,
            warmup_runs=result.measurement.warmup_runs,
            measured_us=attribution.measured_us,
            filtered_us=attribution.filtered_us,
            attributed_us=attribution.attributed_us,
        )


class PlanStatus(StrEnum):
    """What the plan decided to do with one costed statement."""

    PROPOSED = "proposed"
    """Proved safe and above the threshold: convert this one."""

    EXCLUDED = "excluded"
    """At least one safety rule fired; see the entry's reasons."""

    BELOW_THRESHOLD = "below_threshold"
    """Safe, but cheaper than ``--min-ms``; shown, not proposed."""


@dataclass(frozen=True, slots=True)
class PlanOptions:
    """Knobs that shape a plan rather than a measurement.

    Attributes:
        run: How the internal profile is measured. Ignored by
            :func:`~importbudget.planner.plan_from_profile`, which measures
            nothing.
        min_us: Statements attributed less than this are safe but not
            proposed, so a hundred 0.1 ms wins do not drown the real ones.
    """

    run: RunOptions | None = None
    min_us: int = 0

    def __post_init__(self) -> None:
        """Reject a negative threshold.

        Raises:
            ValueError: ``min_us`` is negative.
        """
        if self.min_us < 0:
            msg = f"min_us must be >= 0, got {self.min_us}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """One costed statement and what the plan decided about it.

    Attributes:
        key: ``file:line``, the same key the attribution table uses.
        status: Proposed, excluded, or below the threshold.
        self_us: Import time attributed to this statement.
        cumulative_us: Advisory cost of everything it pulls in. Rows overlap;
            never sum this column.
        reasons: Every rule that fired. Empty for a safe statement.
        bound_names: Module-global names the statement binds.
        module: Owning module, when known.
        display_path: Source file, relative to the scan root.
        lineno: 1-based line number, when known.
        source: The source line, when known.
    """

    key: str
    status: PlanStatus
    self_us: int
    cumulative_us: int
    reasons: tuple[Violation, ...] = ()
    bound_names: tuple[str, ...] = ()
    module: str | None = None
    display_path: str | None = None
    lineno: int | None = None
    source: str | None = None

    @property
    def is_safe(self) -> bool:
        """True when no safety rule fired, threshold notwithstanding."""
        return not self.reasons

    @property
    def codes(self) -> tuple[RuleCode, ...]:
        """Reason codes of every rule that fired."""
        return tuple(reason.code for reason in self.reasons)


@dataclass(frozen=True, slots=True)
class PlanTotals:
    """The one-line summary of a plan.

    Attributes:
        predicted_saving_us: Sum of the proposed statements' attributed time.
            Set-dependent — an upper bound on what conversion buys, not a
            promise.
        safe_count: Statements no rule rejected, threshold included.
        excluded_count: Statements at least one rule rejected.
        below_threshold_count: Safe statements dropped by ``min_us``.
        candidate_count: Costed statement rows the plan looked at.
        attributed_us: Total attributed time of the underlying profile.
        unaddressable_us: Cost on ``<entrypoint>`` and ``<dynamic>`` rows,
            which no import statement can be converted to remove.
    """

    predicted_saving_us: int
    safe_count: int
    excluded_count: int
    below_threshold_count: int
    candidate_count: int
    attributed_us: int
    unaddressable_us: int


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Everything one ``importbudget plan`` run produced.

    Attributes:
        profile: The measurement the plan is built on, live or loaded.
        entries: Every costed statement, ordered by descending attributed time.
        totals: The summary block.
        source_root: Directory the reported source paths are relative to.
        min_us: The threshold that was applied.
        warnings: importbudget's own diagnostics from measuring and analyzing.
    """

    profile: ProfileSummary
    entries: tuple[PlanEntry, ...]
    totals: PlanTotals
    source_root: Path | None = None
    min_us: int = 0
    warnings: tuple[str, ...] = ()

    def proposed(self) -> tuple[PlanEntry, ...]:
        """Return the statements the plan proposes converting."""
        return tuple(e for e in self.entries if e.status is PlanStatus.PROPOSED)

    def excluded(self) -> tuple[PlanEntry, ...]:
        """Return the statements at least one safety rule rejected."""
        return tuple(e for e in self.entries if e.status is PlanStatus.EXCLUDED)

    def below_threshold(self) -> tuple[PlanEntry, ...]:
        """Return the safe statements that ``min_us`` dropped."""
        return tuple(e for e in self.entries if e.status is PlanStatus.BELOW_THRESHOLD)
