"""The ``replace`` answer (#1171), unit-level and through the real CLI.

``{"key": ..., "replace": [[old, new], ...]}`` is a ``body`` given as edits of
the target cell: each ``old`` must match exactly once, pairs apply in order.
The CLI tests run on BOTH engines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.doc_apply import _ItemError, parse_decision_rows, resolve_replace
from clm.slides.sync_wire import SEQUENCE_WIRE_SCHEMA, WIRE_SCHEMA
from tests.cli.test_sync_v3_cli import DE, EN, _json_payload, _stderr

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"

#: A deck whose localized cell has several lines to edit.
DE2 = DE.replace("# DE Text\n", "#\n# Ein Satz.\n# Noch ein Satz mit Satz.\n")
EN2 = EN.replace("# EN text\n", "#\n# One sentence.\n# Another sentence with a sentence.\n")


# ---------------------------------------------------------------------------
# resolve_replace and the row shape
# ---------------------------------------------------------------------------


class TestResolveReplace:
    def test_one_pair(self) -> None:
        assert resolve_replace("# a b c", [("b", "B")], where="x") == "# a B c"

    def test_several_pairs_in_order(self) -> None:
        # the second pair is counted against the result of the first
        out = resolve_replace("# cat dog", [("cat", "dog dog"), ("dog dog dog", "x")], where="x")
        assert out == "# x"

    def test_multi_line_old(self) -> None:
        out = resolve_replace("#\n# one\n# two\n", [("one\n# two", "1\n# 2")], where="x")
        assert out == "#\n# 1\n# 2"

    def test_zero_matches(self) -> None:
        with pytest.raises(_ItemError, match="replace pair #1: 0 matches in x"):
            resolve_replace("# a", [("b", "c")], where="x")

    def test_two_matches(self) -> None:
        with pytest.raises(_ItemError, match="2 matches in x — extend `old`"):
            resolve_replace("# a a", [("a", "b")], where="x")

    def test_overlapping_matches_count(self) -> None:
        with pytest.raises(_ItemError, match="2 matches"):
            resolve_replace("# aaa", [("aa", "b")], where="x")

    def test_a_failing_later_pair_fails_the_whole_row(self) -> None:
        with pytest.raises(_ItemError, match="replace pair #2: 0 matches"):
            resolve_replace("# a b", [("a", "A"), ("zzz", "y")], where="x")

    def test_a_result_equal_to_the_base_is_rejected(self) -> None:
        with pytest.raises(_ItemError, match="change nothing"):
            resolve_replace("# ab", [("a", "x"), ("xb", "ab")], where="x")


class TestRowShape:
    @pytest.mark.parametrize(
        ("replace", "message"),
        [
            ("old->new", "non-empty list"),
            ([], "non-empty list"),
            ([["a"]], "pair #1 must be a list of two strings"),
            ([["a", 1]], "pair #1 must be a list of two strings"),
            ([["", "x"]], "'old' is empty"),
            ([["a", "a"]], "the same"),
        ],
    )
    def test_shape_errors(self, replace: object, message: str) -> None:
        rows, errors = parse_decision_rows([{"key": "id:x", "replace": replace}])
        assert rows == [] and len(errors) == 1 and message in errors[0], errors

    @pytest.mark.parametrize("other", [{"choice": "keep_twin"}, {"body": "# a"}])
    def test_replace_stands_alone(self, other: dict) -> None:
        _rows, errors = parse_decision_rows([{"key": "id:x", "replace": [["a", "b"]], **other}])
        assert errors and "exactly one of 'choice', 'body' or 'replace'" in errors[0]

    def test_side_accompanies_replace(self) -> None:
        rows, errors = parse_decision_rows([{"key": "id:x", "replace": [["a", "b"]], "side": "de"}])
        assert errors == []
        assert rows[0].replace == (("a", "b"),) and rows[0].side == "de"


# ---------------------------------------------------------------------------
# The CLI, on both engines
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


@pytest.fixture(params=["positional", "sequence"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "sequence":
        monkeypatch.setenv("CLM_SYNC_CORE", "sequence")
    else:
        monkeypatch.delenv("CLM_SYNC_CORE", raising=False)
    return request.param


@pytest.fixture
def pair(tmp_path: Path, runner: CliRunner, engine: str) -> tuple[Path, Path]:
    de = tmp_path / "slides_t.de.py"
    en = tmp_path / "slides_t.en.py"
    de.write_text(DE2, encoding="utf-8")
    en.write_text(EN2, encoding="utf-8")
    assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
    # one translate_edit: the DE text changed since the record
    de.write_text(DE2.replace("Ein Satz.", "Ein neuer Satz."), "utf-8")
    return de, en


def _report(runner: CliRunner, de: Path, engine: str) -> tuple[str, list[dict]]:
    payload = _json_payload(runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output)
    if engine == "sequence":
        deck = payload["pairs"][0]
        return deck["report_id"], deck["items"]
    return payload["report_id"], payload["items"]


def _apply(runner: CliRunner, de: Path, engine: str, *rows: dict, dry_run: bool = False):
    report_id, _items = _report(runner, de, engine)
    document = {
        "schema": SEQUENCE_WIRE_SCHEMA if engine == "sequence" else WIRE_SCHEMA,
        "report_id": report_id,
        "decisions": list(rows),
    }
    args = ["apply", str(de), "--decisions", "-", "--json"]
    if dry_run:
        args.append("--dry-run")
    return runner.invoke(slides_sync_group, args, input=json.dumps(document))


def _rejections(result, engine: str) -> list[str]:
    payload = _json_payload(result.output)
    if engine == "sequence":
        return [r["reason"] for r in payload["rejected"]]
    return [i["reason"] for i in payload["items"] if i["status"] == "rejected"]


def _clean(runner: CliRunner, de: Path) -> bool:
    payload = _json_payload(runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output)
    return payload["is_clean"] is True


class TestTranslateEditReplace:
    def test_report_advertises_replace(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        _rid, items = _report(runner, pair[0], engine)
        (item,) = [i for i in items if i["key"] == "id:s0-m"]
        assert item["action"] == "translate_edit"
        assert "replace" in item["answers"]
        (template,) = [t for t in item["templates"] if "replace" in t]
        assert "side" not in template

    def test_report_replace_apply_clean(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        """The claimed-wired path: report → replace → apply → clean, on disk."""
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "replace": [["One sentence.", "A new sentence."]]},
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert en.read_text("utf-8") == EN2.replace("One sentence.", "A new sentence.")
        assert _clean(runner, de)

    def test_several_pairs_and_multi_line_old(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {
                "key": "id:s0-m",
                "replace": [
                    ["One sentence.\n# Another", "A new sentence.\n# One more"],
                    ["with a sentence", "with a clause"],
                ],
            },
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert "# A new sentence.\n# One more sentence with a clause.\n" in en.read_text("utf-8")
        assert _clean(runner, de)

    @pytest.mark.parametrize(
        ("pairs", "reason"),
        [
            ([["Nowhere", "x"]], "0 matches in the en body of id:s0-m"),
            ([["sentence", "x"]], "matches in the en body of id:s0-m — extend `old`"),
        ],
    )
    def test_a_bad_pair_is_rejected_and_writes_no_part(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str, pairs: list, reason: str
    ) -> None:
        de, en = pair
        result = _apply(runner, de, engine, {"key": "id:s0-m", "replace": pairs})
        assert result.exit_code == 1, result.output + _stderr(result)
        reasons = _rejections(result, engine)
        assert len(reasons) == 1 and reason in reasons[0], reasons
        assert en.read_text("utf-8") == EN2

    def test_dry_run_reports_a_bad_pair_and_writes_nothing(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner, de, engine, {"key": "id:s0-m", "replace": [["Nowhere", "x"]]}, dry_run=True
        )
        assert result.exit_code == 1, result.output + _stderr(result)
        assert "0 matches" in _rejections(result, engine)[0]
        assert en.read_text("utf-8") == EN2

    def test_side_on_translate_edit_is_rejected(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "replace": [["One sentence.", "x."]], "side": "en"},
        )
        assert result.exit_code == 1, result.output + _stderr(result)
        assert "'side' is not meaningful" in _rejections(result, engine)[0]
        assert en.read_text("utf-8") == EN2

    def test_a_delimiter_in_new_is_rejected(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "replace": [["One sentence.", "x\n# %% [markdown]\n# y"]]},
        )
        assert result.exit_code == 1, result.output + _stderr(result)
        assert "delimiter" in _rejections(result, engine)[0]
        assert en.read_text("utf-8") == EN2

    def test_on_a_member_that_frames_nothing_it_is_rejected(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "replace": [["One sentence.", "x."]]},
            {"key": "id:s0", "replace": [["Title", "Heading"]], "side": "en"},
        )
        assert result.exit_code == 1, result.output + _stderr(result)
        reasons = _rejections(result, engine)
        assert len(reasons) == 1, reasons
        payload = _json_payload(result.output)
        if engine == "positional":
            assert payload["counts"].get("already_applied", 0) == 0

    def test_a_stale_report_id_refuses_the_document(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        document = {
            "schema": SEQUENCE_WIRE_SCHEMA if engine == "sequence" else WIRE_SCHEMA,
            "report_id": "0" * 16,
            "decisions": [{"key": "id:s0-m", "replace": [["One sentence.", "x."]]}],
        }
        result = runner.invoke(
            slides_sync_group, ["apply", str(de), "--decisions", "-"], input=json.dumps(document)
        )
        assert result.exit_code == 2
        assert en.read_text("utf-8") == EN2


class TestPositionalOnly:
    """Rows the sequence core does not ask with a body (its verify rows take
    ``confirm`` only), so ``replace`` is the positional engine's there."""

    def test_verify_translation_replace_needs_side(
        self, runner: CliRunner, tmp_path: Path, engine: str
    ) -> None:
        if engine != "positional":
            pytest.skip("the sequence core's verify_translation takes confirm only")
        de = tmp_path / "slides_t.de.py"
        en = tmp_path / "slides_t.en.py"
        de.write_text(DE2, encoding="utf-8")
        en.write_text(EN2, encoding="utf-8")
        assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(DE2.replace("Ein Satz.", "Ein neuer Satz."), "utf-8")
        en.write_text(EN2.replace("One sentence.", "One sentense."), "utf-8")
        _rid, items = _report(runner, de, engine)
        (item,) = [i for i in items if i["key"] == "id:s0-m"]
        assert item["action"] == "verify_translation" and "replace" in item["answers"]
        missing = _apply(runner, de, engine, {"key": "id:s0-m", "replace": [["sentense", "x"]]})
        assert missing.exit_code == 1
        assert "must name the 'side'" in _rejections(missing, engine)[0]
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "replace": [["One sentense.", "One new sentence."]], "side": "en"},
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert "# One new sentence.\n" in en.read_text("utf-8")
        assert _clean(runner, de)

    def test_the_title_macro(self, runner: CliRunner, tmp_path: Path, engine: str) -> None:
        de = tmp_path / "slides_t.de.py"
        en = tmp_path / "slides_t.en.py"
        de.write_text(DE2, encoding="utf-8")
        en.write_text(EN2, encoding="utf-8")
        assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(DE2.replace("Titel DE", "Neuer Titel DE"), "utf-8")
        _rid, items = _report(runner, de, engine)
        (item,) = [i for i in items if i["key"] == "id:title"]
        assert item["action"] == "translate_edit"
        result = _apply(
            runner, de, engine, {"key": "id:title", "replace": [["Title EN", "New EN"]]}
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert 'header_en("New EN")' in en.read_text("utf-8")
        assert _clean(runner, de)
