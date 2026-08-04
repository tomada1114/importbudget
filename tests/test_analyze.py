"""Tests for the whitelist engine that runs every rule over every statement."""

from __future__ import annotations

import pytest

from importbudget.analyze import Analyzer, analyze
from importbudget.errors import SourceScanError
from importbudget.rules import RULES

from .conftest import ADVERSARIAL_DIR, FIXTURES_DIR, PROJECT_DIR, safe_sources

#: Every executable statement of the adversarial fixture, by line.
ADVERSARIAL_LINES = (
    13,
    15,
    16,
    17,
    18,
    19,
    20,
    27,
    29,
    31,
    33,
    36,
    39,
    42,
    45,
    49,
    55,
    60,
)


@pytest.fixture
def adversarial_verdicts():
    return analyze(
        ADVERSARIAL_DIR / "__init__.py",
        "adversarial",
        root=FIXTURES_DIR,
    )


class TestZeroFalseSafe:
    def test_not_one_adversarial_statement_lands_in_the_safe_set(
        self, adversarial_verdicts
    ):
        assert safe_sources(adversarial_verdicts) == set()

    def test_every_executable_statement_was_actually_judged(self, adversarial_verdicts):
        judged = tuple(v.statement.lineno for v in adversarial_verdicts)

        assert judged == ADVERSARIAL_LINES

    def test_every_exclusion_carries_at_least_one_reason_code(
        self, adversarial_verdicts
    ):
        assert all(verdict.codes for verdict in adversarial_verdicts)

    def test_the_type_checking_import_is_not_a_candidate_at_all(
        self, adversarial_verdicts
    ):
        judged = {v.statement.lineno for v in adversarial_verdicts}

        # Line 24 is `from collections.abc import Sequence` under TYPE_CHECKING.
        assert 24 not in judged


class TestAnalyze:
    def test_source_order_is_preserved(self):
        verdicts = analyze(PROJECT_DIR / "demopkg" / "__init__.py", "demopkg")

        assert [v.statement.lineno for v in verdicts] == [8, 9, 18, 19, 20]

    def test_the_key_matches_the_attribution_row_key(self):
        verdicts = analyze(
            PROJECT_DIR / "demopkg" / "util.py", "demopkg.util", root=PROJECT_DIR
        )

        assert verdicts[0].key == "demopkg/util.py:3"

    def test_an_unparsable_file_raises(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("import (\n", encoding="utf-8")

        with pytest.raises(SourceScanError, match="could not scan"):
            analyze(broken, "broken")

    def test_a_custom_rule_set_is_honoured(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\n", encoding="utf-8")

        with_all = analyze(path, "sample", rules=RULES)
        with_none = analyze(path, "sample", rules=())

        assert with_all[0].codes
        assert with_none[0].is_safe


class TestAnalyzer:
    def test_a_file_is_analyzed_once_and_then_served_from_cache(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\n\n\ndef f():\n    return decimal\n", "utf-8")
        analyzer = Analyzer(root=tmp_path)
        first = analyzer.verdicts(path, "sample")
        path.unlink()

        assert analyzer.verdicts(path, "sample") == first

    def test_find_returns_the_statement_on_a_line(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\nimport json\n", encoding="utf-8")
        verdict = Analyzer(root=tmp_path).find(path, "sample", 2)

        assert verdict is not None
        assert verdict.statement.source == "import json"

    def test_find_returns_none_for_a_line_holding_no_import(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\n", encoding="utf-8")

        assert Analyzer(root=tmp_path).find(path, "sample", 99) is None

    def test_find_merges_every_statement_sharing_one_line(self, tmp_path):
        # Attribution is line-granular, so `import os; import plugins` is one
        # row covering two statements: the safe one must not vouch for the
        # side-effect one sitting next to it.
        path = tmp_path / "sample.py"
        path.write_text(
            "import os; import mypkg.plugins\n\n\ndef f():\n    return os.getcwd()\n",
            encoding="utf-8",
        )

        verdict = Analyzer(root=tmp_path).find(path, "sample", 1)

        assert verdict is not None
        assert not verdict.is_safe
        assert "UNUSED_IMPORT" in {str(code) for code in verdict.codes}

    def test_find_reports_the_bound_names_of_every_statement_on_the_line(
        self, tmp_path
    ):
        path = tmp_path / "sample.py"
        path.write_text("import os; import json\n", encoding="utf-8")

        verdict = Analyzer(root=tmp_path).find(path, "sample", 1)

        assert verdict is not None
        assert set(verdict.bound_names) == {"os", "json"}

    def test_find_leaves_a_lone_statement_untouched(self, tmp_path):
        path = tmp_path / "sample.py"
        path.write_text("import decimal\n\n\ndef f():\n    return decimal\n", "utf-8")

        verdict = Analyzer(root=tmp_path).find(path, "sample", 1)

        assert verdict is not None
        assert verdict.is_safe

    def test_an_unparsable_file_yields_a_warning_instead_of_raising(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("import (\n", encoding="utf-8")
        analyzer = Analyzer(root=tmp_path)

        assert analyzer.verdicts(broken, "broken") == ()
        assert any("could not scan" in warning for warning in analyzer.warnings)

    def test_warnings_are_deduplicated(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("import (\n", encoding="utf-8")
        analyzer = Analyzer(root=tmp_path)
        analyzer.verdicts(broken, "broken")
        analyzer.verdicts(broken, "broken")

        assert len(analyzer.warnings) == 1
