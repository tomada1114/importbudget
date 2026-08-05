"""Tie measurement and attribution together into one profiling session.

This is the entry point the ``profile`` command and the public API share:
locate the source we own, measure the entrypoint, then attribute the measured
self time to statements.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .attribute import attribute
from .errors import EntrypointError
from .index import SourceIndex, scan_package, scan_script
from .measure import (
    Entrypoint,
    EntrypointKind,
    RunOptions,
    build_child_env,
    measure,
    run_child,
)
from .sources import scan_source
from .stderr import ForeignStderr

if TYPE_CHECKING:
    from .attribute import Attribution, AttributionResult
    from .measure import Measurement

__all__ = ["ProfileResult", "profile"]

# Prints one absolute location per line: every portion of a namespace package,
# in import order, or the single origin of an ordinary module. Paths are made
# absolute in the child, whose working directory is the one being profiled.
_LOCATE_CODE = (
    "import importlib.util, os, sys\n"
    "spec = importlib.util.find_spec(sys.argv[1])\n"
    "found = getattr(spec, 'submodule_search_locations', None) if spec else None\n"
    "paths = list(found or ())\n"
    "if not paths and spec and spec.origin:\n"
    "    paths = [spec.origin]\n"
    "print('\\n'.join(os.path.abspath(path) for path in paths))\n"
)


@dataclass(frozen=True, slots=True)
class ProfileResult:
    """Everything one ``importbudget profile`` run produced.

    Attributes:
        entrypoint: What was profiled.
        measurement: Mean measurement and the environment it ran in.
        attribution: Statement-level attribution table.
        source_root: Directory the reported source paths are relative to.
        warnings: importbudget's own measurement and source-scan diagnostics,
            deduplicated.
        stderr: What the profiled program itself wrote to stderr, kept apart
            from ``warnings`` so consumers can filter it out.
    """

    entrypoint: Entrypoint
    measurement: Measurement
    attribution: AttributionResult
    source_root: Path | None = None
    warnings: tuple[str, ...] = ()
    stderr: ForeignStderr = field(default_factory=ForeignStderr)

    def top(self, count: int) -> tuple[Attribution, ...]:
        """Return the ``count`` most expensive rows.

        Args:
            count: Number of rows; non-positive means all of them.

        Returns:
            The leading rows of the attribution table.
        """
        if count <= 0:
            return self.attribution.rows
        return self.attribution.rows[:count]


def profile(
    entrypoint: Entrypoint | str,
    options: RunOptions | None = None,
) -> ProfileResult:
    """Profile an entrypoint and attribute its import time to statements.

    Args:
        entrypoint: Module name, script path, or a prepared
            :class:`~importbudget.entrypoints.Entrypoint`.
        options: Run counts and environment overrides.

    Returns:
        The measurement plus the attribution table.

    Raises:
        EntrypointError: The entrypoint's source could not be located.
        MeasurementError: The interpreter could not be started, or the
            entrypoint produced unusable importtime output.
    """
    opts = options or RunOptions()
    cwd = opts.cwd or Path.cwd()
    target = (
        Entrypoint.parse(entrypoint, cwd=cwd)
        if isinstance(entrypoint, str)
        else entrypoint
    )

    index = _build_index(target, options=opts, cwd=cwd)
    measurement = measure(target, opts)
    result = attribute(measurement, index)
    warnings = tuple(dict.fromkeys(measurement.warnings + index.warnings))
    return ProfileResult(
        entrypoint=target,
        measurement=measurement,
        attribution=result,
        source_root=index.root,
        warnings=warnings,
        stderr=measurement.stderr,
    )


def _build_index(
    entrypoint: Entrypoint,
    *,
    options: RunOptions,
    cwd: Path,
) -> SourceIndex:
    """Scan the source files whose import statements we can attribute to."""
    if entrypoint.kind is EntrypointKind.SCRIPT:
        return _index_for_script(cwd / entrypoint.target)

    package = entrypoint.top_level_package
    if package is None:  # pragma: no cover - only scripts lack a package
        msg = f"Entrypoint {entrypoint.target!r} has no owning package."
        raise EntrypointError(msg)
    locations = _locate_module(package, python=options.python, cwd=cwd)
    if not locations:
        msg = (
            f"Cannot locate the source of {package!r}. Install it, or run "
            f"importbudget from the directory that contains it."
        )
        raise EntrypointError(msg)

    # A namespace package has one location per portion; scanning only the first
    # would leave every module under the others unowned, and so unattributed.
    index = SourceIndex()
    for location in locations:
        portion = (
            scan_package(location.parent, package)
            if location.is_dir()
            else _index_for_module_file(location, package)
        )
        index = index.merged_with(portion)
    return _with_root_owner(index, entrypoint)


def _with_root_owner(index: SourceIndex, entrypoint: Entrypoint) -> SourceIndex:
    """Attach the module that owns the root rows for ``-m`` entrypoints.

    ``python -m pkg.cli`` executes the target as ``__main__``, so its own
    imports appear as root rows with no parent. Naming the module makes those
    rows attributable to its source lines instead of ``<entrypoint>``.
    """
    if entrypoint.kind is not EntrypointKind.MODULE_RUN:
        return index
    candidates = (entrypoint.target, f"{entrypoint.target}.__main__")
    owner = next((name for name in candidates if name in index.statements), None)
    if owner is None:
        return index
    return SourceIndex(
        statements=index.statements,
        dynamic=index.dynamic,
        root_owner=owner,
        root=index.root,
        warnings=index.warnings,
    )


def _index_for_script(script: Path) -> SourceIndex:
    """Index a script plus any package sitting next to it."""
    if not script.is_file():
        msg = f"Script entrypoint not found: {script}"
        raise EntrypointError(msg)
    root = script.parent
    index = scan_script(script, root=root)
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "__init__.py").is_file():
            index = index.merged_with(scan_package(root, child.name))
    return index


def _index_for_module_file(path: Path, module: str) -> SourceIndex:
    """Index a single-file module (``pkg.py`` rather than ``pkg/``)."""
    root = path.parent
    statements, dynamic = scan_source(path, module, root=root)
    return SourceIndex(
        statements={module: statements},
        dynamic={module: dynamic} if dynamic else {},
        root=root,
    )


def _locate_module(
    module: str,
    *,
    python: str | None,
    cwd: Path,
) -> tuple[Path, ...]:
    """Ask the profiled interpreter where a module's source lives.

    The lookup runs in the child interpreter (and the same environment as the
    measurement) so that a virtualenv-installed package resolves to the copy
    that will actually be imported. It shares the measurement's own child
    runner so that an unusable interpreter surfaces as a ``MeasurementError``
    here too, rather than as a bare ``OSError`` — this runs *before* the first
    measured process, so it is where a bad ``python`` is noticed.

    Returns:
        Every existing location, in import order: one entry per portion of a
        namespace package, a single entry for an ordinary module or package,
        and nothing at all when the module cannot be found.

    Raises:
        MeasurementError: The interpreter could not be started at all.
    """
    interpreter = python or sys.executable
    completed = run_child(
        interpreter,
        ["-c", _LOCATE_CODE, module],
        cwd=cwd,
        env=build_child_env(cwd),
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return ()
    # The same directory can be reached by two sys.path entries (``''`` and an
    # absolute copy of the working directory), so duplicates are dropped.
    origins = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return tuple(path for path in map(Path, dict.fromkeys(origins)) if path.exists())
