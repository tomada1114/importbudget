"""Average several ``-X importtime`` runs into one tree.

Two rules make the mean usable rather than merely plausible:

* Each module is averaged over the runs that actually imported it, so a
  conditional import is reported at its real cost rather than diluted by the
  runs that skipped it.
* The averaged tree must still satisfy ``sum(self) == sum(root cumulative)``.
  Rounding each mean on its own leaves up to a microsecond of residual per
  module, which is enough to push the attributed total *above* the measured
  total and print a share over 100%.  The residual is therefore handed to the
  largest fractional parts and cumulative times are rebuilt from the rounded
  self times, making the identity exact instead of approximate.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .importtime import ImportNode, ImportTree

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["mean_tree"]


def mean_tree(trees: Sequence[ImportTree]) -> ImportTree:
    """Average per-module times across runs, keeping the richest run's shape.

    Args:
        trees: One parsed tree per measured run; at least one.

    Returns:
        The mean tree. Its self times sum exactly to its root cumulative times,
        so anything derived from it can be compared against the measured total
        without a rounding fudge.
    """
    if len(trees) == 1:
        return trees[0]

    totals = _totals(trees)
    skeleton = max(trees, key=lambda tree: len(tree.nodes))
    nodes: list[ImportNode] = []
    roots = tuple(_clone_shape(root, nodes) for root in skeleton.roots)
    means = [totals[node.name][0] / totals[node.name][1] for node in nodes]
    for node, self_us in zip(nodes, largest_remainder(means), strict=True):
        node.self_us = self_us
        node.cumulative_us = node.subtree_us
    return ImportTree(
        roots=roots,
        nodes=tuple(nodes),
        warnings=tuple(_missing_module_warnings(totals, skeleton)),
    )


def largest_remainder(values: Sequence[float]) -> list[int]:
    """Round ``values`` to integers that still sum to ``round(sum(values))``.

    Args:
        values: Non-negative means, one per module.

    Returns:
        The rounded values, each within one microsecond of its input.
    """
    floors = [math.floor(value) for value in values]
    shortfall = round(math.fsum(values)) - sum(floors)
    if shortfall <= 0:
        return floors
    ranked = sorted(
        range(len(values)), key=lambda index: (floors[index] - values[index], index)
    )
    for index in ranked[:shortfall]:
        floors[index] += 1
    return floors


def _totals(trees: Sequence[ImportTree]) -> dict[str, list[int]]:
    """Return ``module -> [summed self time, number of runs that imported it]``."""
    totals: dict[str, list[int]] = {}
    for tree in trees:
        for node in tree.nodes:
            entry = totals.setdefault(node.name, [0, 0])
            entry[0] += node.self_us
            entry[1] += 1
    return totals


def _missing_module_warnings(
    totals: Mapping[str, list[int]],
    skeleton: ImportTree,
) -> list[str]:
    """Report modules that some run imported but the reference run did not."""
    missing = sorted(set(totals) - skeleton.names())
    if not missing:
        return []
    return [
        f"module set differed between runs; ignoring {len(missing)} module(s) "
        f"absent from the reference run: {', '.join(missing[:5])}"
    ]


def _clone_shape(node: ImportNode, collected: list[ImportNode]) -> ImportNode:
    """Copy a subtree's shape, collecting clones post-order (children first)."""
    clone = ImportNode(name=node.name, self_us=0, cumulative_us=0, depth=node.depth)
    for child in node.children:
        child_clone = _clone_shape(child, collected)
        child_clone.parent = clone
        clone.children.append(child_clone)
    collected.append(clone)
    return clone
