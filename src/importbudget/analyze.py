"""Run every safety rule over every candidate statement.

This is the whitelist engine.  For one owned module it parses the source once
(:func:`~importbudget.sources.scan_module`), derives the shared AST facts
(:func:`~importbudget.rules.build_context`), and asks every rule in
:data:`~importbudget.rules.RULES` about every live import statement.

Two properties matter:

* **All rules run.** A :class:`Verdict` carries the complete list of firing
  codes, not the first one. A user deciding whether to hand-convert a statement
  needs to see all four reasons, not fix one and come back.
* **Dead statements are dropped, not excluded.** Imports under
  ``if TYPE_CHECKING:`` never execute, so they cost nothing and there is
  nothing to propose; reporting them as "excluded: NON_TOPLEVEL" would bury the
  real findings under noise.

Joining these verdicts with measured cost is :mod:`importbudget.planner`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import SourceScanError
from .rules import RULES, build_context
from .sources import scan_module

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .rules import ModuleContext, Rule, RuleCode, Violation
    from .sources import ImportStatement

__all__ = ["Analyzer", "Verdict", "analyze"]


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the rule set concluded about one import statement.

    Attributes:
        statement: The statement that was judged.
        bound_names: Module-global names it binds (``import a.b`` binds ``a``).
        violations: Every rule that fired, in rule-registry order. Empty means
            the statement proved safe to convert.
    """

    statement: ImportStatement
    bound_names: tuple[str, ...]
    violations: tuple[Violation, ...]

    @property
    def key(self) -> str:
        """``file:line`` label, the same key the attribution table uses."""
        return self.statement.location

    @property
    def is_safe(self) -> bool:
        """True only when no rule fired at all."""
        return not self.violations

    @property
    def codes(self) -> tuple[RuleCode, ...]:
        """Reason codes of every rule that fired."""
        return tuple(violation.code for violation in self.violations)


def analyze(
    path: Path,
    module: str,
    *,
    root: Path | None = None,
    rules: Sequence[Rule] = RULES,
) -> tuple[Verdict, ...]:
    """Judge every live import statement of one owned source file.

    Args:
        path: Source file to analyze.
        module: Dotted name of that file's module (for a package
            ``__init__.py`` pass the package name itself).
        root: Directory the reported paths are made relative to.
        rules: Rule set to apply; defaults to
            :data:`~importbudget.rules.RULES`.

    Returns:
        One verdict per executable import statement, in source order. Dead
        statements (``if TYPE_CHECKING:`` and friends) are omitted.

    Raises:
        SourceScanError: The file could not be read or parsed.
    """
    tree, statements, _dynamic = scan_module(path, module, root=root)
    context = build_context(path, module, tree)
    return tuple(
        _judge(statement, context, rules)
        for statement in statements
        if not statement.is_dead and context.has_node(statement)
    )


def _judge(
    statement: ImportStatement,
    context: ModuleContext,
    rules: Sequence[Rule],
) -> Verdict:
    """Apply the whole rule set to one statement, keeping every violation."""
    violations = tuple(
        violation
        for violation in (rule.check(statement, context) for rule in rules)
        if violation is not None
    )
    return Verdict(
        statement=statement,
        bound_names=context.bound_names(statement),
        violations=violations,
    )


def _merge(verdicts: Sequence[Verdict]) -> Verdict:
    """Fold several statements sharing one line into a single verdict.

    The merged verdict is safe only when every statement on the line is, and
    it carries the reasons and bound names of all of them so the report still
    explains which neighbour ruined the row.
    """
    first = verdicts[0]
    violations: dict[Violation, None] = {}
    names: dict[str, None] = {}
    for verdict in verdicts:
        violations.update(dict.fromkeys(verdict.violations))
        names.update(dict.fromkeys(verdict.bound_names))
    return Verdict(
        statement=first.statement,
        bound_names=tuple(names),
        violations=tuple(violations),
    )


class Analyzer:
    """Analyze many files while parsing each of them at most once.

    The planner walks an attribution table whose rows arrive interleaved by
    cost, so the same file is asked about repeatedly. Results and scan failures
    are memoized per path; a file that cannot be parsed yields no verdicts and
    one warning, and the planner turns the missing verdict into an
    :attr:`~importbudget.rules.RuleCode.UNANALYZED` exclusion rather than
    silently dropping the row.
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        rules: Sequence[Rule] = RULES,
    ) -> None:
        """Store the display root and the rule set every file is judged with."""
        self._root = root
        self._rules = rules
        self._cache: dict[Path, tuple[Verdict, ...]] = {}
        self._warnings: list[str] = []

    @property
    def warnings(self) -> tuple[str, ...]:
        """Files that could not be analyzed, deduplicated in first-seen order."""
        return tuple(dict.fromkeys(self._warnings))

    def verdicts(self, path: Path, module: str) -> tuple[Verdict, ...]:
        """Return every verdict for one file, analyzing it on first request."""
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            found = analyze(path, module, root=self._root, rules=self._rules)
        except SourceScanError as exc:
            self._warnings.append(str(exc))
            found = ()
        self._cache[path] = found
        return found

    def find(self, path: Path, module: str, lineno: int) -> Verdict | None:
        """Return the verdict for ``lineno``, merging every statement on it.

        Attribution is line-granular (``file:line``), so ``import os; import
        plugins`` arrives as **one** costed row covering **two** statements.
        Returning just the first would let a safe statement vouch for an unsafe
        neighbour, which is the false-safe this whitelist exists to prevent, so
        the violations of every statement on the line are unioned instead: the
        row is safe only when all of them are.
        """
        found = [
            verdict
            for verdict in self.verdicts(path, module)
            if verdict.statement.lineno == lineno
        ]
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        return _merge(found)
