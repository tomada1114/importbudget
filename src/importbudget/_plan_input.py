"""Read a saved profile JSON back into the shapes the planner works on.

``importbudget plan --from-profile p.json`` must produce exactly the plan the
live path would, so the document is decoded back into
:class:`~importbudget.attribute.Attribution` rows rather than into a second,
parallel row type.

Validation is strict on purpose.  The plan's whole value is "these statements
are provably safe"; silently planning against a truncated or foreign document
would be a quiet way to reach a wrong answer.  Every missing or mistyped field
raises :class:`~importbudget.errors.PlanInputError` naming the field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .attribute import Attribution, AttributionKind
from .errors import PlanInputError
from .plans import ProfileSummary
from .report import PROFILE_DOCUMENT, SCHEMA_VERSION

__all__ = ["load_profile_document"]


def load_profile_document(
    path: Path,
) -> tuple[ProfileSummary, tuple[Attribution, ...], Path | None]:
    """Decode a profile JSON document into planner inputs.

    Args:
        path: File written by ``importbudget profile --json``.

    Returns:
        The measurement summary, the attribution rows, and the source root the
        rows' paths are relative to.

    Raises:
        PlanInputError: The file is unreadable, is not JSON, is not a profile
            document of a supported schema version, or carries a field that is
            missing, of the wrong type, or out of range.
    """
    document = _read(path)
    _check_kind(document, path)
    entrypoint = _mapping(document, "entrypoint")
    root = entrypoint.get("source_root")
    summary = _summary(document, entrypoint, origin=path.as_posix())
    rows = tuple(_row(item) for item in _sequence(document, "statements"))
    return summary, rows, Path(root) if isinstance(root, str) else None


def _read(path: Path) -> dict[str, Any]:
    """Load the document, turning every I/O and syntax failure into our error."""
    try:
        text = path.read_text(encoding="utf-8")
        loaded = json.loads(text)
    except OSError as exc:
        msg = f"could not read profile document {path}: {exc}"
        raise PlanInputError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise PlanInputError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} is not an importbudget profile document (expected an object)"
        raise PlanInputError(msg)
    return loaded


def _check_kind(document: dict[str, Any], path: Path) -> None:
    """Reject documents of the wrong kind or an unsupported schema version.

    A missing ``document`` key is an error rather than an assumed profile: no
    released schema version ever omitted it, so its absence means the file is
    not one of ours — and guessing "profile" would plan against a foreign
    document that happens to carry a ``statements`` array.
    """
    kind = document.get("document")
    if kind != PROFILE_DOCUMENT:
        msg = f"{path} is a {kind!r} document, not a profile"
        raise PlanInputError(msg)
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        msg = (
            f"{path} has schema_version {version!r}; this importbudget "
            f"understands {SCHEMA_VERSION}"
        )
        raise PlanInputError(msg)


def _summary(
    document: dict[str, Any],
    entrypoint: dict[str, Any],
    *,
    origin: str,
) -> ProfileSummary:
    """Rebuild the measurement summary from the document's header blocks."""
    environment = _mapping(document, "environment")
    measurement = _mapping(document, "measurement")
    return ProfileSummary(
        target=_text(entrypoint, "target"),
        kind=_text(entrypoint, "kind"),
        origin=origin,
        python_version=_text(environment, "python_version"),
        platform=_text(environment, "platform"),
        runs=_number(measurement, "runs", minimum=1),
        warmup_runs=_number(measurement, "warmup_runs"),
        measured_us=_number(measurement, "measured_us"),
        filtered_us=_number(measurement, "filtered_baseline_us"),
        attributed_us=_number(measurement, "attributed_us"),
    )


def _row(item: Any) -> Attribution:
    """Rebuild one attribution row from its JSON object."""
    if not isinstance(item, dict):
        msg = f"statements[] entries must be objects, got {type(item).__name__}"
        raise PlanInputError(msg)
    try:
        kind = AttributionKind(_text(item, "kind"))
    except ValueError as exc:
        msg = f"unknown statement kind {item.get('kind')!r}"
        raise PlanInputError(msg) from exc
    return Attribution(
        key=_text(item, "key"),
        kind=kind,
        self_us=_number(item, "self_us"),
        cumulative_us=_number(item, "cumulative_us"),
        modules=tuple(item.get("modules") or ()),
        owner=_optional_text(item, "module"),
        display_path=_optional_text(item, "file"),
        lineno=item.get("line") if isinstance(item.get("line"), int) else None,
        source=_optional_text(item, "source"),
    )


def _mapping(document: dict[str, Any], field: str) -> dict[str, Any]:
    """Return a required object-valued field."""
    value = document.get(field)
    if not isinstance(value, dict):
        msg = f"profile document is missing the {field!r} object"
        raise PlanInputError(msg)
    return value


def _sequence(document: dict[str, Any], field: str) -> list[Any]:
    """Return a required array-valued field."""
    value = document.get(field)
    if not isinstance(value, list):
        msg = f"profile document is missing the {field!r} array"
        raise PlanInputError(msg)
    return value


def _text(mapping: dict[str, Any], field: str) -> str:
    """Return a required string field."""
    value = mapping.get(field)
    if not isinstance(value, str):
        msg = f"profile document field {field!r} must be a string, got {value!r}"
        raise PlanInputError(msg)
    return value


def _optional_text(mapping: dict[str, Any], field: str) -> str | None:
    """Return a string field that is allowed to be absent or null."""
    value = mapping.get(field)
    return value if isinstance(value, str) else None


def _number(mapping: dict[str, Any], field: str, *, minimum: int = 0) -> int:
    """Return a required integer field, rejecting values out of range.

    Every number a profile document carries is a count or a microsecond total,
    so the range check mirrors the one the live path gets for free from
    :class:`~importbudget.entrypoints.RunOptions` and
    :class:`~importbudget.plans.PlanOptions`: without it ``runs: -5`` loads
    happily and renders nonsense in the plan header.
    """
    value = mapping.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"profile document field {field!r} must be an integer, got {value!r}"
        raise PlanInputError(msg)
    if value < minimum:
        msg = f"profile document field {field!r} must be >= {minimum}, got {value}"
        raise PlanInputError(msg)
    return value
