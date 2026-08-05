"""Prove a plan's saving by measuring both source trees, run for run.

``verify`` is the answer to the one thing ``plan`` cannot do: its predicted
saving is a set-dependent upper bound, and the PoC watched the same module get
charged to different statements across runs of the same program.  So nothing
here is derived from the plan's arithmetic.  Both sides are re-measured, always.

The session is built out of three deliberate choices:

* **Two scratch trees, not one edited in place.**  The plan's source root is
  copied twice and the conversion's before/after text is written into the
  copies, so the user's checkout is never touched and the "before" side stays
  available even after ``apply --write`` has already run.
* **Strict interleaving.**  Runs alternate before, after, before, after.
  Batching all of one side first lets any drift in machine load land entirely
  on one arm of the comparison, which is exactly the error a paired design
  exists to remove.  The order actually executed is carried in
  :attr:`~importbudget.verifies.VerifyResult.schedule` so it can be checked.
* **Interpreter bootstrap subtracted per run**, for the same reason
  :mod:`importbudget.check` subtracts it: it is not the project's cost.
"""

from __future__ import annotations

import platform as platform_module
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from ._subtrees import normalize
from ._verify_input import load_plan_facts
from .applies import ApplyOptions
from .codemod import apply
from .entrypoints import RunOptions
from .errors import MeasurementError, VerifyInputError
from .importtime import parse_importtime, validate_totals
from .measure import build_child_env, run_child
from .verifies import (
    DEFAULT_VERIFY_RUNS,
    DEFAULT_VERIFY_WARMUP,
    Comparison,
    ComparisonKind,
    Side,
    VerifyOptions,
    VerifyResult,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ._verify_input import PlanFacts
    from .applies import ApplyResult, FileEdit
    from .entrypoints import Entrypoint
    from .importtime import ImportTree

__all__ = ["verify"]

#: Printed by the baseline run so the version probe costs no extra process.
_VERSION_PROBE = "import sys; print(sys.version.split()[0])"

_MAX_STDERR_EXCERPT = 500
_US_PER_MS = 1000.0

#: Never copied into a scratch tree: build caches whose contents would make the
#: two sides start out unequally warm, and directories that are simply large.
_IGNORED = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)


@dataclass(frozen=True, slots=True)
class _Roots:
    """The two scratch trees one verification measures against."""

    before: Path
    after: Path


@dataclass(frozen=True, slots=True)
class _Session:
    """The raw output of one interleaved measurement session."""

    schedule: tuple[Side, ...]
    before: tuple[tuple[ImportTree, int], ...]
    after: tuple[tuple[ImportTree, int], ...]
    returncodes: tuple[int, ...]
    python_version: str
    runs: int
    warmup_runs: int


def verify(
    plan_path: Path | str,
    options: VerifyOptions | None = None,
) -> VerifyResult:
    """Measure a plan's conversion on both source trees and compare them.

    Args:
        plan_path: A document written by ``importbudget plan --json``.
        options: Run counts, target version and divergence threshold.

    Returns:
        The paired comparison, raw and — when a subtree stayed unchanged —
        normalized, plus the plan's prediction to hold it against.

    Raises:
        VerifyInputError: The plan document is unusable, or its conversion
            changes no source, leaving nothing to compare.
        MeasurementError: The interpreter could not be started, or a run
            produced unusable ``-X importtime`` output.
    """
    opts = options or VerifyOptions()
    path = Path(plan_path)
    facts = load_plan_facts(path)
    conversion = apply(path, ApplyOptions(target_version=opts.target_version))
    edits = conversion.changed_edits()
    if not edits:
        msg = (
            f"{path} converts no source, so there is nothing to verify. The "
            f"plan proposes no statement the codemod can rewrite, or it has "
            f"already been applied to these files."
        )
        raise VerifyInputError(msg)

    run = opts.run or RunOptions(runs=DEFAULT_VERIFY_RUNS, warmup=DEFAULT_VERIFY_WARMUP)
    with TemporaryDirectory(prefix="importbudget-verify-") as scratch:
        roots = _materialize(Path(scratch), facts.source_root, edits)
        session = _run_pairs(facts.entrypoint, roots, run)
    return _build_result(facts, conversion, session, opts)


def _materialize(
    scratch: Path,
    source_root: Path,
    edits: Sequence[FileEdit],
) -> _Roots:
    """Copy the source root twice and write each side's text into its copy."""
    roots = _Roots(before=scratch / "before", after=scratch / "after")
    for root in (roots.before, roots.after):
        shutil.copytree(source_root, root, ignore=_IGNORED)
    for edit in edits:
        _write(roots.before, edit.display_path, edit.before)
        _write(roots.after, edit.display_path, edit.after)
    return roots


def _write(root: Path, display_path: str, text: str) -> None:
    """Write one file into a scratch tree, refusing to escape it.

    Raises:
        VerifyInputError: The plan names a path outside its own source root.
    """
    target = (root / display_path).resolve()
    if not target.is_relative_to(root.resolve()):
        msg = f"the plan names a file outside its source root: {display_path}"
        raise VerifyInputError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _run_pairs(entrypoint: Entrypoint, roots: _Roots, run: RunOptions) -> _Session:
    """Alternate before/after runs, discarding the warm-up pairs."""
    python = run.python or sys.executable
    baseline, python_version = _baseline(python, roots.before)
    schedule: list[Side] = []
    before: list[tuple[ImportTree, int]] = []
    after: list[tuple[ImportTree, int]] = []
    returncodes: list[int] = []
    sides = ((Side.BEFORE, roots.before, before), (Side.AFTER, roots.after, after))

    for index in range(run.warmup + run.runs):
        for side, root, kept in sides:
            tree, returncode = _measure_once(python, entrypoint, root)
            if index < run.warmup:
                continue
            schedule.append(side)
            kept.append((tree, _net_us(tree, baseline)))
            returncodes.append(returncode)
    return _Session(
        schedule=tuple(schedule),
        before=tuple(before),
        after=tuple(after),
        returncodes=tuple(returncodes),
        python_version=python_version,
        runs=run.runs,
        warmup_runs=run.warmup,
    )


def _measure_once(
    python: str,
    entrypoint: Entrypoint,
    root: Path,
) -> tuple[ImportTree, int]:
    """Run the entrypoint once from ``root`` under ``-X importtime``."""
    completed = run_child(
        python,
        ["-X", "importtime", *entrypoint.command_args],
        cwd=root,
        env=build_child_env(root),
    )
    tree = parse_importtime(completed.stderr)
    validate_totals(tree)
    return tree, completed.returncode


def _baseline(python: str, root: Path) -> tuple[frozenset[str], str]:
    """Return the bootstrap module names and the interpreter version.

    Raises:
        MeasurementError: The baseline probe could not be run.
    """
    completed = run_child(
        python,
        ["-X", "importtime", "-c", _VERSION_PROBE],
        cwd=root,
        env=build_child_env(root),
    )
    if completed.returncode != 0:
        excerpt = completed.stderr.strip()[-_MAX_STDERR_EXCERPT:]
        msg = f"Failed to measure the interpreter baseline with {python!r}: {excerpt}"
        raise MeasurementError(msg)
    return parse_importtime(completed.stderr).names(), completed.stdout.strip()


def _net_us(tree: ImportTree, baseline: frozenset[str]) -> int:
    """Return one run's import time, less the interpreter's own bootstrap."""
    bootstrap = sum(node.self_us for node in tree.nodes if node.name in baseline)
    return tree.total_root_cumulative_us - bootstrap


def _build_result(
    facts: PlanFacts,
    conversion: ApplyResult,
    session: _Session,
    options: VerifyOptions,
) -> VerifyResult:
    """Assemble the measured session and the plan's prediction into one answer."""
    return VerifyResult(
        target=facts.entrypoint.target,
        kind=str(facts.entrypoint.kind),
        plan_path=conversion.plan_path,
        target_version=conversion.target_version,
        schedule=session.schedule,
        raw=Comparison(
            kind=ComparisonKind.RAW,
            before=tuple(float(total) for _, total in session.before),
            after=tuple(float(total) for _, total in session.after),
        ),
        normalized=normalize(session.before, session.after),
        predicted_saving_us=facts.predicted_saving_us,
        attributed_us=facts.attributed_us,
        unaddressable_us=facts.unaddressable_us,
        converted_count=len(conversion.converted()),
        skipped_count=len(conversion.skipped()),
        divergence_threshold=options.divergence_threshold,
        runs=session.runs,
        warmup_runs=session.warmup_runs,
        python_version=session.python_version,
        platform=platform_module.platform(),
        returncodes=session.returncodes,
    )
