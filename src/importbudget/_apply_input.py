"""Read a saved plan JSON back into the shapes the codemod works on.

``apply`` is the one command that edits the user's source, so it trusts nothing
it is handed.  Only statements whose ``verdict`` is exactly ``"safe"`` become
targets, every missing or mistyped field raises
:class:`~importbudget.errors.ApplyInputError` naming it, and a ``file`` that
escapes the document's ``source_root`` is refused rather than resolved.

The plan is a *ranked subset*, never an exhaustive list of convertible
statements: its rows come from costed attribution, so a safe statement with no
measured cost (a duplicate import, an already-loaded module) never appears.
Nothing here tries to make up the difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ApplyInputError
from .report import PLAN_DOCUMENT, SCHEMA_VERSION

__all__ = ["PlanTarget", "load_plan_document"]

#: ``verdict`` value of a statement every safety rule stayed silent on.
SAFE_VERDICT = "safe"


@dataclass(frozen=True, slots=True)
class PlanTarget:
    """One statement the plan proved safe to make lazy.

    Attributes:
        key: ``file:line``, carried through to the apply report unchanged.
        path: Absolute path of the source file, resolved under ``source_root``.
        display_path: The document's ``file`` value.
        lineno: 1-based line the statement started on when the plan was made.
        source: The source line the plan recorded, used to detect a file that
            moved on since.
        bound_names: Module-global names the statement binds.
    """

    key: str
    path: Path
    display_path: str
    lineno: int
    source: str
    bound_names: tuple[str, ...]


def load_plan_document(path: Path) -> tuple[tuple[PlanTarget, ...], Path | None]:
    """Decode a plan JSON document into codemod targets.

    Args:
        path: File written by ``importbudget plan --json``.

    Returns:
        The safe statements, in document order, and the source root their
        paths were resolved against.

    Raises:
        ApplyInputError: The file is unreadable, is not JSON, is not a plan
            document of a supported schema version, or names a source file
            outside its own source root.
    """
    document = _read(path)
    _check_kind(document, path)
    root = _source_root(document)
    targets = tuple(
        target
        for item in _sequence(document, "statements")
        if (target := _target(item, root)) is not None
    )
    return targets, root


def _read(path: Path) -> dict[str, Any]:
    """Load the document, turning every I/O and syntax failure into our error."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"could not read plan document {path}: {exc}"
        raise ApplyInputError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise ApplyInputError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} is not an importbudget plan document (expected an object)"
        raise ApplyInputError(msg)
    return loaded


def _check_kind(document: dict[str, Any], path: Path) -> None:
    """Reject documents of the wrong kind or an unsupported schema version."""
    kind = document.get("document")
    if kind != PLAN_DOCUMENT:
        msg = f"{path} is a {kind!r} document, not a plan"
        raise ApplyInputError(msg)
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        msg = (
            f"{path} has schema_version {version!r}; this importbudget "
            f"understands {SCHEMA_VERSION}"
        )
        raise ApplyInputError(msg)


def _source_root(document: dict[str, Any]) -> Path | None:
    """Return the directory the document's ``file`` values are relative to."""
    entrypoint = document.get("entrypoint")
    root = entrypoint.get("source_root") if isinstance(entrypoint, dict) else None
    return Path(root).resolve() if isinstance(root, str) else None


def _target(item: Any, root: Path | None) -> PlanTarget | None:
    """Build one target, or return ``None`` for a statement to leave alone."""
    if not isinstance(item, dict):
        msg = f"statements[] entries must be objects, got {type(item).__name__}"
        raise ApplyInputError(msg)
    if item.get("verdict") != SAFE_VERDICT:
        return None
    display_path = _text(item, "file")
    lineno = _number(item, "line")
    return PlanTarget(
        key=_text(item, "key"),
        path=_resolve(display_path, root),
        display_path=display_path,
        lineno=lineno,
        source=_text(item, "source"),
        bound_names=_names(item),
    )


def _resolve(display_path: str, root: Path | None) -> Path:
    """Resolve a document path, refusing anything that escapes the root.

    Raises:
        ApplyInputError: The path leaves ``source_root``.
    """
    resolved = Path(display_path)
    if root is not None:
        resolved = (root / resolved).resolve()
        if not resolved.is_relative_to(root):
            msg = f"plan statement file {display_path!r} escapes source root {root}"
            raise ApplyInputError(msg)
        return resolved
    return resolved.resolve()


def _names(item: dict[str, Any]) -> tuple[str, ...]:
    """Return a statement's bound names, which an older plan may omit."""
    value = item.get("bound_names")
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        msg = (
            f"plan statement field 'bound_names' must be a string array, got {value!r}"
        )
        raise ApplyInputError(msg)
    return tuple(value)


def _sequence(document: dict[str, Any], field: str) -> list[Any]:
    """Return a required array-valued field."""
    value = document.get(field)
    if not isinstance(value, list):
        msg = f"plan document is missing the {field!r} array"
        raise ApplyInputError(msg)
    return value


def _text(mapping: dict[str, Any], field: str) -> str:
    """Return a required string field."""
    value = mapping.get(field)
    if not isinstance(value, str):
        msg = f"plan statement field {field!r} must be a string, got {value!r}"
        raise ApplyInputError(msg)
    return value


def _number(mapping: dict[str, Any], field: str) -> int:
    """Return a required integer field."""
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"plan statement field {field!r} must be an integer, got {value!r}"
        raise ApplyInputError(msg)
    return value
