"""Tests for the AST scan that turns source files into import statements."""

from __future__ import annotations

import pytest

from importbudget.sources import scan_source

from .conftest import FIXTURES_DIR, PROJECT_DIR

SAMPLE = FIXTURES_DIR / "scan_sample.py"


@pytest.fixture(scope="module")
def sample_statements():
    statements, _ = scan_source(SAMPLE, "demo.sub.mod", root=FIXTURES_DIR)
    return {statement.lineno: statement for statement in statements}


@pytest.fixture(scope="module")
def sample_dynamic():
    _, dynamic = scan_source(SAMPLE, "demo.sub.mod", root=FIXTURES_DIR)
    return dynamic


class TestCandidates:
    def test_dotted_import_covers_every_prefix(self, sample_statements):
        assert sample_statements[3].candidates == {"os", "os.path"}

    def test_from_import_covers_package_and_member(self, sample_statements):
        assert sample_statements[4].candidates == {"typing", "typing.TYPE_CHECKING"}

    def test_single_dot_relative_import_resolves_to_the_owning_package(
        self, sample_statements
    ):
        # `demo.sub.mod` is a module, so `from . import sibling` means demo.sub;
        # importing it also imports the parent package, hence "demo".
        assert sample_statements[16].candidates == {
            "demo",
            "demo.sub",
            "demo.sub.sibling",
        }

    def test_relative_submodule_import_resolves_every_prefix(self, sample_statements):
        assert sample_statements[17].candidates == {
            "demo",
            "demo.sub",
            "demo.sub.deeper",
            "demo.sub.deeper.thing",
        }

    def test_double_dot_relative_import_climbs_one_package(self, sample_statements):
        assert sample_statements[18].candidates == {"demo", "demo.parent_thing"}

    def test_relative_import_above_the_root_yields_no_candidates(
        self, sample_statements
    ):
        # `from ... import too_far_up` cannot resolve from demo.sub.mod.
        assert sample_statements[38].candidates == frozenset()

    def test_relative_import_in_a_package_init_keeps_the_package(self):
        statements, _ = scan_source(
            PROJECT_DIR / "demopkg" / "__init__.py", "demopkg", root=PROJECT_DIR
        )
        real = next(
            statement
            for statement in statements
            if statement.source == "from . import slow_a" and not statement.is_dead
        )

        assert real.candidates == {"demopkg", "demopkg.slow_a"}


class TestClassification:
    def test_type_checking_import_is_dead(self, sample_statements):
        assert sample_statements[7].is_dead

    def test_if_false_import_is_dead(self, sample_statements):
        assert sample_statements[12].is_dead

    def test_else_branch_of_type_checking_is_live(self, sample_statements):
        assert not sample_statements[9].is_dead

    def test_qualified_type_checking_guard_is_recognised(self, sample_statements):
        assert sample_statements[36].is_dead

    def test_module_level_import_is_toplevel(self, sample_statements):
        assert sample_statements[3].is_toplevel

    def test_function_level_import_is_not_toplevel(self, sample_statements):
        assert not sample_statements[23].is_toplevel

    def test_statements_are_ordered_by_position(self):
        statements, _ = scan_source(SAMPLE, "demo.sub.mod", root=FIXTURES_DIR)

        linenos = [statement.lineno for statement in statements]

        assert linenos == sorted(linenos)

    def test_location_is_the_display_path_and_line(self, sample_statements):
        assert sample_statements[3].location == "scan_sample.py:3"


class TestDynamicImports:
    def test_literal_argument_names_the_call(self, sample_dynamic):
        literal = next(call for call in sample_dynamic if call.lineno == 25)

        assert literal.name == "csv"
        assert literal.location == "scan_sample.py:25"

    def test_non_literal_argument_leaves_the_name_unknown(self, sample_dynamic):
        unknown = next(call for call in sample_dynamic if call.lineno == 30)

        assert unknown.name is None

    def test_plain_calls_are_not_dynamic_imports(self, sample_dynamic):
        # Line 41 calls a value out of a dict; it is not an import call.
        assert {call.lineno for call in sample_dynamic} == {25, 30}

    def test_dunder_import_with_a_literal_is_detected(self):
        _, dynamic = scan_source(
            PROJECT_DIR / "demopkg" / "report.py", "demopkg.report", root=PROJECT_DIR
        )

        assert {call.name for call in dynamic} == {"demopkg.dyn", "gzip"}


class TestDisplayPaths:
    def test_paths_outside_the_scan_root_stay_absolute(self, tmp_path):
        statements, _ = scan_source(SAMPLE, "demo.sub.mod", root=tmp_path)

        assert statements[0].display_path == SAMPLE.as_posix()
