"""Recover a small delta by normalizing against a subtree nothing changed.

A 4 ms difference on a 110 ms startup is invisible in raw totals: the PoC got
its sign right in 7 of 15 paired runs, which is a coin toss.  The same 15 pairs
recovered it every time once each total was expressed in units of an *unchanged*
subtree measured in the same run — machine load moves the reference and the
total together, so dividing one by the other cancels most of it.  That matches
the other PoC finding, that percentage-of-total shares were 3-4x more stable
than absolute milliseconds.

A subtree qualifies as a reference only when it is structurally identical in
every run of both sides: same node, same set of modules underneath it.  Two
consequences fall out of that and are worth stating, because both were ways of
getting this wrong:

* a subtree containing anything the conversion touched is rejected
  automatically, since making an import lazy removes rows from underneath it;
* a dynamically imported module can never be chosen, because
  ``importlib.import_module`` emits no row of its own — its transitive imports
  nest under whichever module called it — so there is no node to isolate.
"""

from __future__ import annotations

from statistics import fmean
from typing import TYPE_CHECKING

from .verifies import Comparison, ComparisonKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .importtime import ImportTree

__all__ = ["MIN_REFERENCE_US", "normalize"]

#: Smallest reference subtree worth dividing by. Below this the reference's own
#: jitter is a larger fraction of itself than the noise it would cancel.
MIN_REFERENCE_US = 1_000


def normalize(
    before: Sequence[tuple[ImportTree, int]],
    after: Sequence[tuple[ImportTree, int]],
) -> Comparison | None:
    """Rescale paired totals by the largest subtree the conversion left alone.

    Args:
        before: One ``(tree, net total us)`` pair per measured before-run.
        after: The same for the after-runs, in the matching order.

    Returns:
        The normalized comparison, or ``None`` when no subtree was structurally
        identical across every run and large enough to divide by.
    """
    trees = [tree for tree, _ in before] + [tree for tree, _ in after]
    reference = _pick_reference(trees)
    if reference is None:
        return None

    costs = [_cumulative(tree, reference) for tree in trees]
    scale = fmean(costs)
    split = len(before)
    return Comparison(
        kind=ComparisonKind.NORMALIZED,
        before=tuple(
            total / cost * scale
            for (_, total), cost in zip(before, costs[:split], strict=True)
        ),
        after=tuple(
            total / cost * scale
            for (_, total), cost in zip(after, costs[split:], strict=True)
        ),
        reference=reference,
    )


def _pick_reference(trees: Sequence[ImportTree]) -> str | None:
    """Return the costliest module whose subtree is identical in every tree."""
    shapes = [_shapes(tree) for tree in trees]
    if not shapes:
        return None

    first, *rest = shapes
    shared = {
        name: shape
        for name, shape in first.items()
        if all(other.get(name) == shape for other in rest)
    }
    if not shared:
        return None

    costs = {
        name: fmean([_cumulative(tree, name) for tree in trees]) for name in shared
    }
    best = max(costs, key=lambda name: costs[name])
    return best if costs[best] >= MIN_REFERENCE_US else None


def _shapes(tree: ImportTree) -> dict[str, frozenset[str]]:
    """Map each uniquely named node to the set of modules under it.

    A name appearing twice is dropped rather than merged: two nodes cannot be
    told apart across runs by name alone, and picking the wrong one would
    silently normalize against a different subtree on each side.
    """
    seen: dict[str, frozenset[str] | None] = {}
    for node in tree.nodes:
        if node.name in seen:
            seen[node.name] = None
            continue
        seen[node.name] = frozenset(child.name for child in node.iter_subtree())
    return {name: shape for name, shape in seen.items() if shape is not None}


def _cumulative(tree: ImportTree, name: str) -> int:
    """Return the cumulative time of the node called ``name``, or 0 if absent."""
    return next(
        (node.cumulative_us for node in tree.nodes if node.name == name),
        0,
    )
