"""Shared test fixtures.

The committed ``fixtures/importtime_*.txt`` captures are real
``python -X importtime`` output for ``tests/fixtures/project``. Using them lets
the parser and attribution tests assert exact numbers without spawning a
subprocess; only the measurement and CLI tests actually run interpreters.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from importbudget.entrypoints import Entrypoint, Measurement
from importbudget.importtime import parse_importtime
from importbudget.index import scan_package

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from importbudget.attribute import Attribution, AttributionResult
    from importbudget.importtime import ImportNode, ImportTree
    from importbudget.index import SourceIndex

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROJECT_DIR = FIXTURES_DIR / "project"
DEMOPKG_CAPTURE = FIXTURES_DIR / "importtime_demopkg.txt"
BASELINE_CAPTURE = FIXTURES_DIR / "importtime_baseline.txt"


@pytest.fixture(scope="session")
def project_dir() -> Path:
    return PROJECT_DIR


@pytest.fixture(scope="session")
def demopkg_capture() -> str:
    return DEMOPKG_CAPTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def baseline_modules() -> frozenset[str]:
    return parse_importtime(BASELINE_CAPTURE.read_text(encoding="utf-8")).names()


@pytest.fixture
def demopkg_tree(demopkg_capture: str) -> ImportTree:
    return parse_importtime(demopkg_capture)


@pytest.fixture
def demopkg_index() -> SourceIndex:
    return scan_package(PROJECT_DIR, "demopkg")


@pytest.fixture
def make_measurement() -> Callable[..., Measurement]:
    """Return a factory building a Measurement from captured importtime text."""

    def factory(
        stderr_text: str,
        *,
        baseline: Iterable[str] = (),
        entrypoint: Entrypoint | None = None,
    ) -> Measurement:
        tree = parse_importtime(stderr_text)
        return Measurement(
            entrypoint=entrypoint or Entrypoint("demopkg"),
            tree=tree,
            baseline_modules=frozenset(baseline),
            runs=1,
            warmup_runs=1,
            returncodes=(0,),
            python_executable=sys.executable,
            python_version="3.12.0",
            platform="test-platform",
            warnings=tree.warnings,
        )

    return factory


@pytest.fixture
def demopkg_measurement(
    make_measurement: Callable[..., Measurement],
    demopkg_capture: str,
    baseline_modules: frozenset[str],
) -> Measurement:
    return make_measurement(demopkg_capture, baseline=baseline_modules)


def node_by_name(tree: ImportTree, name: str) -> ImportNode:
    """Return the single tree node with ``name``."""
    matches = [node for node in tree.nodes if node.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} node, got {matches}"
    return matches[0]


def row_by_key(result: AttributionResult, key: str) -> Attribution:
    """Return the attribution row with ``key``."""
    matches = [row for row in result.rows if row.key == key]
    assert len(matches) == 1, f"expected exactly one {key!r} row, got {matches}"
    return matches[0]


def row_keys(result: AttributionResult) -> set[str]:
    """Return every row key of an attribution result."""
    return {row.key for row in result.rows}
