"""Command-line interface for importbudget.

Only :mod:`argparse` is used: importbudget profiles startup cost, so its own
startup must not be padded with a CLI framework.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from ._version import __version__
from .errors import ImportBudgetError
from .measure import DEFAULT_RUNS, DEFAULT_WARMUP_RUNS, Entrypoint, RunOptions
from .profiler import profile
from .report import render_json, render_table

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["build_parser", "main"]

DEFAULT_TOP = 10

_EXIT_OK = 0
_EXIT_ERROR = 1


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
            f"statements to show in the table, 0 for all (default: "
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
        Process exit code: 0 on success, 1 when profiling failed.
    """
    args = build_parser().parse_args(argv)
    try:
        return _run_profile(args)
    # ValueError covers out-of-range --runs / --warmup values, which argparse
    # cannot reject on its own.
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


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
