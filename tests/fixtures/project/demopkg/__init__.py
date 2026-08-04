"""Fixture package with a deliberately expensive import graph.

Costs come from ``time.sleep`` so attribution assertions do not depend on
machine speed. Lint and type checks skip this tree: it holds dead, duplicated
and dynamic imports on purpose.
"""

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Never executed. Must NOT receive slow_a's cost; the real import below
    # owns it. A dead import stealing attribution was a real PoC bug.
    from . import slow_a

time.sleep(0.01)

from . import slow_a
from .cli import main
from .report import render

__all__ = ["main", "render", "slow_a"]
