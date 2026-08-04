"""Attribute measured import time to the source statements that caused it.

``-X importtime`` reports per *module* costs.  A module is only paid for once,
by the statement that imported it first, so the cost has to be pushed back onto
that statement:

1. Owned source files are scanned into import statements with candidate name
   sets (see :mod:`importbudget.sources` and :mod:`importbudget.index`).
2. For each measured node, ancestors are climbed until the importing module is
   one we own.  That boundary node's name is looked up in the owner's candidate
   sets, first match wins (see :mod:`importbudget._resolve`).
3. Only **self** time is aggregated.  Cumulative time is carried per row as an
   advisory "potential saving" and must never be summed: in the PoC it
   over-counted the real total by 142%.

Rows that no statement explains are surfaced as explicit ``<dynamic>`` rows
rather than dropped — 19% of a real CLI landed there, and hiding it makes the
table look broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._resolve import ENTRYPOINT_KEY, AttributionKind, _Bucket, _Resolver
from .index import SCRIPT_MODULE, SourceIndex, scan_package, scan_script
from .sources import DynamicImport, ImportStatement, scan_source

if TYPE_CHECKING:
    from .entrypoints import Measurement
    from .importtime import ImportNode

__all__ = [
    "ENTRYPOINT_KEY",
    "SCRIPT_MODULE",
    "Attribution",
    "AttributionKind",
    "AttributionResult",
    "DynamicImport",
    "ImportStatement",
    "SourceIndex",
    "attribute",
    "scan_package",
    "scan_script",
    "scan_source",
]


@dataclass(frozen=True, slots=True)
class Attribution:
    """One row of the attribution table.

    Attributes:
        key: Stable row identifier (``file:line`` for statements).
        kind: Row category.
        self_us: Import time attributed to this row. These sum to the total.
        cumulative_us: Advisory "potential saving" of the boundary modules.
            **Never sum this column**; boundaries nest inside each other.
        modules: Modules rolled up into this row.
        owner: Owning module, when known.
        display_path: Source file, relative to the scan root.
        lineno: 1-based line number, when known.
        source: The source line, when known.
    """

    key: str
    kind: AttributionKind
    self_us: int
    cumulative_us: int
    modules: tuple[str, ...]
    owner: str | None = None
    display_path: str | None = None
    lineno: int | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class AttributionResult:
    """Attribution table for one measurement.

    Attributes:
        rows: Rows ordered by descending attributed self time.
        attributed_us: Sum of :attr:`Attribution.self_us` over all rows.
        filtered_us: Interpreter bootstrap time removed as noise.
        measured_us: Total import time reported by the measurement.
    """

    rows: tuple[Attribution, ...]
    attributed_us: int
    filtered_us: int
    measured_us: int

    @property
    def net_measured_us(self) -> int:
        """Measured time excluding filtered interpreter bootstrap noise."""
        return self.measured_us - self.filtered_us


def attribute(measurement: Measurement, index: SourceIndex) -> AttributionResult:
    """Push each module's self time onto the statement that imported it first.

    Args:
        measurement: Parsed and averaged ``-X importtime`` measurement.
        index: Import statements of the modules we own.

    Returns:
        The attribution table. Rows are sorted by descending self time and
        their ``self_us`` values sum to the measured total minus the filtered
        interpreter bootstrap noise.
    """
    buckets, filtered_us = _accumulate(measurement, index)
    rows = tuple(
        sorted(
            (_freeze(bucket) for bucket in buckets.values()),
            key=lambda row: (-row.self_us, row.key),
        )
    )
    return AttributionResult(
        rows=rows,
        attributed_us=sum(row.self_us for row in rows),
        filtered_us=filtered_us,
        measured_us=measurement.measured_us,
    )


def _accumulate(
    measurement: Measurement,
    index: SourceIndex,
) -> tuple[dict[str, _Bucket], int]:
    """Fold every measured node into its row, returning the filtered noise.

    A boundary's cumulative time is added only the first time that boundary is
    seen, so the advisory column stays the cost of the boundary rather than a
    multiple of it.
    """
    resolver = _Resolver(index)
    buckets: dict[str, _Bucket] = {}
    seen_boundaries: set[int] = set()
    filtered_us = 0
    owned = index.modules
    baseline = measurement.baseline_modules

    for node in measurement.tree.nodes:
        if _is_bootstrap_noise(node, baseline, owned):
            filtered_us += node.self_us
            continue
        boundary, bucket = resolver.resolve(node)
        stored = buckets.setdefault(bucket.key, bucket)
        stored.self_us += node.self_us
        stored.modules.append(node.name)
        if id(boundary) not in seen_boundaries:
            seen_boundaries.add(id(boundary))
            stored.cumulative_us += boundary.cumulative_us
    return buckets, filtered_us


def _freeze(bucket: _Bucket) -> Attribution:
    """Return the immutable row for an accumulated bucket."""
    return Attribution(
        key=bucket.key,
        kind=bucket.kind,
        self_us=bucket.self_us,
        cumulative_us=bucket.cumulative_us,
        modules=tuple(bucket.modules),
        owner=bucket.owner,
        display_path=bucket.display_path,
        lineno=bucket.lineno,
        source=bucket.source,
    )


def _is_bootstrap_noise(
    node: ImportNode,
    baseline: frozenset[str],
    owned: frozenset[str],
) -> bool:
    """Report whether a node is interpreter startup noise.

    A module counts as noise only when a bare interpreter imports it anyway
    *and* nothing we own is on its import chain, so an owned ``import pickle``
    is still charged to our code while the bootstrap copy is dropped.
    """
    if node.name not in baseline:
        return False
    return not any(ancestor.name in owned for ancestor in node.iter_ancestors())
