"""CLI tests for ``clm slides sync report`` / ``apply`` on the sequence core.

Phase C of ``docs/claude/design/sync-sequence-core.md``: with
``CLM_SYNC_CORE=sequence`` the two verbs run ``clm.slides.doc_pass``; the
envelope is schema 6, ``engine: "sequence"``. Without it, nothing changes
(``test_sync_v3_cli.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.sync_wire import SEQUENCE_WIRE_SCHEMA, WIRE_SCHEMA
from tests.cli.test_sync_v3_cli import DE, EN, _json_payload, _stderr, _write_pair


@pytest.fixture
def runner() -> CliRunner:
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


@pytest.fixture
def pair(tmp_path: Path, runner: CliRunner) -> tuple[Path, Path]:
    de, en = _write_pair(tmp_path)
    assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
    return de, en


@pytest.fixture
def sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLM_SYNC_CORE", "sequence")


def _report(runner: CliRunner, de: Path) -> dict:
    result = runner.invoke(slides_sync_group, ["report", str(de), "--json"])
    return _json_payload(result.output)


def _document(tmp_path: Path, report_id: str, *decisions: dict, schema: int = 6) -> Path:
    path = tmp_path / "decisions.json"
    doc = {"schema": schema, "report_id": report_id, "decisions": list(decisions)}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.usefixtures("sequence")
class TestSequenceCore:
    def test_a_clean_deck_reports_clean_on_schema_6(
        self, runner: CliRunner, pair: tuple[Path, Path]
    ) -> None:
        payload = _report(runner, pair[0])
        assert payload["schema"] == SEQUENCE_WIRE_SCHEMA
        assert payload["engine"] == "sequence"
        assert payload["is_clean"] is True
        assert payload["exit_code"] == 0

    def test_report_apply_report(
        self, runner: CliRunner, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        de, en = pair
        de.write_text(DE.replace("# DE Text", "# DE Text neu").replace("x = 1", "x = 2"), "utf-8")
        payload = _report(runner, de)
        deck = payload["pairs"][0]
        assert payload["exit_code"] == 1 and payload["needs_model"] is True
        assert [(i["action"], i["key"], i["answers"]) for i in deck["items"]] == [
            ("translate_edit", "id:s0-m", ["body", "replace", "keep_twin"])
        ]
        assert [(c["action"], c["key"], c["side"]) for c in deck["changes"]] == [
            ("mirror", "seq:3", "en")
        ]
        document = _document(tmp_path, deck["report_id"], {"key": "id:s0-m", "body": "# EN new"})
        result = runner.invoke(slides_sync_group, ["apply", str(de), "--decisions", str(document)])
        assert result.exit_code == 0, result.output + _stderr(result)
        # the body writer is the positional engine's (it adds the blank `#` line)
        assert en.read_text("utf-8") == EN.replace("# EN text", "#\n# EN new").replace(
            "x = 1", "x = 2"
        )
        assert _report(runner, de)["is_clean"] is True

    def test_apply_with_a_question_open_writes_nothing(
        self, runner: CliRunner, pair: tuple[Path, Path]
    ) -> None:
        de, en = pair
        de.write_text(DE.replace("# DE Text", "# DE Text neu").replace("x = 1", "x = 2"), "utf-8")
        result = runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert result.exit_code == 1
        payload = _json_payload(result.output)
        assert payload["wrote"] is False and payload["ledger_recorded"] is False
        assert en.read_text("utf-8") == EN

    def test_a_stale_document_is_refused(
        self, runner: CliRunner, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        de, _en = pair
        document = _document(tmp_path, "0" * 16)
        result = runner.invoke(slides_sync_group, ["apply", str(de), "--decisions", str(document)])
        assert result.exit_code == 2
        assert "re-run `clm slides sync report" in _stderr(result)

    def test_a_schema_5_document_is_refused(
        self, runner: CliRunner, pair: tuple[Path, Path], tmp_path: Path
    ) -> None:
        de, _en = pair
        report_id = _report(runner, de)["pairs"][0]["report_id"]
        document = _document(tmp_path, report_id, schema=WIRE_SCHEMA)
        result = runner.invoke(slides_sync_group, ["apply", str(de), "--decisions", str(document)])
        assert result.exit_code == 2
        assert "announces schema 5" in _stderr(result)

    def test_member_and_since_are_positional_only(
        self, runner: CliRunner, pair: tuple[Path, Path]
    ) -> None:
        de, _en = pair
        result = runner.invoke(slides_sync_group, ["apply", str(de), "--member", "id:s0"])
        assert result.exit_code == 2 and "--member" in result.output + _stderr(result)
        result = runner.invoke(slides_sync_group, ["report", str(de), "--since", "HEAD"])
        assert result.exit_code == 2 and "--since" in result.output + _stderr(result)


def test_the_positional_engine_refuses_a_schema_6_document(
    runner: CliRunner, pair: tuple[Path, Path], tmp_path: Path
) -> None:
    de, _en = pair
    report_id = _json_payload(
        runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
    )["report_id"]
    document = _document(tmp_path, report_id, schema=SEQUENCE_WIRE_SCHEMA)
    result = runner.invoke(slides_sync_group, ["apply", str(de), "--decisions", str(document)])
    assert result.exit_code == 2
    assert "announces schema 6" in _stderr(result)


def test_an_unknown_engine_name_is_a_usage_error(
    runner: CliRunner, pair: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLM_SYNC_CORE", "sequnce")
    result = runner.invoke(slides_sync_group, ["report", str(pair[0])])
    assert result.exit_code == 2
    assert "CLM_SYNC_CORE" in result.output + _stderr(result)


class TestIdentifyPerHalf:
    """#1266: the core asks ``identify`` once per half for one handle; the
    answer names its half with ``side`` beside the ``choice``. The shared
    parser used to refuse that shape (exit 2), so it was unanswerable."""

    @pytest.fixture
    def swapped(self, tmp_path: Path, runner: CliRunner) -> Path:
        from tests.slides.test_doc_pass import DE as DOC_DE
        from tests.slides.test_doc_pass import EN as DOC_EN

        de, en = tmp_path / "slides_t.de.py", tmp_path / "slides_t.en.py"
        de.write_text(DOC_DE, encoding="utf-8", newline="\n")
        en.write_text(DOC_EN, encoding="utf-8", newline="\n")
        assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        cells = "# %%\nx = 1\n\n# %%\ny = 2\n"
        # both halves swap the two code cells and edit both, differently
        de.write_text(
            DOC_DE.replace(cells, "# %%\ny = 20\n\n# %%\nx = 10\n"), encoding="utf-8", newline="\n"
        )
        en.write_text(
            DOC_EN.replace(cells, "# %%\ny = 21\n\n# %%\nx = 11\n"), encoding="utf-8", newline="\n"
        )
        return de

    @pytest.mark.usefixtures("sequence")
    def test_identify_answers_name_their_half(
        self, runner: CliRunner, swapped: Path, tmp_path: Path
    ) -> None:
        deck = _report(runner, swapped)["pairs"][0]
        items = [i for i in deck["items"] if i["action"] == "identify"]
        assert sorted((i["key"], i["side"]) for i in items) == [
            ("seq:3", "de"),
            ("seq:3", "en"),
            ("seq:4", "de"),
            ("seq:4", "en"),
        ]
        # every template of a per-half row carries that half
        assert all(t["side"] == i["side"] for i in items for t in i["templates"])
        # recorded x (seq:3) is now the 4th cell on both halves, y the 3rd
        target = {"seq:3": "4", "seq:4": "3"}
        decisions = [
            {
                "key": i["key"],
                "action": "identify",
                "choice": f"new:{i['side']}:deck:{target[i['key']]}",
                "side": i["side"],
            }
            for i in items
        ]
        document = _document(tmp_path, deck["report_id"], *decisions)
        result = runner.invoke(
            slides_sync_group, ["apply", str(swapped), "--decisions", str(document), "--json"]
        )
        assert result.exit_code != 2, result.output + _stderr(result)
        payload = _json_payload(result.output)
        assert payload["rejected"] == []
        answered = {
            (i["key"], i["side"]): i["answer"]
            for i in payload["items"]
            if i["action"] == "identify"
        }
        # each half's row took its own answer, not the first one on the handle
        assert answered == {
            ("seq:3", "de"): "new:de:deck:4",
            ("seq:4", "de"): "new:de:deck:3",
            ("seq:3", "en"): "new:en:deck:4",
            ("seq:4", "en"): "new:en:deck:3",
        }
        # with the cells identified, the core reaches the questions after them
        assert {i["action"] for i in payload["items"] if i["answer"] is None} == {
            "conflict_shared",
            "order_decision",
        }
