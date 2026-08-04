"""Capture of whatever the profiled program wrote to its own stderr.

``-X importtime`` shares one stream with the program under test, so parsing has
to keep the two apart.  This value object is the "not ours" half: it exists so
that a report consumer can filter program chatter out, and it is capped because
:mod:`logging` writes to stderr by default and a real CLI would otherwise bury
the table in its own output.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["MAX_STDERR_LINES", "ForeignStderr"]

#: Distinct foreign stderr lines kept before the rest are only counted.
MAX_STDERR_LINES = 10


@dataclass(frozen=True, slots=True)
class ForeignStderr:
    """Output the profiled program itself wrote to stderr.

    Attributes:
        lines: The first :data:`MAX_STDERR_LINES` distinct lines, in order.
        suppressed: Distinct lines dropped after the cap was reached.
    """

    lines: tuple[str, ...] = ()
    suppressed: int = 0

    def __bool__(self) -> bool:
        """Report whether the program wrote anything to stderr."""
        return bool(self.lines) or bool(self.suppressed)

    @classmethod
    def collect(
        cls,
        lines: Iterable[str],
        *,
        limit: int = MAX_STDERR_LINES,
    ) -> ForeignStderr:
        """Deduplicate ``lines``, keep the first ``limit``, count the rest.

        Args:
            lines: Stderr lines in the order the program produced them.
            limit: Maximum number of distinct lines to retain.

        Returns:
            The capped capture.
        """
        kept: list[str] = []
        seen: set[str] = set()
        suppressed = 0
        for line in lines:
            if line in seen:
                continue
            seen.add(line)
            if len(kept) < limit:
                kept.append(line)
            else:
                suppressed += 1
        return cls(lines=tuple(kept), suppressed=suppressed)

    @classmethod
    def merge(
        cls,
        parts: Iterable[ForeignStderr],
        *,
        limit: int = MAX_STDERR_LINES,
    ) -> ForeignStderr:
        """Combine per-run captures, preserving the cap and the dropped count.

        Args:
            parts: One capture per measured run.
            limit: Maximum number of distinct lines to retain.

        Returns:
            The merged capture. ``suppressed`` counts distinct lines, so a run
            that repeats itself does not inflate it.
        """
        collected = list(parts)
        merged = cls.collect(
            chain.from_iterable(part.lines for part in collected), limit=limit
        )
        return cls(
            lines=merged.lines,
            suppressed=merged.suppressed + sum(part.suppressed for part in collected),
        )
