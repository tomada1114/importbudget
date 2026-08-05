"""Read the facts ``verify`` needs out of a saved plan document.

:func:`~importbudget.codemod.apply` already decodes the plan's *statements*,
and re-decoding them here would mean two readers to keep in step.  This module
reads only the envelope around them: what was profiled, where its source lives,
and what the plan predicted — the things a before/after comparison has to know
but a codemod does not.

The predicted saving is carried through untouched.  It is a set-dependent upper
bound, and ``verify`` exists precisely to measure how far from it reality
landed, so nothing here tries to correct it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .entrypoints import Entrypoint, EntrypointKind
from .errors import VerifyInputError
from .report import PLAN_DOCUMENT

__all__ = ["PlanFacts", "load_plan_facts"]


@dataclass(frozen=True, slots=True)
class PlanFacts:
    """What the plan document says about the run it was built from.

    Attributes:
        entrypoint: The entrypoint to re-measure on both source trees.
        source_root: Directory the plan's file paths are relative to, and the
            tree that is copied to build the before and after scratch trees.
        predicted_saving_us: The plan's predicted saving; an upper bound.
        attributed_us: Total attributed time of the plan's profile.
        unaddressable_us: Time no statement conversion can remove, because it
            sits on the entrypoint itself or on dynamic imports.
    """

    entrypoint: Entrypoint
    source_root: Path
    predicted_saving_us: int = 0
    attributed_us: int = 0
    unaddressable_us: int = 0


def load_plan_facts(path: Path) -> PlanFacts:
    """Decode the envelope of a plan document.

    Args:
        path: File written by ``importbudget plan --json``.

    Returns:
        The entrypoint, its source root, and the plan's predicted totals.

    Raises:
        VerifyInputError: The file is unreadable, is not a plan document, or
            names no entrypoint and source root to re-measure.
    """
    document = _read(path)
    if document.get("document") != PLAN_DOCUMENT:
        found = document.get("document", "<missing>")
        msg = f"{path} is not a plan document (document: {found!r})"
        raise VerifyInputError(msg)

    entrypoint = _entrypoint(document, path)
    totals = _mapping(document.get("totals"))
    return PlanFacts(
        entrypoint=entrypoint,
        source_root=_source_root(document, path),
        predicted_saving_us=_non_negative_int(totals.get("predicted_saving_us")),
        attributed_us=_non_negative_int(totals.get("attributed_us")),
        unaddressable_us=_non_negative_int(totals.get("unaddressable_us")),
    )


def _read(path: Path) -> dict[str, Any]:
    """Load the document, naming the file in every failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        msg = f"cannot read the plan document {path}: {error}"
        raise VerifyInputError(msg) from error
    try:
        # Any: the document is untrusted JSON; every field is checked below.
        document: Any = json.loads(text)
    except json.JSONDecodeError as error:
        msg = f"{path} is not valid JSON: {error}"
        raise VerifyInputError(msg) from error
    if not isinstance(document, dict):
        msg = f"{path} does not hold a JSON object"
        raise VerifyInputError(msg)
    return document


def _entrypoint(document: dict[str, Any], path: Path) -> Entrypoint:
    """Rebuild the profiled entrypoint from the document's header.

    A script entrypoint is reduced to its file name on purpose: the plan's
    ``source_root`` *is* the directory that script sits in, and the scratch
    trees are copies of that directory, so the path the plan recorded relative
    to some other working directory would not resolve inside them.
    """
    entrypoint = _mapping(document.get("entrypoint"))
    target = entrypoint.get("target")
    if not isinstance(target, str) or not target:
        msg = f"{path} names no entrypoint to verify"
        raise VerifyInputError(msg)
    try:
        kind = EntrypointKind(entrypoint.get("kind", EntrypointKind.MODULE))
    except ValueError as error:
        msg = f"{path} names an unknown entrypoint kind: {entrypoint.get('kind')!r}"
        raise VerifyInputError(msg) from error
    if kind is EntrypointKind.SCRIPT:
        target = Path(target).name
    return Entrypoint(target=target, kind=kind)


def _source_root(document: dict[str, Any], path: Path) -> Path:
    """Return the directory the before and after scratch trees are copied from."""
    root = _mapping(document.get("entrypoint")).get("source_root")
    if not isinstance(root, str) or not root:
        msg = (
            f"{path} records no source_root, so there is no source tree to "
            f"copy and re-measure"
        )
        raise VerifyInputError(msg)
    resolved = Path(root)
    if not resolved.is_dir():
        msg = f"the plan's source_root {root} is not a directory"
        raise VerifyInputError(msg)
    return resolved


def _mapping(value: object) -> dict[str, Any]:
    """Return a JSON object, or an empty one for anything else."""
    return value if isinstance(value, dict) else {}


def _non_negative_int(value: object) -> int:
    """Return a non-negative integer field, treating anything else as zero."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
