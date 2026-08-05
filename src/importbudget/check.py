"""Measure one entrypoint's import cost and compare it with a budget.

This is the CI gate.  It deliberately does *not* go through
:func:`~importbudget.profiler.profile`: attribution needs the entrypoint's
source on disk, while a budget only needs the clock, so ``check`` works against
an installed wheel exactly as it works against a checkout.

Two rules make the number worth gating on:

* Interpreter bootstrap is subtracted.  A bare ``python -c pass`` already
  imports ``encodings``, ``site`` and friends; charging that to the project
  would make every budget interpreter- and machine-specific for no reason.
  ``-m`` entrypoints additionally pay ``runpy``, which
  :func:`~importbudget.measure.measure` already reports as baseline.
* A run that exited non-zero is a *failure*, never a measurement.  ``profile``
  keeps such a run and warns, because the rows printed before the entrypoint
  died are still real; a gate must not, because a crashing entrypoint that
  imports half of what it normally does would silently pass its budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .budgets import Budget, CheckOptions, CheckResult
from .entrypoints import Entrypoint, RunOptions
from .errors import EntrypointError, MeasurementError
from .measure import measure

if TYPE_CHECKING:
    from .entrypoints import Measurement

__all__ = ["check"]

_MAX_FAILURE_EXCERPT = 200


def check(
    entrypoint: Entrypoint | str,
    options: CheckOptions | Budget,
) -> CheckResult:
    """Measure an entrypoint's import cost and compare it with a budget.

    Args:
        entrypoint: Module name, script path, or a prepared
            :class:`~importbudget.entrypoints.Entrypoint`.
        options: The budget, or a full :class:`~importbudget.budgets.CheckOptions`
            when the run counts matter too.

    Returns:
        The verdict. A failure to run the entrypoint is reported as
        :attr:`~importbudget.budgets.CheckOutcome.FAILED` rather than raised,
        so that ``--json`` still produces a document and the caller can map
        every outcome onto its own exit code.
    """
    opts = CheckOptions(budget=options) if isinstance(options, Budget) else options
    run = opts.run or RunOptions()
    try:
        target = (
            Entrypoint.parse(entrypoint) if isinstance(entrypoint, str) else entrypoint
        )
        measurement = measure(target, run)
    except (EntrypointError, MeasurementError) as error:
        return _failed(entrypoint, opts, run, reason=str(error))

    if failure := _run_failure(measurement):
        return _failed(
            measurement.entrypoint,
            opts,
            run,
            reason=failure,
            measurement=measurement,
        )
    return _measured(measurement, opts)


def _measured(measurement: Measurement, options: CheckOptions) -> CheckResult:
    """Build the verdict for a measurement that completed cleanly."""
    filtered_us = _bootstrap_us(measurement)
    return CheckResult(
        target=measurement.entrypoint.target,
        kind=str(measurement.entrypoint.kind),
        budget=options.budget,
        cost_us=measurement.measured_us - filtered_us,
        measured_us=measurement.measured_us,
        filtered_us=filtered_us,
        runs=measurement.runs,
        warmup_runs=measurement.warmup_runs,
        returncodes=measurement.returncodes,
        python_version=measurement.python_version,
        platform=measurement.platform,
        warnings=measurement.warnings,
        stderr=measurement.stderr,
    )


def _failed(
    entrypoint: Entrypoint | str,
    options: CheckOptions,
    run: RunOptions,
    *,
    reason: str,
    measurement: Measurement | None = None,
) -> CheckResult:
    """Build the verdict for an entrypoint that could not be measured."""
    target = entrypoint if isinstance(entrypoint, str) else entrypoint.target
    kind = "" if isinstance(entrypoint, str) else str(entrypoint.kind)
    if measurement is None:
        return CheckResult(
            target=target,
            kind=kind,
            budget=options.budget,
            runs=run.runs,
            warmup_runs=run.warmup,
            failure=reason,
        )
    return CheckResult(
        target=target,
        kind=kind,
        budget=options.budget,
        runs=measurement.runs,
        warmup_runs=measurement.warmup_runs,
        returncodes=measurement.returncodes,
        python_version=measurement.python_version,
        platform=measurement.platform,
        failure=reason,
        warnings=measurement.warnings,
        stderr=measurement.stderr,
    )


def _run_failure(measurement: Measurement) -> str | None:
    """Return why the entrypoint is unmeasurable, or ``None`` when it is not.

    The excerpt comes from the entrypoint's own stderr, which is where the
    traceback lands, so the message names the underlying exception instead of
    only the exit status.
    """
    failures = sorted({code for code in measurement.returncodes if code != 0})
    if not failures:
        return None
    detail = _last_stderr_line(measurement)
    status = f"exited with status {', '.join(str(code) for code in failures)}"
    return (
        f"the entrypoint {status}: {detail}" if detail else f"the entrypoint {status}"
    )


def _last_stderr_line(measurement: Measurement) -> str:
    """Return the entrypoint's last stderr line: a traceback's exception line."""
    lines = measurement.stderr.lines
    if not lines:
        return ""
    return lines[-1][:_MAX_FAILURE_EXCERPT]


def _bootstrap_us(measurement: Measurement) -> int:
    """Return the interpreter startup time to subtract from the measured total.

    A module the bare interpreter imports anyway is bootstrap wherever it
    appears in the tree: it was already in ``sys.modules`` before the
    entrypoint ran, so its row cannot be the entrypoint's doing.
    """
    baseline = measurement.baseline_modules
    return sum(node.self_us for node in measurement.tree.nodes if node.name in baseline)
