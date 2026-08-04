"""Run an entrypoint under ``python -X importtime`` and average the runs.

The parser lives in :mod:`importbudget.importtime` and the value objects in
:mod:`importbudget.entrypoints`; this module owns the child process, the
interpreter baseline used to filter bootstrap noise, and the run-to-run
averaging.  Three measurement rules come straight from the PoC:

* The first run pays the cold page cache (5-25x the steady state), so it is
  always discarded.
* ``-S`` must not be used to suppress ``site``: it removes site-packages and
  breaks virtualenv-installed entrypoints. The bootstrap baseline is measured
  with the same interpreter instead.
* Averaging must not break ``sum(self) == sum(root cumulative)``.  Rounding
  each mean independently does break it, so the residual is redistributed and
  cumulative times are rebuilt from the rounded self times.
"""

from __future__ import annotations

import os
import platform as platform_module
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .averaging import mean_tree
from .entrypoints import (
    DEFAULT_RUNS,
    DEFAULT_WARMUP_RUNS,
    Entrypoint,
    EntrypointKind,
    Measurement,
    RunOptions,
)
from .errors import MeasurementError
from .importtime import ImportNode, ImportTree, parse_importtime, validate_totals
from .stderr import ForeignStderr

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "DEFAULT_RUNS",
    "DEFAULT_WARMUP_RUNS",
    "Entrypoint",
    "EntrypointKind",
    "ForeignStderr",
    "ImportNode",
    "ImportTree",
    "Measurement",
    "RunOptions",
    "build_child_env",
    "mean_tree",
    "measure",
    "parse_importtime",
    "run_child",
    "validate_totals",
]

#: Printed by the baseline run so the version probe costs no extra process.
_VERSION_PROBE = "import sys; print(sys.version.split()[0])"

_MAX_STDERR_EXCERPT = 500


def measure(entrypoint: Entrypoint, options: RunOptions | None = None) -> Measurement:
    """Profile an entrypoint under ``-X importtime`` and average the runs.

    Args:
        entrypoint: What to start.
        options: Run counts and environment overrides.

    Returns:
        The mean measurement, plus the baseline used to filter interpreter noise.

    Raises:
        MeasurementError: The interpreter could not be started, or a measured
            run produced unusable ``-X importtime`` output.
    """
    opts = options or RunOptions()
    python = opts.python or sys.executable
    cwd = opts.cwd or Path.cwd()
    env = build_child_env(cwd)
    baseline_modules, python_version = _measure_baseline(python, cwd=cwd, env=env)
    trees, returncodes = _collect_runs(
        python, entrypoint, options=opts, cwd=cwd, env=env
    )
    averaged = mean_tree(trees)
    validate_totals(averaged)
    if entrypoint.kind is EntrypointKind.MODULE_RUN:
        baseline_modules |= _runpy_overhead(averaged)
    return Measurement(
        entrypoint=entrypoint,
        tree=averaged,
        baseline_modules=frozenset(baseline_modules),
        runs=opts.runs,
        warmup_runs=opts.warmup,
        returncodes=tuple(returncodes),
        python_executable=python,
        python_version=python_version,
        platform=platform_module.platform(),
        warnings=_run_warnings(trees, averaged, returncodes),
        stderr=ForeignStderr.merge(tree.stderr for tree in trees),
    )


def build_child_env(cwd: Path) -> dict[str, str]:
    """Return a child-process environment that can import packages from ``cwd``.

    Args:
        cwd: Working directory of the child interpreter.

    Returns:
        A copy of the current environment with ``cwd`` prepended to
        ``PYTHONPATH``, so an uninstalled project is importable exactly as it
        would be when run from that directory.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    entries = [str(cwd), existing] if existing else [str(cwd)]
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def run_child(
    python: str,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run one child interpreter, turning a failed spawn into a package error.

    Args:
        python: Interpreter to execute.
        args: Interpreter arguments, already split into argv entries.
        cwd: Working directory for the child.
        env: Environment for the child.

    Returns:
        The completed process, whatever its exit status.

    Raises:
        MeasurementError: The interpreter could not be started at all.
    """
    try:
        # S603: a fixed argument list run without a shell, built from the
        # interpreter path and the entrypoint the user asked to profile.
        return subprocess.run(  # noqa: S603
            [python, *args],
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"Could not run the interpreter {python!r}: {exc}"
        raise MeasurementError(msg) from exc


def _run_warnings(
    trees: Sequence[ImportTree],
    averaged: ImportTree,
    returncodes: Sequence[int],
) -> tuple[str, ...]:
    """Merge the per-run and averaging diagnostics, newest concern last.

    A non-zero exit is a warning rather than an error: the rows printed before
    the entrypoint died are still real, and refusing to report them would make
    importbudget useless on exactly the crashing startup a user wants to fix.
    """
    warnings = [warning for tree in trees for warning in tree.warnings]
    warnings.extend(averaged.warnings)
    if failures := sorted({code for code in returncodes if code != 0}):
        warnings.append(
            f"the entrypoint exited with a non-zero status {failures}; "
            f"the measurement may be incomplete"
        )
    return tuple(dict.fromkeys(warnings))


def _collect_runs(
    python: str,
    entrypoint: Entrypoint,
    *,
    options: RunOptions,
    cwd: Path,
    env: Mapping[str, str],
) -> tuple[list[ImportTree], list[int]]:
    """Run the entrypoint repeatedly, discarding the warm-up runs."""
    trees: list[ImportTree] = []
    returncodes: list[int] = []
    for index in range(options.warmup + options.runs):
        completed = run_child(
            python,
            ["-X", "importtime", *entrypoint.command_args],
            cwd=cwd,
            env=env,
        )
        if index < options.warmup:
            continue
        tree = parse_importtime(completed.stderr)
        validate_totals(tree)
        trees.append(tree)
        returncodes.append(completed.returncode)
    return trees, returncodes


def _measure_baseline(
    python: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> tuple[set[str], str]:
    """Return the bootstrap module names and the interpreter version.

    The baseline must come from the *same* interpreter and environment,
    otherwise site hooks such as ``sitecustomize`` are misclassified.
    """
    completed = run_child(
        python, ["-X", "importtime", "-c", _VERSION_PROBE], cwd=cwd, env=env
    )
    if completed.returncode != 0:
        excerpt = completed.stderr.strip()[-_MAX_STDERR_EXCERPT:]
        msg = f"Failed to measure the interpreter baseline with {python!r}: {excerpt}"
        raise MeasurementError(msg)
    tree = parse_importtime(completed.stderr)
    return set(tree.names()), completed.stdout.strip()


def _runpy_overhead(tree: ImportTree) -> frozenset[str]:
    """Return modules charged to ``-m`` execution rather than to the target.

    ``python -m pkg.cli`` imports ``runpy`` and its helpers before the target
    module runs; that subtree is pure invocation overhead. The target module's
    own imports appear as roots, not below ``runpy``, so nothing owned is lost.
    """
    names: set[str] = set()
    for root in tree.roots:
        if root.name == "runpy":
            names.update(node.name for node in root.iter_subtree())
    return frozenset(names)
