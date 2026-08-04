"""Value objects describing a profiling run.

What to start (:class:`Entrypoint`), how to run it (:class:`RunOptions`), and
what came back (:class:`Measurement`).  Kept apart from
:mod:`importbudget.measure`, which owns the child processes, so that the shapes
crossing the public API carry no subprocess machinery with them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import EntrypointError
from .importtime import ForeignStderr, ImportTree

__all__ = [
    "DEFAULT_RUNS",
    "DEFAULT_WARMUP_RUNS",
    "Entrypoint",
    "EntrypointKind",
    "Measurement",
    "RunOptions",
]

_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")

#: Measured runs kept by default; the reported tree is their mean.
DEFAULT_RUNS = 3

#: Runs discarded before measuring, to pay the cold page cache once.
DEFAULT_WARMUP_RUNS = 1


class EntrypointKind(StrEnum):
    """How the entrypoint is handed to the interpreter."""

    MODULE = "module"
    """Imported with ``-c "import <target>"``."""

    MODULE_RUN = "module-run"
    """Executed with ``-m <target>``."""

    SCRIPT = "script"
    """Executed as a script path."""


@dataclass(frozen=True, slots=True)
class Entrypoint:
    """What to start under the profiler.

    Attributes:
        target: Dotted module name, or a path for :attr:`EntrypointKind.SCRIPT`.
        kind: How ``target`` is executed.
    """

    target: str
    kind: EntrypointKind = EntrypointKind.MODULE

    @classmethod
    def parse(
        cls,
        target: str,
        *,
        run_module: bool = False,
        cwd: Path | None = None,
    ) -> Entrypoint:
        """Classify a command-line entrypoint.

        Args:
            target: Module name or script path.
            run_module: Force ``-m`` style execution.
            cwd: Directory a relative script path is resolved against.

        Returns:
            The classified entrypoint.

        Raises:
            EntrypointError: The target is neither a readable script nor a
                dotted module name.
        """
        base = cwd or Path.cwd()
        if run_module:
            kind = EntrypointKind.MODULE_RUN
        elif target.endswith(".py") or (base / target).is_file():
            kind = EntrypointKind.SCRIPT
        else:
            kind = EntrypointKind.MODULE

        if kind is EntrypointKind.SCRIPT:
            if not (base / target).is_file():
                msg = f"Script entrypoint not found: {target}"
                raise EntrypointError(msg)
        elif not _DOTTED_NAME_RE.match(target):
            msg = f"Entrypoint {target!r} is not an importable module name."
            raise EntrypointError(msg)
        return cls(target=target, kind=kind)

    @property
    def command_args(self) -> list[str]:
        """Interpreter arguments that start this entrypoint."""
        match self.kind:
            case EntrypointKind.MODULE:
                return ["-c", f"import {self.target}"]
            case EntrypointKind.MODULE_RUN:
                return ["-m", self.target]
            case EntrypointKind.SCRIPT:
                return [self.target]

    @property
    def top_level_package(self) -> str | None:
        """Owning top-level package, or ``None`` for a script entrypoint."""
        if self.kind is EntrypointKind.SCRIPT:
            return None
        return self.target.split(".")[0]


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Execution knobs for a profiling session.

    Attributes:
        runs: Measured runs; the reported tree is their mean.
        warmup: Runs executed and discarded first (cold page cache).
        cwd: Working directory for the child interpreter.
        python: Interpreter to profile with; defaults to ``sys.executable``.
    """

    runs: int = DEFAULT_RUNS
    warmup: int = DEFAULT_WARMUP_RUNS
    cwd: Path | None = None
    python: str | None = None

    def __post_init__(self) -> None:
        """Reject run counts that cannot produce a measurement.

        Raises:
            ValueError: ``runs`` is below 1 or ``warmup`` is negative.
        """
        if self.runs < 1:
            msg = f"runs must be >= 1, got {self.runs}"
            raise ValueError(msg)
        if self.warmup < 0:
            msg = f"warmup must be >= 0, got {self.warmup}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Measurement:
    """Mean of several ``-X importtime`` runs plus the environment it ran in.

    Attributes:
        entrypoint: What was profiled.
        tree: Mean import tree over the measured runs.
        baseline_modules: Modules a bare interpreter imports anyway, plus the
            ``runpy`` overhead for ``-m`` entrypoints.
        runs: Number of measured runs.
        warmup_runs: Number of discarded runs.
        returncodes: Exit status of each measured run.
        python_executable: Interpreter used for the child processes.
        python_version: Version reported by that interpreter.
        platform: Host platform description.
        warnings: Deduplicated importbudget diagnostics from every run.
        stderr: What the profiled program itself wrote to stderr, merged
            across runs and capped.
    """

    entrypoint: Entrypoint
    tree: ImportTree
    baseline_modules: frozenset[str]
    runs: int
    warmup_runs: int
    returncodes: tuple[int, ...]
    python_executable: str
    python_version: str
    platform: str
    warnings: tuple[str, ...] = ()
    stderr: ForeignStderr = field(default_factory=ForeignStderr)

    @property
    def measured_us(self) -> int:
        """Total import time of the entrypoint, in microseconds."""
        return self.tree.total_root_cumulative_us
