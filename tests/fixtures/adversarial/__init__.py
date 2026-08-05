"""Zero-false-safe fixture: every executable import here must be excluded.

This module is never imported. It is parsed by the rule set, which must reject
every single statement below; one statement landing in the safe set is the
failure mode the whitelist exists to prevent.

Its coverage is *syntactic*: every case here is refusable from the shape of the
statement and the block enclosing it, so passing this file proves the placement
and shape rules hold and nothing more. The semantic cases — lines that read as
textbook lazy candidates and are unsafe only because of what surrounds them —
live in the sibling ``semantic.py`` and ``opaque_exports.py``. The three files
together are the acceptance criterion for the rules package.

It is a package ``__init__.py`` on purpose, so the re-export rule applies too.
Lint and type checks skip this tree (see ``pyproject.toml``): the point of the
file is to hold code no linter would accept.
"""

from __future__ import annotations  # FUTURE_IMPORT (G12)

import json  # MODULE_LEVEL_USE: base class below
import os.path  # MODULE_LEVEL_USE: binds `os`, used below (G4/D4)
import sys  # MODULE_LEVEL_USE: version check below
import xml.etree.ElementTree  # MODULE_LEVEL_USE: binds `xml` (G4/D4)
from decimal import *  # STAR_IMPORT (G11)
from typing import TYPE_CHECKING  # MODULE_LEVEL_USE + REEXPORT_IN_INIT

if TYPE_CHECKING:
    # Dead: never executed, costs nothing, must not even be a candidate.
    from collections.abc import Sequence

try:  # TRY_EXCEPT_IMPORT (P4)
    import tomllib
except ImportError:  # TRY_EXCEPT_IMPORT (P4)
    import tomli as tomllib
else:  # TRY_EXCEPT_IMPORT (P4) — orelse is covered too
    import textwrap
finally:  # TRY_EXCEPT_IMPORT (P4) — finalbody is covered too
    import types

if sys.version_info >= (3, 12):  # NON_TOPLEVEL (P6)
    import graphlib

with open(os.devnull) as _handle:  # NON_TOPLEVEL (P7)
    import binascii

for _each in ():  # NON_TOPLEVEL (P8)
    import base64

while False:  # NON_TOPLEVEL (P8) — `while False` is not treated as dead
    import bisect

match sys.platform:  # NON_TOPLEVEL (P9)
    case _:
        import calendar


class Holder(json.JSONDecoder):
    """Class body imports are a SyntaxError under lazy (P3)."""

    import codecs  # NON_TOPLEVEL (P3)


def helper():
    """Function body imports are a SyntaxError under lazy (P2)."""
    import copy  # NON_TOPLEVEL (P2)

    return copy


PATH = xml.etree.ElementTree
__all__ = ["PATH", "Holder", "helper", "tomllib"]
