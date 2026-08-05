"""Command-line interface for importbudget.

Only :mod:`argparse` is used: importbudget profiles startup cost, so its own
startup must not be padded with a CLI framework.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from ._version import __version__
from .applies import NATIVE_TARGET_VERSION, TARGET_VERSIONS, ApplyOptions
from .apply_report import render_apply_json, render_apply_table
from .budgets import Budget, CheckOptions
from .check import check
from .check_report import render_check_json, render_check_table
from .codemod import apply
from .errors import ImportBudgetError
from .measure import DEFAULT_RUNS, DEFAULT_WARMUP_RUNS, Entrypoint, RunOptions
from .plan_report import render_plan_json, render_plan_table
from .planner import plan, plan_from_profile
from .plans import PlanOptions
from .profiler import profile
from .report import render_json, render_table
from .verifies import (
    DEFAULT_DIVERGENCE_THRESHOLD,
    DEFAULT_VERIFY_RUNS,
    DEFAULT_VERIFY_WARMUP,
    VerifyOptions,
)
from .verify import verify
from .verify_report import render_verify_json, render_verify_table

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .plans import PlanResult


__all__ = ["build_parser", "main"]

DEFAULT_TOP = 10

_EXIT_OK = 0
_EXIT_ERROR = 1
_US_PER_MS = 1000


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``importbudget`` command.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="importbudget",
        description=(
            "Attribute Python startup import time to the statements that caused it."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_profile_parser(subparsers)
    _add_plan_parser(subparsers)
    _add_apply_parser(subparsers)
    _add_verify_parser(subparsers)
    _add_check_parser(subparsers)
    return parser


def _add_profile_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``profile`` subcommand and its flags."""
    profile_parser = subparsers.add_parser(
        "profile",
        help="measure an entrypoint and attribute its import time",
        description=(
            "Run an entrypoint under `python -X importtime` and attribute each "
            "module's self time to the first import statement that imported it."
        ),
    )
    profile_parser.add_argument(
        "entrypoint",
        help="module to import, module to run with -m, or a script path",
    )
    profile_parser.add_argument(
        "-m",
        "--module",
        action="store_true",
        help="run the entrypoint with -m instead of importing it",
    )
    _add_measurement_flags(profile_parser)


def _add_plan_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``plan`` subcommand and its flags."""
    plan_parser = subparsers.add_parser(
        "plan",
        help="propose the import statements that can safely be made lazy",
        description=(
            "Join a profile with the PEP 810 safety rules. A statement is "
            "proposed only when every rule proves it safe; everything else is "
            "listed with the machine-readable codes that rejected it."
        ),
    )
    plan_parser.add_argument(
        "entrypoint",
        nargs="?",
        help="module to import, module to run with -m, or a script path",
    )
    _add_plan_flags(plan_parser)
    _add_measurement_flags(plan_parser)


def _add_plan_flags(plan_parser: argparse.ArgumentParser) -> None:
    """Register the flags unique to ``plan``."""
    plan_parser.add_argument(
        "-m",
        "--module",
        action="store_true",
        help="run the entrypoint with -m instead of importing it",
    )
    plan_parser.add_argument(
        "--from-profile",
        metavar="PATH",
        help=(
            "plan from a saved `profile --json` document instead of measuring; "
            "--runs and --warmup are then unused"
        ),
    )
    plan_parser.add_argument(
        "--min-ms",
        type=float,
        default=0.0,
        metavar="X",
        help=(
            "do not propose statements attributed less than X ms; they are "
            "still listed, as skipped by the threshold (default: 0)"
        ),
    )


def _add_apply_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``apply`` subcommand and its flags."""
    apply_parser = subparsers.add_parser(
        "apply",
        help="convert the statements a plan proved safe into lazy imports",
        description=(
            "Rewrite the `verdict: safe` statements of a saved `plan --json` "
            "document as PEP 810 lazy imports. Prints a diff and writes "
            "nothing unless --write is given."
        ),
    )
    apply_parser.add_argument(
        "plan",
        metavar="PLAN",
        help="a document written by `importbudget plan --json`",
    )
    apply_parser.add_argument(
        "--write",
        action="store_true",
        help="write the converted files to disk instead of only showing them",
    )
    apply_parser.add_argument(
        "--target-version",
        default=NATIVE_TARGET_VERSION,
        choices=TARGET_VERSIONS,
        metavar="X.Y",
        help=(
            f"interpreter the converted source must run on; anything below "
            f"{NATIVE_TARGET_VERSION} uses the importlib.util.LazyLoader "
            f"fallback, which cannot convert from-imports (default: "
            f"{NATIVE_TARGET_VERSION})"
        ),
    )
    apply_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON document instead of the report",
    )


def _add_verify_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``verify`` subcommand and its flags."""
    verify_parser = subparsers.add_parser(
        "verify",
        help="measure a plan's conversion before and after, and compare them",
        description=(
            "Re-measure the plan's entrypoint on two scratch copies of its "
            "source — one unconverted, one converted — in strictly interleaved "
            "pairs, and report the delta with its standard deviation. An "
            "improvement is claimed only above 3 sigma. Your own files are "
            "never touched."
        ),
    )
    verify_parser.add_argument(
        "plan",
        metavar="PLAN",
        help="a document written by `importbudget plan --json`",
    )
    verify_parser.add_argument(
        "--target-version",
        default=NATIVE_TARGET_VERSION,
        choices=TARGET_VERSIONS,
        metavar="X.Y",
        help=(
            f"interpreter the converted tree is emitted for; it has to be one "
            f"this interpreter can run, since this interpreter measures it "
            f"(default: {NATIVE_TARGET_VERSION})"
        ),
    )
    verify_parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_VERIFY_RUNS,
        metavar="N",
        help=f"interleaved before/after pairs to measure (default: {DEFAULT_VERIFY_RUNS})",
    )
    verify_parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_VERIFY_WARMUP,
        metavar="N",
        help=(
            f"pairs discarded before measuring, to pay the cold page cache on "
            f"both trees (default: {DEFAULT_VERIFY_WARMUP})"
        ),
    )
    verify_parser.add_argument(
        "--divergence-threshold",
        type=float,
        default=DEFAULT_DIVERGENCE_THRESHOLD,
        metavar="X",
        help=(
            f"warn when the plan's predicted saving and the measured one differ "
            f"by more than this fraction (default: {DEFAULT_DIVERGENCE_THRESHOLD})"
        ),
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON document instead of the report",
    )


def _add_check_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``check`` subcommand and its flags."""
    check_parser = subparsers.add_parser(
        "check",
        help="fail when an entrypoint's import cost exceeds a budget",
        description=(
            "Measure an entrypoint's import time, excluding interpreter "
            "startup, and compare it with --max. Exits 0 within budget "
            "(equality included), 1 over budget, and 2 when the entrypoint "
            "could not be measured at all."
        ),
    )
    check_parser.add_argument(
        "entrypoint",
        help="module to import, module to run with -m, or a script path",
    )
    check_parser.add_argument(
        "-m",
        "--module",
        action="store_true",
        help="run the entrypoint with -m instead of importing it",
    )
    check_parser.add_argument(
        "--max",
        required=True,
        type=_budget,
        metavar="DURATION",
        help="budget with a unit, such as 150ms or 0.15s; a bare number is refused",
    )
    check_parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        metavar="N",
        help=f"measured runs to average (default: {DEFAULT_RUNS})",
    )
    check_parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_RUNS,
        metavar="N",
        help=(
            f"runs discarded before measuring, to pay the cold page cache "
            f"(default: {DEFAULT_WARMUP_RUNS})"
        ),
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON document instead of the report",
    )


def _budget(text: str) -> Budget:
    """Parse ``--max`` for argparse, which reports the failure and exits 2.

    Raises:
        argparse.ArgumentTypeError: The value is not a duration with a unit.
            Routing it through argparse rather than the command body keeps a
            mistyped budget out of exit code 1, which means "over budget" and
            would read as a regression that never happened.
    """
    try:
        return Budget.parse(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _add_measurement_flags(profile_parser: argparse.ArgumentParser) -> None:
    """Register the flags controlling how much is measured and shown."""
    profile_parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        metavar="N",
        help=f"measured runs to average (default: {DEFAULT_RUNS})",
    )
    profile_parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_RUNS,
        metavar="N",
        help=(
            f"runs discarded before measuring, to pay the cold page cache "
            f"(default: {DEFAULT_WARMUP_RUNS})"
        ),
    )
    profile_parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help=(
            f"statements to show per table section, 0 for all (default: "
            f"{DEFAULT_TOP}); JSON output always contains every statement"
        ),
    )
    profile_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the machine-readable JSON document instead of the table",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Arguments to parse; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 when the subcommand failed.
        ``check`` additionally returns its own verdict, where 1 means "over
        budget" and 2 means "the entrypoint could not be measured".
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "profile": _run_profile,
        "plan": _run_plan,
        "apply": _run_apply,
        "verify": _run_verify,
        "check": _run_check,
    }
    try:
        return commands[args.command](args)
    # ValueError covers out-of-range --runs / --warmup / --min-ms values, which
    # argparse cannot reject on its own.
    except (ImportBudgetError, ValueError) as error:
        print(f"importbudget: {error}", file=sys.stderr)  # noqa: T201
        return _EXIT_ERROR


def _run_profile(args: argparse.Namespace) -> int:
    """Execute the ``profile`` subcommand."""
    entrypoint = Entrypoint.parse(args.entrypoint, run_module=args.module)
    options = RunOptions(runs=args.runs, warmup=args.warmup)
    result = profile(entrypoint, options)
    output = render_json(result) if args.json else render_table(result, top=args.top)
    print(output)  # noqa: T201 — stdout is this command's output channel
    return _EXIT_OK


def _run_plan(args: argparse.Namespace) -> int:
    """Execute the ``plan`` subcommand.

    Raises:
        ValueError: Neither an entrypoint nor ``--from-profile`` was given, or
            both were.
    """
    result = _plan_result(args)
    output = (
        render_plan_json(result)
        if args.json
        else render_plan_table(result, top=args.top)
    )
    print(output)  # noqa: T201 — stdout is this command's output channel
    return _EXIT_OK


def _run_apply(args: argparse.Namespace) -> int:
    """Execute the ``apply`` subcommand."""
    options = ApplyOptions(target_version=args.target_version, write=args.write)
    result = apply(args.plan, options)
    output = render_apply_json(result) if args.json else render_apply_table(result)
    print(output)  # noqa: T201 — stdout is this command's output channel
    return _EXIT_OK


def _run_verify(args: argparse.Namespace) -> int:
    """Execute the ``verify`` subcommand."""
    options = VerifyOptions(
        run=RunOptions(runs=args.runs, warmup=args.warmup),
        target_version=args.target_version,
        divergence_threshold=args.divergence_threshold,
    )
    result = verify(args.plan, options)
    output = render_verify_json(result) if args.json else render_verify_table(result)
    print(output)  # noqa: T201 — stdout is this command's output channel
    return _EXIT_OK


def _run_check(args: argparse.Namespace) -> int:
    """Execute the ``check`` subcommand.

    Returns:
        The gate's own exit code: 0 within budget, 1 over it, 2 when the
        entrypoint could not be measured.
    """
    entrypoint = Entrypoint.parse(args.entrypoint, run_module=args.module)
    options = CheckOptions(
        budget=args.max,
        run=RunOptions(runs=args.runs, warmup=args.warmup),
    )
    result = check(entrypoint, options)
    output = render_check_json(result) if args.json else render_check_table(result)
    print(output)  # noqa: T201 — stdout is this command's output channel
    return result.exit_code


def _plan_result(args: argparse.Namespace) -> PlanResult:
    """Build the plan from whichever input form the user chose."""
    options = PlanOptions(
        run=RunOptions(runs=args.runs, warmup=args.warmup),
        min_us=round(args.min_ms * _US_PER_MS),
    )
    if args.from_profile:
        if args.entrypoint:
            msg = "give either an entrypoint or --from-profile, not both"
            raise ValueError(msg)
        return plan_from_profile(args.from_profile, options)
    if not args.entrypoint:
        msg = "plan needs an entrypoint, or --from-profile PATH"
        raise ValueError(msg)
    entrypoint = Entrypoint.parse(args.entrypoint, run_module=args.module)
    return plan(entrypoint, options)


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
