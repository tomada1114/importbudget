"""Resolve an import statement to every module name it can be blamed for.

``import a.b.c`` pays for ``a``, ``a.b`` and ``a.b.c``, and ``from .x import y``
inside ``pkg.sub`` resolves to ``pkg.sub.x`` and ``pkg.sub.x.y``.  Attribution
looks a measured module name up in these sets, so a name missing here silently
sends real time to the ``<dynamic>`` bucket.
"""

from __future__ import annotations

import ast

__all__ = ["candidates"]


def candidates(
    node: ast.Import | ast.ImportFrom,
    *,
    module: str,
    is_package: bool,
) -> set[str]:
    """Return every module name a statement can be responsible for."""
    names: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            names |= _dotted_prefixes(alias.name)
        return names

    base = (
        _resolve_relative(module, is_package=is_package, level=node.level)
        if node.level
        else ""
    )
    full = f"{base}.{node.module}" if node.module else base
    full = full.strip(".")
    if not full:
        return names
    names |= _dotted_prefixes(full)
    # `from pkg import sub` may import the submodule `pkg.sub`.
    names |= {f"{full}.{alias.name}" for alias in node.names if alias.name != "*"}
    return names


def _resolve_relative(module: str, *, is_package: bool, level: int) -> str:
    """Resolve the package a relative import is anchored at."""
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    keep = len(parts) - (level - 1)
    return ".".join(parts[:keep]) if keep > 0 else ""


def _dotted_prefixes(name: str) -> set[str]:
    """Return ``{"a", "a.b", "a.b.c"}`` for ``"a.b.c"``."""
    parts = name.split(".")
    return {".".join(parts[: index + 1]) for index in range(len(parts))}
