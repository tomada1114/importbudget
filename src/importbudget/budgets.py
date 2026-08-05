"""Value objects describing a startup-time budget and the gate's verdict.

What the budget is (:class:`Budget`), how to measure against it
(:class:`CheckOptions`) and what came back (:class:`CheckResult`,
:class:`CheckOutcome`).  Kept apart from :mod:`importbudget.check`, which owns
the measurement, for the same reason :mod:`importbudget.plans` is kept apart
from :mod:`importbudget.planner`.

The gate has three answers, not two.  "Over budget" and "could not measure"
are different facts and a CI job must be able to tell them apart, so
:attr:`CheckResult.exit_code` maps them onto three distinct process exit codes
(see :data:`EXIT_CODES`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from .stderr import ForeignStderr

if TYPE_CHECKING:
    from .entrypoints import RunOptions

__all__ = [
    "DURATION_UNITS_US",
    "EXIT_CODES",
    "Budget",
    "CheckOptions",
    "CheckOutcome",
    "CheckResult",
]

#: Microseconds in each unit ``--max`` accepts.
DURATION_UNITS_US = {"us": 1, "ms": 1_000, "s": 1_000_000}

_DURATION_RE = re.compile(
    rf"^\s*(\d*\.?\d+)\s*({'|'.join(DURATION_UNITS_US)})\s*$",
    re.IGNORECASE,
)

_US_PER_MS = 1000.0


@dataclass(frozen=True, slots=True)
class Budget:
    """A startup-time ceiling, canonically in microseconds.

    Attributes:
        us: The ceiling, in microseconds.
    """

    us: int

    def __post_init__(self) -> None:
        """Reject a ceiling no measurement could ever sit under.

        Raises:
            ValueError: ``us`` is negative.
        """
        if self.us < 0:
            msg = f"budget must be >= 0 us, got {self.us}"
            raise ValueError(msg)

    @classmethod
    def parse(cls, text: str) -> Budget:
        """Parse a duration such as ``150ms`` or ``0.15s`` into a budget.

        A bare number is refused on purpose: ``--max 150`` reads as either 150
        milliseconds or 150 seconds depending on who wrote it, and guessing
        wrong turns a gate into a rubber stamp.

        Args:
            text: The duration as the user typed it. The unit is required and
                may be ``us``, ``ms`` or ``s``, in any case.

        Returns:
            The parsed budget.

        Raises:
            ValueError: The text is not a duration with a recognized unit.
        """
        match = _DURATION_RE.match(text)
        if match is None:
            units = ", ".join(DURATION_UNITS_US)
            msg = (
                f"cannot read {text!r} as a duration; give a number and one of "
                f"the units {units}, such as 150ms or 0.15s"
            )
            raise ValueError(msg)
        amount, unit = match.groups()
        return cls(us=round(float(amount) * DURATION_UNITS_US[unit.lower()]))

    @property
    def ms(self) -> float:
        """The ceiling in milliseconds."""
        return self.us / _US_PER_MS


class CheckOutcome(StrEnum):
    """The three answers the budget gate can give."""

    WITHIN = "within"
    """Measured cost is at or below the budget."""

    OVER = "over"
    """Measured cost exceeds the budget."""

    FAILED = "failed"
    """The entrypoint could not be measured; the budget was never tested."""


#: Process exit code per outcome. Distinct on purpose: a CI job that treats
#: "could not measure" as "over budget" reports a regression that never
#: happened, and one that treats it as "within" hides a broken entrypoint.
EXIT_CODES = {
    CheckOutcome.WITHIN: 0,
    CheckOutcome.OVER: 1,
    CheckOutcome.FAILED: 2,
}


@dataclass(frozen=True, slots=True)
class CheckOptions:
    """Knobs for one budget check.

    Attributes:
        budget: The ceiling the measured cost is compared against.
        run: How the entrypoint is measured.
    """

    budget: Budget
    run: RunOptions | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Everything one ``importbudget check`` run produced.

    Attributes:
        target: The entrypoint that was measured.
        kind: How that entrypoint was executed.
        budget: The ceiling it was compared against.
        cost_us: Measured import time excluding interpreter bootstrap, or
            ``None`` when the entrypoint could not be measured.
        measured_us: Total import time reported by the measurement.
        filtered_us: Interpreter bootstrap time removed as noise.
        runs: Measured runs behind the numbers.
        warmup_runs: Runs discarded before measuring.
        returncodes: Exit status of each measured run.
        python_version: Interpreter the measurement ran on.
        platform: Host platform of the measurement.
        failure: Why the entrypoint could not be measured; ``None`` when it
            could.
        warnings: importbudget's own diagnostics from the run.
        stderr: What the measured program itself wrote to stderr.
    """

    target: str
    kind: str
    budget: Budget
    cost_us: int | None = None
    measured_us: int | None = None
    filtered_us: int | None = None
    runs: int = 0
    warmup_runs: int = 0
    returncodes: tuple[int, ...] = ()
    python_version: str = ""
    platform: str = ""
    failure: str | None = None
    warnings: tuple[str, ...] = ()
    stderr: ForeignStderr = field(default_factory=ForeignStderr)

    @property
    def outcome(self) -> CheckOutcome:
        """The gate's verdict.

        A cost exactly equal to the budget is within it: a budget is a
        ceiling, and refusing the value it names would make every documented
        budget one microsecond tighter than it reads.
        """
        if self.failure is not None or self.cost_us is None:
            return CheckOutcome.FAILED
        if self.cost_us > self.budget.us:
            return CheckOutcome.OVER
        return CheckOutcome.WITHIN

    @property
    def exit_code(self) -> int:
        """Process exit code for this verdict; see :data:`EXIT_CODES`."""
        return EXIT_CODES[self.outcome]

    @property
    def headroom_us(self) -> int | None:
        """Microseconds left under the budget; negative when over it."""
        if self.cost_us is None:
            return None
        return self.budget.us - self.cost_us
