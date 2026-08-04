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

_LOCATE_CODE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.find_spec(sys.argv[1])\n"
    "locations = getattr(spec, 'submodule_search_locations', None) if spec else None\n"
    "print(next(iter(locations)) if locations else (spec.origin if spec else ''))\n"
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
    location = _locate_module(package, python=options.python, cwd=cwd)
    if location is None:
        msg = (
            f"Cannot locate the source of {package!r}. Install it, or run "
            f"importbudget from the directory that contains it."
        )
        raise EntrypointError(msg)

    index = (
        scan_package(location.parent, package)
        if location.is_dir()
        else _index_for_module_file(location, package)
    )
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
) -> Path | None:
    """Ask the profiled interpreter where a module's source lives.

    The lookup runs in the child interpreter (and the same environment as the
    measurement) so that a virtualenv-installed package resolves to the copy
    that will actually be imported. It shares the measurement's own child
    runner so that an unusable interpreter surfaces as a ``MeasurementError``
    here too, rather than as a bare ``OSError`` — this runs *before* the first
    measured process, so it is where a bad ``python`` is noticed.

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
    origin = completed.stdout.strip()
    if completed.returncode != 0 or not origin:
        return None
    path = Path(origin)
    return path if path.exists() else None
