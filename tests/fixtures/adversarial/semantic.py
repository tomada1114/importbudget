"""Semantic false-safe fixture: lines that look like perfect lazy candidates.

The sibling ``__init__.py`` is *syntactic*: every statement in it can be refused
from the shape of the line and its enclosing block alone. The two shapes here
cannot. Each is a plain module-level import read only inside a function — the
textbook lazy candidate — and only the rest of the module decides whether
converting it is safe, or merely useless:

* ``import graphlib; import gzip`` puts two statements on one line, and
  attribution is line-granular, so both arrive as a single ``file:line`` row.
  ``graphlib`` alone is convertible; ``gzip`` beside it is not. The row must not
  come out safe because its first half was, which is what
  :meth:`importbudget.analyze.Analyzer.find` merges the verdicts to prevent.
* ``import wave`` is a **known gap** (issue #21). Nothing reads ``wave`` while
  the module executes, so the rule set judges it safe — yet ``_probe()`` runs
  during import and reaches it through ``_read``. Converting it is harmless and
  saves nothing, so ``apply`` raises ``MODULE_LEVEL_CALL`` on it instead of the
  rule set rejecting it.

This module is never imported, and no ``from __future__ import annotations``
belongs here: it would add a statement a syntactic rule already covers. Lint and
type checks skip this tree (see ``pyproject.toml``).
"""

import graphlib; import gzip

import wave


def sorter():
    return graphlib.TopologicalSorter()


def _read(path):
    return wave.open(path)


def _probe():
    return _read("sample.wav")


OPENER = gzip.open
HEADER = _probe()
