"""Opaque-``__all__`` fixture: a module whose public surface cannot be read.

``__all__.append(...)`` is a method call, so a static read of the assignment
sees only ``["helper"]`` and would conclude that ``gzip`` is private and safe to
defer. It is not: ``docs/pep810-rules.md`` §4 lists the ``__all__`` interaction
with lazy imports as **UNVERIFIED**, and a name that might be exported cannot be
proven unexported.

Both statements below are otherwise immaculate — module top level, read only
inside a function, no re-export shape, not an ``__init__.py`` — so
``OPAQUE_EXPORTS`` is the *only* code that may fire, which is what makes this
file worth keeping separate from its siblings.

This module is never imported. Lint and type checks skip this tree (see
``pyproject.toml``).
"""

import gzip
import zoneinfo

__all__ = ["helper"]
__all__.append("gzip")


def helper(path):
    return gzip.open(path), zoneinfo.ZoneInfo("UTC")
