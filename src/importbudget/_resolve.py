"""Map one measured module onto the source statement that imported it.

Split out of :mod:`importbudget.attribute` so that "which row does this node
belong to" stays separate from "what does the finished table look like".

The rule is climb-then-match: ancestors of a measured node are walked until the
*importing* module is one we own, and that boundary node's name is looked up in
the owner's candidate sets.  First match wins, which is what makes a duplicated
import statement receive zero rather than splitting the cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .importtime import ImportNode
    from .index import SourceIndex
    from .sources import DynamicImport, ImportStatement

__all__ = ["ENTRYPOINT_KEY", "AttributionKind"]

#: Row that collects everything the interpreter or the entrypoint itself did.
ENTRYPOINT_KEY = "<entrypoint>"


class AttributionKind(StrEnum):
    """Why a row exists in the attribution table."""

    STATEMENT = "statement"
    """A concrete ``import`` statement in owned source."""

    DYNAMIC = "dynamic"
    """An import no static statement explains (``importlib.import_module``)."""

    ENTRYPOINT = "entrypoint"
    """Work done by the interpreter or the entrypoint itself."""


@dataclass(slots=True)
class _Bucket:
    """Mutable accumulator behind one attribution row."""

    key: str
    kind: AttributionKind
    owner: str | None = None
    display_path: str | None = None
    lineno: int | None = None
    source: str | None = None
    self_us: int = 0
    cumulative_us: int = 0
    modules: list[str] = field(default_factory=list)


class _Resolver:
    """Maps measured nodes to the owning source statement, first match wins."""

    def __init__(self, index: SourceIndex) -> None:
        self._index = index
        self._owned = index.modules
        self._cache: dict[tuple[str, str], ImportStatement | None] = {}

    def resolve(self, node: ImportNode) -> tuple[ImportNode, _Bucket]:
        """Return the boundary node and a fresh bucket describing its row.

        Args:
            node: Measured node to attribute.

        Returns:
            The node whose importer we own, and the row it belongs to.
        """
        boundary = node
        while True:
            parent = boundary.parent
            if parent is None:
                owner = self._index.root_owner
                if owner is None or boundary.name == owner:
                    return boundary, _Bucket(
                        key=ENTRYPOINT_KEY,
                        kind=AttributionKind.ENTRYPOINT,
                    )
                return boundary, self._bucket_for(owner, boundary.name)
            if parent.name in self._owned:
                return boundary, self._bucket_for(parent.name, boundary.name)
            boundary = parent

    def _bucket_for(self, owner: str, imported: str) -> _Bucket:
        """Return the row an owner module's import of ``imported`` belongs to."""
        statement = self._find_statement(owner, imported)
        if statement is not None:
            return _Bucket(
                key=statement.location,
                kind=AttributionKind.STATEMENT,
                owner=owner,
                display_path=statement.display_path,
                lineno=statement.lineno,
                source=statement.source,
            )
        call = self._find_dynamic(owner, imported)
        if call is not None:
            return _Bucket(
                key=f"<dynamic> {call.location}",
                kind=AttributionKind.DYNAMIC,
                owner=owner,
                display_path=call.display_path,
                lineno=call.lineno,
                source=call.source,
            )
        return _Bucket(
            key=f"<dynamic in {owner}>",
            kind=AttributionKind.DYNAMIC,
            owner=owner,
        )

    def _find_statement(self, owner: str, imported: str) -> ImportStatement | None:
        """Find the first executable statement of ``owner`` importing ``imported``.

        The result is cached per (owner, imported) pair, which is what makes a
        duplicated import statement receive zero: the first occurrence wins.
        Module-level statements are preferred over function-level ones, which
        merely *may* have run during import.
        """
        cache_key = (owner, imported)
        if cache_key in self._cache:
            return self._cache[cache_key]
        candidates = [
            stmt
            for stmt in self._index.statements.get(owner, ())
            if not stmt.is_dead and imported in stmt.candidates
        ]
        match = min(
            candidates,
            key=lambda stmt: (not stmt.is_toplevel, stmt.lineno, stmt.col),
            default=None,
        )
        self._cache[cache_key] = match
        return match

    def _find_dynamic(self, owner: str, imported: str) -> DynamicImport | None:
        """Find a dynamic call in ``owner`` whose literal argument matches."""
        return next(
            (
                call
                for call in self._index.dynamic.get(owner, ())
                if call.name == imported
            ),
            None,
        )
