"""Parse the ``-X importtime`` stderr stream into an import tree.

Three facts about that stream drive this module and are easy to get wrong:

* Rows are printed in **post-order** — a module appears after its children.
* The module column is indented **two spaces per nesting level** (CPython
  prints ``%*s`` with width ``import_level * 2``).  Assuming one space silently
  produces a flat forest with no children and no error.
* Parent packages nest *under* the submodule node: ``from rich.console import
  Console`` yields node ``rich.console`` with ``rich`` as its child.

The profiled program writes to the same stream, so it is split into three
channels instead of one:

* rows matching CPython's fixed-width ``import time: %9ld | %10ld | `` layout
  *and* numerically possible become tree nodes;
* :attr:`ImportTree.warnings` carries importbudget's own diagnostics — an
  impossible row, an odd indentation width, a subtree whose cumulative time
  does not add up;
* :attr:`ImportTree.stderr` carries the program's own output, deduplicated and
  capped so a chatty entrypoint cannot flood the report.

What this does **not** provide is forgery-proofing.  A line reproducing the
exact column widths that is also internally consistent — ``self == cumulative``
on a leaf row — is indistinguishable from a real one, because nothing else in
the stream corroborates it.  The checks below reject *implausible* rows, not
adversarial ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .errors import MeasurementError
from .stderr import MAX_STDERR_LINES, ForeignStderr

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

__all__ = [
    "INDENT_WIDTH",
    "MAX_STDERR_LINES",
    "NODE_TOLERANCE_US",
    "TOTAL_TOLERANCE",
    "ForeignStderr",
    "ImportNode",
    "ImportTree",
    "parse_importtime",
    "validate_totals",
]

_HEADER_RE = re.compile(r"^import time:\s+self \[us\] \|")
_ROW_RE = re.compile(r"^import time: ( *\d+) \| ( *\d+) \| (\s*)(\S.*)$")

#: CPython indents the module column by this many spaces per nesting level.
INDENT_WIDTH = 2

#: Field widths of CPython's ``"import time: %9ld | %10ld | %*s%s"`` row.
_SELF_WIDTH = 9
_CUMULATIVE_WIDTH = 10

#: Allowed relative gap between the sum of self times and the root cumulative.
TOTAL_TOLERANCE = 0.01

#: Slack for the per-node ``cumulative == self + sum(child cumulative)`` check.
#: CPython truncates every duration to whole microseconds and times a parent
#: independently of its children, so genuine rows drift a little; measured
#: captures stay within 4us, while mis-nesting is orders of magnitude larger.
NODE_TOLERANCE_US = 16


@dataclass(slots=True)
class ImportNode:
    """One ``-X importtime`` row, linked into the reconstructed import tree.

    Attributes:
        name: Dotted module name as printed by CPython.
        self_us: Time spent importing this module alone, in microseconds.
        cumulative_us: Self time plus the time of everything it imported.
        depth: Nesting level, derived from the row indentation.
        parent: Importing module, or ``None`` for a root row.
        children: Modules imported while this module was being imported.
    """

    name: str
    self_us: int
    cumulative_us: int
    depth: int
    parent: ImportNode | None = None
    children: list[ImportNode] = field(default_factory=list)

    def iter_subtree(self) -> Iterator[ImportNode]:
        """Yield this node and every descendant, parents before children."""
        yield self
        for child in self.children:
            yield from child.iter_subtree()

    def iter_ancestors(self) -> Iterator[ImportNode]:
        """Yield the importing modules, nearest first."""
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    @property
    def subtree_us(self) -> int:
        """Cumulative time this node's own rows account for."""
        return self.self_us + sum(child.cumulative_us for child in self.children)


@dataclass(frozen=True, slots=True)
class ImportTree:
    """A parsed ``-X importtime`` stream.

    Attributes:
        roots: Modules imported directly by the interpreter or the entrypoint.
        nodes: Every node, in the order CPython printed them (post-order).
        warnings: importbudget's own parse diagnostics.
        stderr: The profiled program's own stderr output.
    """

    roots: tuple[ImportNode, ...]
    nodes: tuple[ImportNode, ...]
    warnings: tuple[str, ...] = ()
    stderr: ForeignStderr = field(default_factory=ForeignStderr)

    @property
    def total_self_us(self) -> int:
        """Sum of every node's self time."""
        return sum(node.self_us for node in self.nodes)

    @property
    def total_root_cumulative_us(self) -> int:
        """Sum of the cumulative time of the roots: the measured total."""
        return sum(node.cumulative_us for node in self.roots)

    def names(self) -> frozenset[str]:
        """Return the set of module names in the tree."""
        return frozenset(node.name for node in self.nodes)


def parse_importtime(stderr_text: str) -> ImportTree:
    """Rebuild the import tree from a ``-X importtime`` stderr capture.

    Rows arrive post-order, so children are held in a pending list until their
    parent shows up one level shallower — one pass, no re-scanning.  Nothing
    here aborts parsing: a line that is not a well-formed row becomes program
    output, and a row whose numbers are impossible becomes a warning.

    Args:
        stderr_text: Raw stderr of a ``python -X importtime`` run.

    Returns:
        The reconstructed tree, the parse diagnostics, and the program's own
        stderr output.
    """
    builder = _TreeBuilder()
    for raw in stderr_text.splitlines():
        if _HEADER_RE.match(raw):
            continue
        match = _ROW_RE.match(raw)
        if match is None or not _has_row_widths(match):
            if raw.strip():
                builder.foreign.append(raw.strip())
            continue
        builder.add(match, raw.strip())
    return builder.finish()


def validate_totals(tree: ImportTree, *, tolerance: float = TOTAL_TOLERANCE) -> None:
    """Assert that the tree accounts for all of the measured time.

    ``sum(self)`` must equal ``sum(root cumulative)`` up to microsecond
    rounding.  A larger gap means the tree was mis-nested, which would silently
    corrupt every attribution built on top of it, so this is the cheapest
    available detector for a parser regression.

    Args:
        tree: Tree to check.
        tolerance: Allowed relative difference.

    Raises:
        MeasurementError: The stream had no rows, or the totals disagree.
    """
    if not tree.nodes:
        msg = "No `-X importtime` rows were produced by the entrypoint."
        raise MeasurementError(msg)

    root_cumulative = tree.total_root_cumulative_us
    if root_cumulative <= 0:
        msg = "The measured import tree has a non-positive cumulative time."
        raise MeasurementError(msg)

    error = abs(tree.total_self_us - root_cumulative) / root_cumulative
    if error > tolerance:
        msg = (
            f"Import tree is inconsistent: sum(self)={tree.total_self_us}us but "
            f"sum(root cumulative)={root_cumulative}us "
            f"({error:.2%} > {tolerance:.2%}). The importtime output format "
            f"may have changed."
        )
        raise MeasurementError(msg)


class _TreeBuilder:
    """Accumulates rows into a tree; children wait for their later parent."""

    def __init__(self) -> None:
        self.pending: list[ImportNode] = []
        self.nodes: list[ImportNode] = []
        self.warnings: list[str] = []
        self.foreign: list[str] = []

    def add(self, match: re.Match[str], raw: str) -> None:
        """Attach one matched row to the tree under construction.

        Args:
            match: Row match produced by the importtime row pattern.
            raw: The stripped source line, quoted in any diagnostic.
        """
        self_text, cumulative_text, indent, name = match.groups()
        self_us, cumulative_us = int(self_text), int(cumulative_text)
        if self_us > cumulative_us:
            self.warnings.append(
                f"discarded an impossible importtime row (self > cumulative): {raw}"
            )
            return
        if len(indent) % INDENT_WIDTH:
            self.warnings.append(f"unexpected odd indentation in row: {raw}")
        node = ImportNode(
            name=name.strip(),
            self_us=self_us,
            cumulative_us=cumulative_us,
            depth=len(indent) // INDENT_WIDTH,
        )
        self._adopt(node)
        self.pending.append(node)
        self.nodes.append(node)

    def finish(self) -> ImportTree:
        """Return the finished tree, after checking each subtree's arithmetic."""
        root_depth = min((node.depth for node in self.pending), default=0)
        roots = tuple(node for node in self.pending if node.depth == root_depth)
        self.warnings.extend(_inconsistent_subtrees(self.nodes))
        return ImportTree(
            roots=roots,
            nodes=tuple(self.nodes),
            warnings=tuple(self.warnings),
            stderr=ForeignStderr.collect(self.foreign),
        )

    def _adopt(self, node: ImportNode) -> None:
        """Move every pending row one level deeper underneath ``node``."""
        child_depth = node.depth + 1
        children = [pend for pend in self.pending if pend.depth == child_depth]
        if not children:
            return
        self.pending = [pend for pend in self.pending if pend.depth != child_depth]
        for child in children:
            child.parent = node
        node.children = children


def _has_row_widths(match: re.Match[str]) -> bool:
    """Report whether a matched row uses CPython's fixed column widths."""
    return _is_padded(match.group(1), _SELF_WIDTH) and _is_padded(
        match.group(2), _CUMULATIVE_WIDTH
    )


def _is_padded(field_text: str, width: int) -> bool:
    """Report whether ``field_text`` looks like a ``%<width>ld`` output field."""
    return len(field_text) == max(width, len(field_text.lstrip(" ")))


def _inconsistent_subtrees(nodes: Iterable[ImportNode]) -> list[str]:
    """Report every node whose cumulative time does not match its own subtree.

    A genuine row satisfies ``cumulative == self + sum(child cumulative)`` to
    within :data:`NODE_TOLERANCE_US`. A larger gap means the row was fabricated
    by the profiled program or the tree was mis-nested; the row is kept so the
    totals still balance, but the numbers below it cannot be trusted.
    """
    return [
        f"module {node.name!r} does not add up: cumulative={node.cumulative_us}us "
        f"but self + children={node.subtree_us}us; the rows below it may be "
        f"fabricated or mis-nested"
        for node in nodes
        if abs(node.subtree_us - node.cumulative_us) > NODE_TOLERANCE_US
    ]
