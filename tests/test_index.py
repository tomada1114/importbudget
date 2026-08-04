"""Tests for rolling per-file scans up into the set of modules we own."""

from __future__ import annotations

import pytest

from importbudget.errors import SourceScanError
from importbudget.index import SCRIPT_MODULE, SourceIndex, scan_package, scan_script

from .conftest import PROJECT_DIR


class TestScanPackage:
    def test_every_module_of_the_package_is_indexed(self, demopkg_index):
        assert demopkg_index.modules == {
            "demopkg",
            "demopkg.cli",
            "demopkg.dyn",
            "demopkg.report",
            "demopkg.slow_a",
            "demopkg.slow_b",
            "demopkg.util",
        }

    def test_display_paths_are_relative_to_the_scan_root(self, demopkg_index):
        statement = demopkg_index.statements["demopkg.util"][0]

        assert statement.display_path == "demopkg/util.py"
        assert statement.path.is_absolute()

    def test_unparsable_file_is_warned_about_not_raised(self, tmp_path):
        package = tmp_path / "broken"
        package.mkdir()
        (package / "__init__.py").write_text("import os\n", encoding="utf-8")
        (package / "bad.py").write_text("def (:\n", encoding="utf-8")

        index = scan_package(tmp_path, "broken")

        assert index.modules == {"broken"}
        assert len(index.warnings) == 1
        assert "bad.py" in index.warnings[0]

    def test_module_with_no_dynamic_calls_is_absent_from_the_dynamic_map(
        self, demopkg_index
    ):
        assert "demopkg.util" not in demopkg_index.dynamic
        assert "demopkg.report" in demopkg_index.dynamic


class TestScanScript:
    def test_script_becomes_the_root_owner(self):
        index = scan_script(PROJECT_DIR / "run_demo.py")

        assert index.root_owner == SCRIPT_MODULE
        assert index.modules == {SCRIPT_MODULE}
        assert index.statements[SCRIPT_MODULE][0].candidates == {"demopkg"}

    def test_missing_script_raises(self, tmp_path):
        with pytest.raises(SourceScanError, match=r"could not scan"):
            scan_script(tmp_path / "missing.py")


class TestSourceIndex:
    def test_merge_keeps_the_left_hand_root_owner(self):
        script = scan_script(PROJECT_DIR / "run_demo.py")
        package = scan_package(PROJECT_DIR, "demopkg")

        merged = script.merged_with(package)

        assert merged.root_owner == SCRIPT_MODULE
        assert "demopkg.cli" in merged.modules
        assert SCRIPT_MODULE in merged.modules

    def test_empty_index_owns_nothing(self):
        assert SourceIndex().modules == frozenset()
