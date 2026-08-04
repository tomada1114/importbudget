"""Roll per-file scans up into the set of modules we own.

Attribution only charges a statement when the *importing* module is one we own,
so the boundary between our code and everything else is exactly the key set of
:attr:`SourceIndex.statements`.  Scanning one file is
:mod:`importbudget.sources`; deciding which files to scan is here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .errors import SourceScanError
from .sources import DynamicImport, ImportStatement, scan_source

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = [
    "SCRIPT_MODULE",
    "SourceIndex",
    "scan_package",
    "scan_script",
]

#: Pseudo-module name used for a script entrypoint, mirroring ``__main__``.
SCRIPT_MODULE = "__main__"


@dataclass(frozen=True, slots=True)
class SourceIndex:
    """Import statements of the modules we own.

    Attributes:
        statements: Owned module name -> its import statements, in source order.
        dynamic: Owned module name -> its dynamic import call sites.
        root_owner: Module whose statements own the *root* measurement rows.
            Set for ``-m`` and script entrypoints, whose own imports have no
            parent row; ``None`` makes root rows land on ``<entrypoint>``.
        root: Directory the display paths are relative to.
        warnings: Files that could not be scanned.
    """

    statements: Mapping[str, tuple[ImportStatement, ...]] = field(default_factory=dict)
    dynamic: Mapping[str, tuple[DynamicImport, ...]] = field(default_factory=dict)
    root_owner: str | None = None
    root: Path | None = None
    warnings: tuple[str, ...] = ()

    @property
    def modules(self) -> frozenset[str]:
        """Names of the modules we own."""
        return frozenset(self.statements)

    def merged_with(self, other: SourceIndex) -> SourceIndex:
        """Return the union of two indexes; this index's entries win.

        Args:
            other: Index to fold in.

        Returns:
            The combined index.
        """
        return SourceIndex(
            statements={**other.statements, **self.statements},
            dynamic={**other.dynamic, **self.dynamic},
            root_owner=self.root_owner or other.root_owner,
            root=self.root or other.root,
            warnings=self.warnings + other.warnings,
        )


def scan_package(root: Path, package: str) -> SourceIndex:
    """Scan every module of an owned package.

    Args:
        root: Directory containing the package (its import root).
        package: Top-level package name.

    Returns:
        The index of that package's import statements. Files that fail to parse
        are reported in :attr:`SourceIndex.warnings` instead of aborting the
        profile, because one unparsable file should not hide the whole table.
    """
    statements: dict[str, tuple[ImportStatement, ...]] = {}
    dynamic: dict[str, tuple[DynamicImport, ...]] = {}
    warnings: list[str] = []
    for path in sorted((root / package).rglob("*.py")):
        module = _module_name(path, root)
        try:
            found, calls = scan_source(path, module, root=root)
        except SourceScanError as exc:
            warnings.append(str(exc))
            continue
        statements[module] = found
        if calls:
            dynamic[module] = calls
    return SourceIndex(
        statements=statements,
        dynamic=dynamic,
        root=root,
        warnings=tuple(warnings),
    )


def scan_script(path: Path, *, root: Path | None = None) -> SourceIndex:
    """Scan a script entrypoint as an owned pseudo-module.

    A script's own imports appear at depth 0 with no parent row, so without
    this they would all collapse into the ``<entrypoint>`` row.

    Args:
        path: Script file.
        root: Directory display paths are made relative to; defaults to the
            script's directory.

    Returns:
        An index whose ``root_owner`` is the script pseudo-module.

    Raises:
        SourceScanError: The script could not be read or parsed.
    """
    base = root or path.parent
    statements, dynamic = scan_source(path, SCRIPT_MODULE, root=base)
    return SourceIndex(
        statements={SCRIPT_MODULE: statements},
        dynamic={SCRIPT_MODULE: dynamic} if dynamic else {},
        root_owner=SCRIPT_MODULE,
        root=base,
    )


def _module_name(path: Path, root: Path) -> str:
    """Return the dotted module name of a source file below ``root``."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
