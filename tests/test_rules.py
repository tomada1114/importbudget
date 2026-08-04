"""Positive/negative pairs for every safety rule.

Each rule gets at least one module it must reject and one it must accept. The
negatives matter more than the positives: a rule that fires on everything is
useless but harmless, while a rule that stays silent when it should not is the
false-safe failure the whitelist exists to prevent.
"""

from __future__ import annotations

import pytest

from importbudget.rules import RULES, RuleCode

from .conftest import codes_by_source, safe_sources

SAFE_MODULE = """
import decimal


def helper():
    return decimal.Decimal(1)
"""


def codes(verdicts):
    """Return the reason codes of the single statement under test."""
    assert len(verdicts) == 1, [v.statement.source for v in verdicts]
    return {str(code) for code in verdicts[0].codes}


class TestRegistry:
    def test_every_rule_has_a_distinct_code(self):
        found = [rule.code for rule in RULES]

        assert len(set(found)) == len(found)

    def test_every_registered_code_is_a_known_rule_code(self):
        assert all(rule.code in set(RuleCode) for rule in RULES)

    def test_all_matching_rules_are_reported_not_just_the_first(self, judge):
        verdicts = judge(
            """
            try:
                from decimal import *
            except ImportError:
                pass
            """
        )

        assert codes(verdicts) == {"STAR_IMPORT", "TRY_EXCEPT_IMPORT", "UNUSED_IMPORT"}


class TestStarImport:
    def test_star_import_is_rejected(self, judge):
        verdicts = judge("from decimal import *\n")

        assert "STAR_IMPORT" in codes(verdicts)

    def test_a_named_import_is_not_a_star_import(self, judge):
        verdicts = judge(SAFE_MODULE)

        assert "STAR_IMPORT" not in codes(verdicts)
        assert verdicts[0].is_safe


class TestFutureImport:
    def test_future_import_is_rejected(self, judge):
        verdicts = judge("from __future__ import annotations\n")

        assert "FUTURE_IMPORT" in codes(verdicts)

    def test_importing_the_future_module_itself_is_not_a_future_statement(self, judge):
        verdicts = judge(
            """
            import __future__


            def helper():
                return __future__.annotations
            """
        )

        assert "FUTURE_IMPORT" not in codes(verdicts)
        assert verdicts[0].is_safe


class TestNonToplevel:
    @pytest.mark.parametrize(
        ("source", "label"),
        [
            pytest.param(
                "def helper():\n    import decimal\n    return decimal\n",
                "function body",
                id="function",
            ),
            pytest.param(
                "class Holder:\n    import decimal\n",
                "class body",
                id="class",
            ),
            pytest.param(
                "import sys\n\nif sys.platform:\n    import decimal\n",
                "conditional or loop block",
                id="module-level-if",
            ),
            pytest.param(
                "for _ in ():\n    import decimal\n",
                "conditional or loop block",
                id="module-level-for",
            ),
            pytest.param(
                "while False:\n    import decimal\n",
                "conditional or loop block",
                id="module-level-while",
            ),
            pytest.param(
                "import sys\n\nmatch sys.platform:\n    case _:\n        import decimal\n",
                "conditional or loop block",
                id="module-level-match",
            ),
        ],
    )
    def test_nested_placements_are_rejected(self, judge, source, label):
        verdicts = judge(source)
        by_source = codes_by_source(judge(source))

        assert "NON_TOPLEVEL" in by_source["import decimal"]
        assert any(
            label in violation.message
            for verdict in verdicts
            for violation in verdict.violations
            if violation.code is RuleCode.NON_TOPLEVEL
        )

    def test_a_module_level_statement_is_not_rejected(self, judge):
        verdicts = judge(SAFE_MODULE)

        assert "NON_TOPLEVEL" not in codes(verdicts)

    def test_type_checking_imports_are_dropped_rather_than_flagged(self, judge):
        verdicts = judge(
            """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                import decimal
            """
        )

        assert "import decimal" not in codes_by_source(verdicts)


class TestTryExceptImport:
    @pytest.mark.parametrize(
        "branch",
        [
            pytest.param("try:\n    import decimal\nexcept ImportError:\n    pass\n"),
            pytest.param("try:\n    pass\nexcept ImportError:\n    import decimal\n"),
            pytest.param(
                "try:\n    pass\nexcept ImportError:\n    pass\nelse:\n    import decimal\n"
            ),
            pytest.param("try:\n    pass\nfinally:\n    import decimal\n"),
        ],
    )
    def test_every_branch_of_a_try_statement_is_rejected(self, judge, branch):
        by_source = codes_by_source(judge(branch))

        assert "TRY_EXCEPT_IMPORT" in by_source["import decimal"]

    def test_a_statement_after_the_try_block_is_not_rejected(self, judge):
        verdicts = judge(
            """
            try:
                pass
            except ImportError:
                pass

            import decimal


            def helper():
                return decimal.Decimal(1)
            """
        )
        by_source = codes_by_source(verdicts)

        assert by_source["import decimal"] == set()


class TestModuleLevelUse:
    @pytest.mark.parametrize(
        "usage",
        [
            pytest.param(
                "@decimal.something\ndef helper():\n    pass\n", id="decorator"
            ),
            pytest.param("class Holder(decimal.Decimal):\n    pass\n", id="base-class"),
            pytest.param("VALUE = decimal.Decimal(1)\n", id="call"),
            pytest.param("VALUE = f'{decimal}'\n", id="f-string"),
            pytest.param("VALUE = [decimal for _ in ()]\n", id="comprehension"),
            pytest.param(
                "def helper(arg=decimal.Decimal(1)):\n    pass\n", id="default"
            ),
            pytest.param(
                "def helper() -> decimal.Decimal:\n    pass\n", id="annotation"
            ),
        ],
    )
    def test_an_import_time_read_is_rejected(self, judge, usage):
        by_source = codes_by_source(judge(f"import decimal\n\n{usage}"))

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    def test_a_dotted_import_tracks_the_bound_name_not_the_dotted_path(self, judge):
        # `import xml.etree.ElementTree` binds `xml` (PEP 810 G4/S7/D4), so a
        # module-level touch of `xml.etree` is a use of this statement's name.
        by_source = codes_by_source(
            judge(
                """
                import xml.etree.ElementTree

                PARSER = xml.etree
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import xml.etree.ElementTree"]

    def test_a_dotted_import_used_only_in_a_function_is_safe(self, judge):
        verdicts = judge(
            """
            import xml.etree.ElementTree


            def helper():
                return xml.etree.ElementTree.Element("x")
            """
        )

        assert verdicts[0].is_safe
        assert verdicts[0].bound_names == ("xml",)

    def test_a_name_listed_in_all_counts_as_an_import_time_use(self, judge):
        by_source = codes_by_source(
            judge(
                """
                import decimal

                __all__ = ["decimal"]


                def helper():
                    return decimal
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    def test_all_extended_with_augmented_assignment_is_read_too(self, judge):
        by_source = codes_by_source(
            judge(
                """
                import decimal

                __all__ = []
                __all__ += ["decimal"]


                def helper():
                    return decimal
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    @pytest.mark.parametrize(
        "mutation",
        [
            pytest.param('__all__.append("decimal")', id="append"),
            pytest.param('__all__.extend(["decimal"])', id="extend"),
            pytest.param('__all__.insert(0, "decimal")', id="insert"),
            pytest.param('__all__[0] = "decimal"', id="subscript"),
            pytest.param("register(__all__)", id="passed-to-a-function"),
        ],
    )
    def test_all_mutated_outside_an_assignment_makes_it_unreadable(
        self, judge, mutation
    ):
        # An opaque mutation hides which names are exported, and the __all__
        # interaction with lazy imports is UNVERIFIED, so nothing is provable.
        by_source = codes_by_source(
            judge(
                f"""
                import decimal

                __all__ = ["helper"]
                {mutation}


                def helper():
                    return decimal
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    def test_a_non_string_entry_makes_all_unreadable(self, judge):
        by_source = codes_by_source(
            judge(
                """
                import decimal

                __all__ = [1]


                def helper():
                    return decimal
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    def test_an_unreadable_all_rejects_everything_in_the_module(self, judge):
        by_source = codes_by_source(
            judge(
                """
                import decimal

                __all__ = sorted(dir())


                def helper():
                    return decimal
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]

    def test_a_global_rebinding_counts_as_an_import_time_use(self, judge):
        by_source = codes_by_source(
            judge(
                """
                import decimal


                def helper():
                    global decimal
                    decimal = None
                """
            )
        )

        assert "MODULE_LEVEL_USE" in by_source["import decimal"]


class TestReexportInInit:
    def test_a_from_import_in_a_package_init_is_a_reexport(self, judge):
        verdicts = judge(
            """
            from decimal import Decimal


            def helper():
                return Decimal(1)
            """,
            name="pkg/__init__.py",
            module="pkg",
        )

        assert "REEXPORT_IN_INIT" in codes(verdicts)

    def test_a_name_in_all_is_a_reexport_even_for_a_plain_import(self, judge):
        verdicts = judge(
            """
            import decimal

            __all__ = ["decimal"]
            """,
            name="pkg/__init__.py",
            module="pkg",
        )

        assert "REEXPORT_IN_INIT" in codes(verdicts)

    def test_an_underscore_alias_is_private_and_not_a_reexport(self, judge):
        verdicts = judge(
            """
            from decimal import Decimal as _Decimal


            def helper():
                return _Decimal(1)
            """,
            name="pkg/__init__.py",
            module="pkg",
        )

        assert "REEXPORT_IN_INIT" not in codes(verdicts)
        assert verdicts[0].is_safe

    def test_a_name_appended_to_all_is_still_a_reexport(self, judge):
        # `__all__.append` is invisible to a literal read of the list, so the
        # whole file has to be refused rather than let `numpy` look private.
        verdicts = judge(
            """
            import numpy

            __all__ = []
            __all__.append("numpy")


            def go():
                return numpy.array([])
            """,
            name="pkg/__init__.py",
            module="pkg",
        )

        assert not verdicts[0].is_safe

    def test_a_plain_import_in_an_init_is_not_a_reexport_on_its_own(self, judge):
        verdicts = judge(SAFE_MODULE, name="pkg/__init__.py", module="pkg")

        assert "REEXPORT_IN_INIT" not in codes(verdicts)
        assert verdicts[0].is_safe

    def test_the_same_from_import_outside_an_init_is_not_a_reexport(self, judge):
        verdicts = judge(
            """
            from decimal import Decimal


            def helper():
                return Decimal(1)
            """
        )

        assert "REEXPORT_IN_INIT" not in codes(verdicts)
        assert verdicts[0].is_safe


class TestUnusedImport:
    def test_a_name_nobody_reads_is_presumed_side_effect_only(self, judge):
        verdicts = judge("import decimal\n")

        assert "UNUSED_IMPORT" in codes(verdicts)

    def test_a_star_import_binds_no_inspectable_name(self, judge):
        verdicts = judge("from decimal import *\n")

        assert "UNUSED_IMPORT" in codes(verdicts)

    def test_a_name_used_only_inside_a_function_is_the_ideal_candidate(self, judge):
        verdicts = judge(SAFE_MODULE)

        assert "UNUSED_IMPORT" not in codes(verdicts)
        assert safe_sources(verdicts) == {"import decimal"}

    def test_one_unused_name_rejects_the_whole_statement(self, judge):
        verdicts = judge(
            """
            from decimal import Decimal, getcontext


            def helper():
                return Decimal(1)
            """
        )

        assert "UNUSED_IMPORT" in codes(verdicts)
