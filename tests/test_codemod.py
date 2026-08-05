"""Tests for the PEP 810 codemod."""

from __future__ import annotations

import importlib
import json
import sys
import textwrap
import types
from dataclasses import replace
from typing import TYPE_CHECKING

import libcst as cst
import pytest

from importbudget._emit import HELPER_NAME
from importbudget.applies import (
    ApplyCode,
    ApplyOptions,
    ApplyStatus,
    FlagCode,
)
from importbudget.apply_report import (
    render_apply_diff,
    render_apply_json,
    render_apply_table,
    to_apply_json_dict,
)
from importbudget.codemod import apply
from importbudget.errors import ApplyInputError, CodemodError
from importbudget.report import APPLY_DOCUMENT, SCHEMA_VERSION

if TYPE_CHECKING:
    from types import ModuleType

    from importbudget.applies import ApplyResult

MIXED_SOURCE = '''
"""A module with one of every shape."""

from __future__ import annotations

import numpy  # the expensive one
import a.b.c
import one, two
from typing import Optional as Opt
from collections import OrderedDict
from . import sibling
from pkg import (alpha, beta)
import json; import plugins

__all__ = ["Opt", "OrderedDict"]
'''

FALLBACK_TARGET = "3.13"


def reason_by_line(result: ApplyResult) -> dict[int | None, str]:
    """Map each statement's line to its outcome, converted or refused."""
    return {
        entry.lineno: str(entry.reason) if entry.reason else str(entry.status)
        for entry in result.entries
    }


class TestDryRun:
    def test_a_dry_run_writes_nothing_to_disk(self, make_plan, tmp_path):
        plan = make_plan("import numpy\n")
        before = (tmp_path / "sample.py").read_text(encoding="utf-8")

        apply(plan)

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == before

    def test_a_dry_run_still_reports_the_diff_it_would_have_written(self, make_plan):
        result = apply(make_plan("import numpy\n"))

        diff = render_apply_diff(result)

        assert "-import numpy" in diff
        assert "+lazy import numpy" in diff

    def test_the_dry_run_says_nothing_was_written(self, make_plan):
        table = render_apply_table(apply(make_plan("import numpy\n")))

        assert "dry run: nothing was written" in table


class TestWrite:
    def test_write_converts_the_statement_on_disk(self, make_plan, tmp_path):
        apply(make_plan("import numpy\n"), ApplyOptions(write=True))

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy\n"
        )

    def test_write_urges_running_the_test_suite(self, make_plan):
        result = apply(make_plan("import numpy\n"), ApplyOptions(write=True))

        assert "run your project's test suite" in render_apply_table(result)

    def test_a_write_that_converted_nothing_does_not_urge_anything(
        self, make_plan, tmp_path
    ):
        result = apply(make_plan("import a.b.c\n"), ApplyOptions(write=True))

        assert "run your project's test suite" not in render_apply_table(result)
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "import a.b.c\n"

    def test_an_unwritable_file_raises_our_error(self, make_plan, tmp_path):
        plan = make_plan("import numpy\n")
        (tmp_path / "sample.py").chmod(0o444)

        with pytest.raises(ApplyInputError, match=r"could not write"):
            apply(plan, ApplyOptions(write=True))

        (tmp_path / "sample.py").chmod(0o644)


class TestVerdictFilter:
    def test_only_safe_statements_are_considered(self, make_plan, tmp_path):
        plan = make_plan(
            "import numpy\nimport pandas\n",
            excluded=["import pandas"],
        )

        result = apply(plan, ApplyOptions(write=True))

        assert [entry.lineno for entry in result.entries] == [1]
        assert "import pandas" in (tmp_path / "sample.py").read_text(encoding="utf-8")

    def test_a_plan_with_no_safe_statements_converts_nothing(self, make_plan):
        plan = make_plan("import numpy\n", excluded=["import numpy"])

        result = apply(plan, ApplyOptions(write=True))

        assert result.entries == ()
        assert render_apply_diff(result) == ""


class TestNativeEmission:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param("import numpy\n", "lazy import numpy\n", id="G1"),
            pytest.param("import numpy as np\n", "lazy import numpy as np\n", id="G2"),
            pytest.param(
                "from typing import Optional\n",
                "lazy from typing import Optional\n",
                id="G6",
            ),
            pytest.param(
                "from typing import Optional as Opt\n",
                "lazy from typing import Optional as Opt\n",
                id="G7",
            ),
            pytest.param(
                "from a.b import thing\n",
                "lazy from a.b import thing\n",
                id="G6-dotted-module",
            ),
        ],
    )
    def test_the_four_emittable_grammar_forms(
        self, make_plan, tmp_path, source, expected
    ):
        apply(make_plan(source), ApplyOptions(write=True))

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == expected

    @pytest.mark.parametrize(
        ("source", "node_type"),
        [
            pytest.param("import numpy\n", cst.LazyImport, id="import"),
            pytest.param(
                "from typing import Optional\n", cst.LazyImportFrom, id="from-import"
            ),
        ],
    )
    def test_the_emitted_node_is_a_real_lazy_node(
        self, make_plan, tmp_path, source, node_type
    ):
        apply(make_plan(source), ApplyOptions(write=True))

        module = cst.parse_module((tmp_path / "sample.py").read_text(encoding="utf-8"))
        line = module.body[0]

        assert isinstance(line, cst.SimpleStatementLine)
        assert isinstance(line.body[0], node_type)

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("import one, two\n", id="G3-multi-target"),
            pytest.param("import a.b.c\n", id="G4-dotted"),
            pytest.param("import a.b.c as d\n", id="G5-dotted-alias"),
            pytest.param("from pkg import (alpha, beta)\n", id="G8-parenthesized"),
            pytest.param("from . import sibling\n", id="G9-relative"),
            pytest.param("from .pkg.mod import thing\n", id="G10-relative-dotted"),
            pytest.param("from pkg import *\n", id="G11-star"),
            pytest.param("from typing import alpha, beta\n", id="multi-name"),
        ],
    )
    def test_shapes_outside_g1_g2_g6_g7_are_refused(self, make_plan, tmp_path, source):
        result = apply(make_plan(source), ApplyOptions(write=True))

        assert result.entries[0].reason is ApplyCode.UNSUPPORTED_FORM
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == source

    def test_a_future_import_is_never_made_lazy(self, make_plan, tmp_path):
        source = "from __future__ import annotations\n"

        result = apply(make_plan(source), ApplyOptions(write=True))

        assert result.entries[0].reason is ApplyCode.UNSUPPORTED_FORM
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == source

    def test_the_g14_spelling_is_never_emitted(self, make_plan, tmp_path):
        apply(make_plan(MIXED_SOURCE), ApplyOptions(write=True))

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert "from . lazy" not in converted
        assert "from .. lazy" not in converted
        for line in converted.splitlines():
            assert "lazy" not in line or line.startswith("lazy ")

    def test_the_mixed_module_converts_exactly_the_emittable_forms(self, make_plan):
        result = apply(make_plan(MIXED_SOURCE))

        assert reason_by_line(result) == {
            3: "UNSUPPORTED_FORM",  # from __future__ import annotations
            5: "converted",  # import numpy
            6: "UNSUPPORTED_FORM",  # import a.b.c
            7: "UNSUPPORTED_FORM",  # import one, two
            8: "converted",  # from typing import Optional as Opt
            9: "converted",  # from collections import OrderedDict
            10: "UNSUPPORTED_FORM",  # from . import sibling
            11: "UNSUPPORTED_FORM",  # from pkg import (alpha, beta)
            12: "COMPOUND_LINE",  # import json; import plugins
        }


class TestPlacement:
    def test_a_safe_statement_outside_module_scope_is_an_internal_error(
        self, make_plan
    ):
        plan = make_plan(
            """
            def load():
                import numpy
                return numpy
            """
        )

        with pytest.raises(CodemodError, match=r"not a module-top-level import"):
            apply(plan)

    def test_a_class_body_import_is_an_internal_error_too(self, make_plan):
        plan = make_plan(
            """
            class Loader:
                import numpy
            """
        )

        with pytest.raises(CodemodError, match=r"placement P1"):
            apply(plan)

    def test_the_error_names_the_statement_it_refused(self, make_plan):
        plan = make_plan(
            """
            try:
                import numpy
            except ImportError:
                numpy = None
            """
        )

        with pytest.raises(CodemodError, match=r"sample\.py:2"):
            apply(plan)


class TestCompoundLines:
    def test_a_line_holding_two_statements_is_never_rewritten(
        self, make_plan, tmp_path
    ):
        source = "import os; import plugins\n"

        result = apply(make_plan(source), ApplyOptions(write=True))

        assert {entry.reason for entry in result.entries} == {ApplyCode.COMPOUND_LINE}
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == source


class TestFallbackEmitter:
    def test_the_fallback_emits_no_lazy_keyword(self, make_plan, tmp_path):
        apply(
            make_plan("import numpy\n"),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert "lazy import" not in converted

    def test_the_fallback_binds_through_lazyloader(self, make_plan, tmp_path):
        apply(
            make_plan("import numpy\n"),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert "importlib.util.LazyLoader" in converted
        assert f'numpy = {HELPER_NAME}("numpy")' in converted

    def test_the_fallback_keeps_an_alias(self, make_plan, tmp_path):
        apply(
            make_plan("import numpy as np\n"),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert f'np = {HELPER_NAME}("numpy")' in converted

    def test_the_fallback_injects_its_helper_only_once(self, make_plan, tmp_path):
        apply(
            make_plan("import numpy\nimport pandas\n"),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert converted.count(f"def {HELPER_NAME}(") == 1

    def test_the_fallback_refuses_from_imports(self, make_plan, tmp_path):
        source = "from requests import Session\n"

        result = apply(
            make_plan(source),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )

        assert result.entries[0].reason is ApplyCode.FALLBACK_UNSUPPORTED
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == source

    def test_the_refusal_reason_reaches_the_json_report(self, make_plan):
        result = apply(
            make_plan("from requests import Session\n"),
            ApplyOptions(target_version=FALLBACK_TARGET),
        )

        document = to_apply_json_dict(result)

        assert document["statements"][0] == {
            "key": "sample.py:1",
            "file": "sample.py",
            "line": 1,
            "status": "skipped",
            "reason": "FALLBACK_UNSUPPORTED",
            "flags": [],
            "before": "from requests import Session",
            "after": None,
        }

    def test_the_generated_helper_really_loads_a_module_lazily(
        self, make_plan, tmp_path, monkeypatch
    ):
        apply(
            make_plan("import json\n"),
            ApplyOptions(target_version=FALLBACK_TARGET, write=True),
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "json", raising=False)
        monkeypatch.delitem(sys.modules, "json", raising=False)

        sample = _fresh_import("sample")

        assert sample.json.dumps({"a": 1}) == '{"a": 1}'

    @pytest.mark.parametrize(
        ("version", "expects_native"),
        [
            pytest.param("3.11", False, id="3.11"),
            pytest.param("3.14", False, id="3.14-upper-fallback-bound"),
            pytest.param("3.15", True, id="3.15-native"),
        ],
    )
    def test_the_target_version_boundary_picks_the_emitter(
        self, make_plan, tmp_path, version, expects_native
    ):
        apply(
            make_plan("import numpy\n"),
            ApplyOptions(target_version=version, write=True),
        )

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert converted.startswith("lazy import") is expects_native

    def test_omitting_the_target_version_emits_native_syntax(self, make_plan, tmp_path):
        apply(make_plan("import numpy\n"), ApplyOptions(write=True))

        assert (tmp_path / "sample.py").read_text(encoding="utf-8").startswith("lazy ")

    def test_an_unknown_target_version_is_rejected(self):
        with pytest.raises(ValueError, match=r"unsupported --target-version '3\.9'"):
            ApplyOptions(target_version="3.9")


class TestIdempotency:
    def test_a_second_native_run_changes_nothing(self, make_plan, tmp_path):
        plan = make_plan(MIXED_SOURCE)
        apply(plan, ApplyOptions(write=True))
        after_first = (tmp_path / "sample.py").read_text(encoding="utf-8")

        second = apply(plan, ApplyOptions(write=True))

        assert second.converted() == ()
        assert render_apply_diff(second) == ""
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == after_first

    def test_a_second_native_run_says_the_statement_is_already_lazy(self, make_plan):
        plan = make_plan("import numpy\n")
        apply(plan, ApplyOptions(write=True))

        second = apply(plan, ApplyOptions(write=True))

        assert second.entries[0].reason is ApplyCode.ALREADY_LAZY

    def test_a_second_fallback_run_changes_nothing(self, make_plan, tmp_path):
        plan = make_plan(MIXED_SOURCE)
        options = ApplyOptions(target_version=FALLBACK_TARGET, write=True)
        apply(plan, options)
        after_first = (tmp_path / "sample.py").read_text(encoding="utf-8")

        second = apply(plan, options)

        assert second.converted() == ()
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == after_first

    def test_an_already_lazy_line_is_recognised_without_bound_names(
        self, make_plan, tmp_path
    ):
        plan = make_plan(
            "VALUE = 1\n",
            extra=[
                {
                    "key": "sample.py:1",
                    "file": "sample.py",
                    "line": 1,
                    "source": "lazy import numpy",
                    "verdict": "safe",
                }
            ],
        )
        (tmp_path / "sample.py").write_text("lazy import numpy\n", encoding="utf-8")

        result = apply(plan, ApplyOptions(write=True))

        assert result.entries[0].reason is ApplyCode.ALREADY_LAZY
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy\n"
        )

    def test_a_hand_written_lazy_import_is_left_alone(self, make_plan, tmp_path):
        plan = make_plan("import numpy\n")
        # Written by hand rather than through make_plan: `ast.parse` on this
        # interpreter cannot read PEP 810 syntax, only LibCST can.
        (tmp_path / "sample.py").write_text("lazy import numpy\n", encoding="utf-8")

        result = apply(plan, ApplyOptions(write=True))

        assert result.entries[0].reason is ApplyCode.ALREADY_LAZY
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy\n"
        )


class TestPreservation:
    def test_every_untouched_line_survives_byte_for_byte(self, make_plan, tmp_path):
        before = (tmp_path / "sample.py").read_text  # bound after make_plan writes it
        plan = make_plan(MIXED_SOURCE)
        original = before()

        apply(plan, ApplyOptions(write=True))
        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        changed = [
            (old, new)
            for old, new in zip(
                original.splitlines(), converted.splitlines(), strict=True
            )
            if old != new
        ]
        assert all(new == f"lazy {old}" for old, new in changed)

    def test_a_trailing_comment_stays_on_the_converted_line(self, make_plan, tmp_path):
        apply(
            make_plan("import numpy  # the expensive one\n"), ApplyOptions(write=True)
        )

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy  # the expensive one\n"
        )

    def test_dunder_all_is_untouched(self, make_plan, tmp_path):
        apply(make_plan(MIXED_SOURCE), ApplyOptions(write=True))

        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert '__all__ = ["Opt", "OrderedDict"]' in converted

    def test_the_fallback_keeps_a_comment_above_the_injection_point(
        self, make_plan, tmp_path
    ):
        plan = make_plan(
            """
            VALUE = 1

            # why numpy is here
            import numpy
            """
        )

        apply(plan, ApplyOptions(target_version=FALLBACK_TARGET, write=True))
        converted = (tmp_path / "sample.py").read_text(encoding="utf-8")

        assert "# why numpy is here\nnumpy = " in converted


class TestStalePlans:
    def test_a_line_that_no_longer_holds_the_planned_statement_is_skipped(
        self, make_plan, tmp_path
    ):
        plan = make_plan("import numpy\n")
        (tmp_path / "sample.py").write_text("import pandas\n", encoding="utf-8")

        result = apply(plan, ApplyOptions(write=True))

        assert result.entries[0].reason is ApplyCode.SOURCE_MISMATCH
        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "import pandas\n"

    def test_a_line_past_the_end_of_the_file_is_skipped(self, make_plan):
        plan = make_plan(
            "import numpy\n",
            extra=[
                {
                    "key": "sample.py:99",
                    "file": "sample.py",
                    "line": 99,
                    "module": "sample",
                    "source": "import gone",
                    "bound_names": ["gone"],
                    "verdict": "safe",
                    "status": "proposed",
                    "reasons": [],
                    "self_us": 1,
                    "cumulative_us": 1,
                }
            ],
        )

        result = apply(plan)

        assert reason_by_line(result)[99] == "SOURCE_MISMATCH"

    def test_a_planned_line_holding_a_compound_statement_is_not_found(self, make_plan):
        plan = make_plan(
            "def load():\n    return 1\n",
            extra=[
                {
                    "key": "sample.py:1",
                    "file": "sample.py",
                    "line": 1,
                    "module": "sample",
                    "source": "def load():",
                    "bound_names": [],
                    "verdict": "safe",
                    "status": "proposed",
                    "reasons": [],
                    "self_us": 1,
                    "cumulative_us": 1,
                }
            ],
        )

        result = apply(plan)

        assert result.entries[0].reason is ApplyCode.NOT_FOUND

    def test_a_planned_line_holding_no_import_is_reported_not_found(self, make_plan):
        plan = make_plan(
            "VALUE = 1\n",
            extra=[
                {
                    "key": "sample.py:1",
                    "file": "sample.py",
                    "line": 1,
                    "module": "sample",
                    "source": "VALUE = 1",
                    "bound_names": [],
                    "verdict": "safe",
                    "status": "proposed",
                    "reasons": [],
                    "self_us": 1,
                    "cumulative_us": 1,
                }
            ],
        )

        result = apply(plan)

        assert result.entries[0].reason is ApplyCode.NOT_FOUND


class TestModuleLevelCallFlag:
    def test_a_name_reached_by_an_import_time_call_is_flagged(self, make_plan):
        plan = make_plan(
            """
            import numpy


            def build():
                return numpy.zeros(3)


            TABLE = build()
            """
        )

        result = apply(plan)

        assert result.entries[0].status is ApplyStatus.CONVERTED
        assert result.entries[0].flags == (FlagCode.MODULE_LEVEL_CALL,)

    def test_the_flag_follows_a_chain_of_local_calls(self, make_plan):
        plan = make_plan(
            """
            import numpy


            def inner():
                return numpy.zeros(3)


            def outer():
                return inner()


            TABLE = outer()
            """
        )

        result = apply(plan)

        assert result.entries[0].flags == (FlagCode.MODULE_LEVEL_CALL,)

    def test_mutually_recursive_functions_do_not_loop(self, make_plan):
        plan = make_plan(
            """
            import numpy


            def ping():
                return pong() or numpy


            def pong():
                return ping()


            TABLE = ping()
            """
        )

        result = apply(plan)

        assert result.entries[0].flags == (FlagCode.MODULE_LEVEL_CALL,)

    def test_a_function_nobody_calls_during_import_raises_no_flag(self, make_plan):
        plan = make_plan(
            """
            import numpy


            def build():
                return numpy.zeros(3)
            """
        )

        result = apply(plan)

        assert result.entries[0].flags == ()

    def test_the_flag_is_explained_in_the_report(self, make_plan):
        plan = make_plan(
            """
            import numpy


            def build():
                return numpy.zeros(3)


            TABLE = build()
            """
        )

        table = render_apply_table(apply(plan))

        assert "MODULE_LEVEL_CALL" in table
        assert "issue #17" in table


class TestPlanInput:
    def test_a_profile_document_is_refused(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({"document": "profile"}), encoding="utf-8")

        with pytest.raises(ApplyInputError, match=r"not a plan"):
            apply(path)

    def test_an_unknown_schema_version_is_refused(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps({"document": "plan", "schema_version": 99}), encoding="utf-8"
        )

        with pytest.raises(ApplyInputError, match=r"schema_version 99"):
            apply(path)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ApplyInputError, match=r"could not read plan document"):
            apply(tmp_path / "absent.json")

    def test_text_that_is_not_json_is_refused(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text("not json", encoding="utf-8")

        with pytest.raises(ApplyInputError, match=r"not valid JSON"):
            apply(path)

    def test_a_json_array_is_refused(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(ApplyInputError, match=r"expected an object"):
            apply(path)

    def test_a_document_without_statements_is_refused(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps({"document": "plan", "schema_version": SCHEMA_VERSION}),
            encoding="utf-8",
        )

        with pytest.raises(ApplyInputError, match=r"missing the 'statements' array"):
            apply(path)

    def test_a_statement_that_is_not_an_object_is_refused(self, make_plan):
        plan = make_plan("import numpy\n", extra=["oops"])

        with pytest.raises(ApplyInputError, match=r"must be objects"):
            apply(plan)

    def test_a_statement_missing_its_line_is_refused(self, make_plan):
        plan = make_plan(
            "import numpy\n",
            extra=[{"key": "k", "file": "sample.py", "verdict": "safe"}],
        )

        with pytest.raises(ApplyInputError, match=r"'line' must be an integer"):
            apply(plan)

    def test_bound_names_must_be_strings(self, make_plan):
        plan = make_plan(
            "import numpy\n",
            extra=[
                {
                    "key": "k",
                    "file": "sample.py",
                    "line": 1,
                    "source": "import numpy",
                    "bound_names": [7],
                    "verdict": "safe",
                }
            ],
        )

        with pytest.raises(ApplyInputError, match=r"'bound_names' must be"):
            apply(plan)

    def test_a_statement_missing_its_file_is_refused(self, make_plan):
        plan = make_plan("import numpy\n", extra=[{"key": "k", "verdict": "safe"}])

        with pytest.raises(ApplyInputError, match=r"'file' must be a string"):
            apply(plan)

    def test_a_statement_without_bound_names_is_still_usable(self, make_plan, tmp_path):
        plan = make_plan(
            "VALUE = 1\nimport numpy\n",
            extra=[
                {
                    "key": "sample.py:2",
                    "file": "sample.py",
                    "line": 2,
                    "source": "import numpy",
                    "verdict": "safe",
                }
            ],
        )

        apply(plan, ApplyOptions(write=True))

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "VALUE = 1\nlazy import numpy\n"
        )

    def test_a_file_escaping_the_source_root_is_refused(self, make_plan):
        plan = make_plan(
            "import numpy\n",
            extra=[
                {
                    "key": "k",
                    "file": "../../etc/passwd.py",
                    "line": 1,
                    "source": "import numpy",
                    "bound_names": [],
                    "verdict": "safe",
                }
            ],
        )

        with pytest.raises(ApplyInputError, match=r"escapes source root"):
            apply(plan)

    def test_a_source_file_that_vanished_is_refused(self, make_plan, tmp_path):
        plan = make_plan("import numpy\n")
        (tmp_path / "sample.py").unlink()

        with pytest.raises(ApplyInputError, match=r"could not read"):
            apply(plan)

    def test_a_source_file_that_does_not_parse_is_refused(self, make_plan, tmp_path):
        plan = make_plan("import numpy\n")
        (tmp_path / "sample.py").write_text("import numpy\ndef (\n", encoding="utf-8")

        with pytest.raises(ApplyInputError, match=r"could not parse"):
            apply(plan)

    def test_a_plan_without_a_source_root_resolves_against_the_cwd(
        self, make_plan, tmp_path, monkeypatch
    ):
        plan = make_plan("import numpy\n")
        document = json.loads(plan.read_text(encoding="utf-8"))
        document["entrypoint"].pop("source_root")
        plan.write_text(json.dumps(document), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        apply(plan, ApplyOptions(write=True))

        assert (tmp_path / "sample.py").read_text(encoding="utf-8") == (
            "lazy import numpy\n"
        )


class TestReport:
    def test_the_document_is_an_apply_document(self, make_plan):
        document = to_apply_json_dict(apply(make_plan("import numpy\n")))

        assert document["document"] == APPLY_DOCUMENT
        assert document["schema_version"] == SCHEMA_VERSION

    def test_the_document_records_the_emitter_that_ran(self, make_plan):
        result = apply(
            make_plan("import numpy\n"),
            ApplyOptions(target_version=FALLBACK_TARGET),
        )

        assert to_apply_json_dict(result)["options"] == {
            "target_version": FALLBACK_TARGET,
            "native": False,
            "write": False,
        }

    def test_the_totals_count_both_outcomes(self, make_plan):
        result = apply(make_plan(MIXED_SOURCE))

        assert to_apply_json_dict(result)["totals"] == {
            "converted_count": 3,
            "skipped_count": 7,
            "flagged_count": 0,
            "file_count": 1,
            "changed_file_count": 1,
        }

    def test_the_json_is_serializable(self, make_plan):
        text = render_apply_json(apply(make_plan(MIXED_SOURCE)))

        assert json.loads(text)["document"] == APPLY_DOCUMENT

    def test_the_table_separates_converted_from_skipped(self, make_plan):
        table = render_apply_table(apply(make_plan(MIXED_SOURCE)))

        assert "converted (3 statement(s)):" in table
        assert "skipped (7 statement(s)):" in table

    def test_the_table_reports_an_empty_plan_without_crashing(self, make_plan):
        table = render_apply_table(
            apply(make_plan("import numpy\n", excluded=["import numpy"]))
        )

        assert "converted (0 statement(s)):\n  (none)" in table
        assert "skipped (0 statement(s)):\n  (none)" in table

    def test_the_diff_of_an_unchanged_run_is_empty(self, make_plan):
        assert render_apply_diff(apply(make_plan("import a.b.c\n"))) == ""

    def test_warnings_reach_the_footer(self, make_plan):
        result = apply(make_plan("import numpy\n"))
        with_warning = replace(result, warnings=("something was odd",))

        table = render_apply_table(with_warning)

        assert "warnings:\n  - something was odd" in table

    def test_several_files_appear_in_one_diff(self, make_plan):
        plan = make_plan({"one.py": "import numpy\n", "two.py": "import pandas\n"})

        diff = render_apply_diff(apply(plan))

        assert "a/one.py" in diff
        assert "a/two.py" in diff


@pytest.mark.skipif(
    sys.version_info < (3, 15),
    reason="PEP 810 lazy imports only exist from Python 3.15",
)
class TestNativeSyntaxOnPython315:
    """REQ-014: the emitted native syntax must run, and keep the public API."""

    SOURCE = textwrap.dedent(
        '''\
        """Converted."""

        import json
        import os as operating_system
        from typing import Optional as Opt

        __all__ = ["Opt", "shout"]


        def shout(text):
            return json.dumps(text).upper()
        '''
    )

    def test_the_converted_module_compiles(self, make_plan, tmp_path):
        apply(make_plan({"native.py": self.SOURCE}), ApplyOptions(write=True))

        converted = (tmp_path / "native.py").read_text(encoding="utf-8")

        assert compile(converted, "native.py", "exec") is not None

    def test_the_converted_module_behaves_the_same(
        self, make_plan, tmp_path, monkeypatch
    ):
        plan = make_plan({"native.py": self.SOURCE})
        monkeypatch.syspath_prepend(str(tmp_path))
        eager = _fresh_import("native")
        expected = (sorted(dir(eager)), eager.__all__, eager.shout({"a": 1}))

        apply(plan, ApplyOptions(write=True))
        lazy = _fresh_import("native")

        assert (sorted(dir(lazy)), lazy.__all__, lazy.shout({"a": 1})) == expected

    def test_the_converted_name_holds_a_proxy_until_it_is_used(
        self, make_plan, tmp_path, monkeypatch
    ):
        apply(make_plan({"native.py": self.SOURCE}), ApplyOptions(write=True))
        monkeypatch.syspath_prepend(str(tmp_path))
        module = _fresh_import("native")
        # Reading through __dict__ hands back the live proxy rather than
        # reifying it, which ordinary attribute access would (PEP 810 S10).
        proxy = module.__dict__["json"]

        assert type(proxy) is types.LazyImportType  # type: ignore[attr-defined]
        module.shout({"a": 1})
        assert module.__dict__["json"] is importlib.import_module("json")


def _fresh_import(name: str) -> ModuleType:
    """Import a module for the first time, discarding any earlier copy."""
    sys.modules.pop(name, None)
    importlib.invalidate_caches()
    return importlib.import_module(name)
