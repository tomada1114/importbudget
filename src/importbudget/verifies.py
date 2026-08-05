"""Value objects describing a measured before/after comparison.

What to verify (:class:`VerifyOptions`), which side each run measured
(:class:`Side`), the statistics of one paired comparison
(:class:`Comparison`, :class:`ComparisonKind`) and the whole answer
(:class:`VerifyResult`).  Kept apart from :mod:`importbudget.verify`, which
owns the child processes and the scratch trees, exactly as
:mod:`importbudget.entrypoints` is kept apart from
:mod:`importbudget.measure`.

The statistics are deliberately austere, because the measurement PoC showed
warm-run coefficients of variation between 7% and 26% depending on machine
load:

* runs are **paired**.  Each pair measures the before tree and the after tree
  back to back, and the statistic is the mean of the per-pair *differences* —
  so a machine that slows down halfway through the session shifts both sides
  of every pair, not one side of the comparison.
* a claim needs :data:`SIGNIFICANCE_SIGMA` sigma.  ``3 sigma < |delta|`` is strict,
  so a delta sitting exactly on three standard deviations is *not* an
  improvement; refusing at the boundary is the answer that cannot be wrong.
* fewer than :data:`MIN_PAIRS` pairs has no standard deviation at all, and
  therefore no claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean, stdev
from typing import TYPE_CHECKING

from .applies import NATIVE_TARGET_VERSION

if TYPE_CHECKING:
    from .entrypoints import RunOptions

__all__ = [
    "DEFAULT_DIVERGENCE_THRESHOLD",
    "DEFAULT_VERIFY_RUNS",
    "DEFAULT_VERIFY_WARMUP",
    "MIN_PAIRS",
    "SIGNIFICANCE_SIGMA",
    "Comparison",
    "ComparisonKind",
    "Side",
    "VerifyOptions",
    "VerifyResult",
]

#: Standard deviations a delta must clear before it is called an improvement.
SIGNIFICANCE_SIGMA = 3.0

#: Paired runs below which no standard deviation, and so no claim, exists.
MIN_PAIRS = 2

#: Measured pairs kept by default. Three is enough for a mean but not for a
#: standard deviation worth trusting, so verify asks for more than ``profile``.
DEFAULT_VERIFY_RUNS = 5

#: Pairs run and discarded first, to pay the cold page cache on both trees.
DEFAULT_VERIFY_WARMUP = 1

#: Relative gap between predicted and measured saving that earns a warning.
DEFAULT_DIVERGENCE_THRESHOLD = 0.3

_US_PER_MS = 1000.0


class Side(StrEnum):
    """Which source tree one measured run started from."""

    BEFORE = "before"
    """The source as the plan found it."""

    AFTER = "after"
    """The source with the plan's conversions applied."""


class ComparisonKind(StrEnum):
    """Which figures a comparison was computed from."""

    RAW = "raw"
    """Per-run totals as measured."""

    NORMALIZED = "normalized"
    """Totals rescaled by an unchanged subtree measured in the same run."""


@dataclass(frozen=True, slots=True)
class Comparison:
    """One paired before/after comparison and the claim it does or does not support.

    Attributes:
        kind: Raw totals, or totals normalized against a reference subtree.
        before: Per-pair before figure, in microseconds.
        after: Per-pair after figure, in microseconds, same order.
        reference: Module whose subtree normalized the figures; ``None`` for
            :attr:`ComparisonKind.RAW`.
    """

    kind: ComparisonKind
    before: tuple[float, ...]
    after: tuple[float, ...]
    reference: str | None = None

    def __post_init__(self) -> None:
        """Reject unpaired samples.

        Raises:
            ValueError: The two sides hold different numbers of runs, which
                would make the per-pair differences meaningless.
        """
        if len(self.before) != len(self.after):
            msg = (
                f"a comparison needs paired runs, got {len(self.before)} before "
                f"and {len(self.after)} after"
            )
            raise ValueError(msg)

    @property
    def pairs(self) -> int:
        """Number of measured pairs."""
        return len(self.before)

    @property
    def deltas(self) -> tuple[float, ...]:
        """Per-pair ``after - before``; negative means faster."""
        return tuple(
            after - before
            for before, after in zip(self.before, self.after, strict=True)
        )

    @property
    def before_us(self) -> float:
        """Mean before figure."""
        return fmean(self.before) if self.before else 0.0

    @property
    def after_us(self) -> float:
        """Mean after figure."""
        return fmean(self.after) if self.after else 0.0

    @property
    def delta_us(self) -> float:
        """Mean of the per-pair differences; negative means faster."""
        deltas = self.deltas
        return fmean(deltas) if deltas else 0.0

    @property
    def sd_us(self) -> float:
        """Sample standard deviation of the per-pair differences."""
        deltas = self.deltas
        if len(deltas) < MIN_PAIRS:
            return 0.0
        return stdev(deltas)

    @property
    def noise_floor_us(self) -> float:
        """The ``3 sigma`` bar a delta has to clear."""
        return SIGNIFICANCE_SIGMA * self.sd_us

    @property
    def is_significant(self) -> bool:
        """True when the delta clears the noise floor.

        The comparison is strict, so a delta of exactly ``3 sigma`` is *not*
        significant: at the boundary the honest answer is that the measurement
        cannot tell.
        """
        if self.pairs < MIN_PAIRS:
            return False
        return self.noise_floor_us < abs(self.delta_us)

    @property
    def is_improvement(self) -> bool:
        """True when the delta is significant and points downwards."""
        return self.is_significant and self.delta_us < 0


@dataclass(frozen=True, slots=True)
class VerifyOptions:
    """Knobs that shape a verification rather than a measurement.

    Attributes:
        run: How each side is measured. ``runs`` counts *pairs*, and ``warmup``
            counts pairs discarded first.
        target_version: Interpreter the converted tree must run on; it has to
            be one this interpreter can execute, since it is this interpreter
            that measures the result.
        divergence_threshold: Relative gap between the plan's predicted saving
            and the measured one that earns a warning. ``0`` warns on any gap.
    """

    run: RunOptions | None = None
    target_version: str = NATIVE_TARGET_VERSION
    divergence_threshold: float = DEFAULT_DIVERGENCE_THRESHOLD

    def __post_init__(self) -> None:
        """Reject a negative divergence threshold.

        Raises:
            ValueError: ``divergence_threshold`` is negative.
        """
        if self.divergence_threshold < 0:
            msg = f"divergence_threshold must be >= 0, got {self.divergence_threshold}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Everything one ``importbudget verify`` run produced.

    Attributes:
        target: The entrypoint that was measured on both trees.
        kind: How that entrypoint was executed.
        plan_path: The plan document whose conversion was verified.
        target_version: Interpreter the converted tree was emitted for.
        schedule: The side of every measured run, in the order they ran.
        raw: Comparison of the measured totals.
        normalized: Comparison of the totals rescaled by an unchanged subtree,
            or ``None`` when no subtree was unchanged in every run.
        predicted_saving_us: The plan's predicted saving: an upper bound.
        attributed_us: Total attributed time of the plan's profile.
        unaddressable_us: Time the plan charged to the entrypoint itself or to
            dynamic imports, which no conversion can remove.
        converted_count: Statements the conversion rewrote.
        skipped_count: Safe statements the conversion declined to rewrite.
        divergence_threshold: Threshold the prediction was held to.
        runs: Measured pairs.
        warmup_runs: Pairs discarded first.
        python_version: Interpreter the measurement ran on.
        platform: Host platform of the measurement.
        warnings: Findings about *this* run, divergence included. The standing
            caveats belong to the report, which is where a reader meets them.
    """

    target: str
    kind: str
    plan_path: str
    target_version: str
    schedule: tuple[Side, ...]
    raw: Comparison
    normalized: Comparison | None = None
    predicted_saving_us: int = 0
    attributed_us: int = 0
    unaddressable_us: int = 0
    converted_count: int = 0
    skipped_count: int = 0
    divergence_threshold: float = DEFAULT_DIVERGENCE_THRESHOLD
    runs: int = 0
    warmup_runs: int = 0
    python_version: str = ""
    platform: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def decisive(self) -> Comparison:
        """The comparison the verdict rests on.

        Raw totals answer first, because they need no assumption about which
        subtree stayed unchanged.  Normalization is a *recovery* step: it is
        consulted only when the raw totals refused a claim, which is the case
        the PoC built it for — a delta smaller than the run-to-run noise of the
        total.
        """
        if self.raw.is_significant or self.normalized is None:
            return self.raw
        return self.normalized if self.normalized.is_significant else self.raw

    @property
    def measured_delta_us(self) -> float:
        """The delta being claimed, from :attr:`decisive`."""
        return self.decisive.delta_us

    @property
    def divergence(self) -> float | None:
        """Relative gap between the predicted and measured saving.

        Returns:
            ``|predicted - measured| / predicted``, or ``None`` when the plan
            predicted nothing and the ratio would be undefined.
        """
        if not self.predicted_saving_us:
            return None
        measured_saving = -self.measured_delta_us
        return abs(self.predicted_saving_us - measured_saving) / abs(
            self.predicted_saving_us
        )

    @property
    def has_divergence_warning(self) -> bool:
        """True when prediction and measurement disagree beyond the threshold."""
        divergence = self.divergence
        return divergence is not None and divergence > self.divergence_threshold

    @property
    def predicted_delta_ms(self) -> float:
        """The predicted saving expressed as a delta, so negative means faster."""
        return -self.predicted_saving_us / _US_PER_MS
