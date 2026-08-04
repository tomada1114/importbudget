"""Dynamic imports that no static import statement explains.

``importlib.import_module`` bypasses the C import path, so CPython emits no
row for the target itself; its cost shows up through the rows of whatever the
target imports normally.  ``__import__`` does go through the C path and gets a
row of its own, which is the case where the literal argument names the row.
"""

import importlib

_dynamic = importlib.import_module("demopkg.dyn")
_forced = __import__("gzip")


def render() -> str:
    """Return the dynamically imported module's marker."""
    return f"{_dynamic.MARKER}:{_forced.__name__}"
