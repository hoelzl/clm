"""The decision-row shape (#1189), through the real CLI, on BOTH engines.

Unknown row fields are a document-level shape error (exit 2), and every
report item carries ``templates``: one ready-to-fill decision row per answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from clm.slides.doc_apply import (
    BODY_PLACEHOLDER,
    REPLACE_PLACEHOLDER,
    SIDE_PLACEHOLDER,
    answer_templates,
    parse_decision_rows,
)
from clm.slides.sync_wire import SEQUENCE_WIRE_SCHEMA, WIRE_SCHEMA
from tests.cli.test_sync_v3_cli import DE, EN, _json_payload, _stderr, _write_pair


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
def edited(tmp_path: Path, runner: CliRunner, engine: str) -> tuple[Path, Path]:
    """A recorded pair whose DE text was edited since: one translate_edit."""
    de, en = _write_pair(tmp_path)
    assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
    de.write_text(DE.replace("# DE Text", "# DE Text neu"), "utf-8")
    return de, en


def _report(runner: CliRunner, de: Path, engine: str) -> tuple[str, list[dict]]:
    payload = _json_payload(runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output)
    if engine == "sequence":
        pair = payload["pairs"][0]
        return pair["report_id"], pair["items"]
    return payload["report_id"], payload["items"]


def _apply(runner: CliRunner, de: Path, engine: str, report_id: str, *rows: dict):
    document = {
        "schema": SEQUENCE_WIRE_SCHEMA if engine == "sequence" else WIRE_SCHEMA,
        "report_id": report_id,
        "decisions": list(rows),
    }
    return runner.invoke(
        slides_sync_group,
        ["apply", str(de), "--decisions", "-", "--json"],
        input=json.dumps(document),
    )


class TestUnknownFields:
    def test_a_junk_field_beside_a_valid_answer_is_refused(
        self, runner: CliRunner, edited: tuple[Path, Path], engine: str
    ) -> None:
        de, en = edited
        report_id, _items = _report(runner, de, engine)
        result = _apply(
            runner, de, engine, report_id, {"key": "id:s0-m", "choice": "keep_twin", "junk": 1}
        )
        assert result.exit_code == 2, result.output + _stderr(result)
        assert "unknown field(s) 'junk'" in _stderr(result)
        assert en.read_text("utf-8") == EN

    def test_a_misspelled_side_is_refused(self) -> None:
        rows, errors = parse_decision_rows([{"key": "id:x", "body": "# a", "sied": "de"}])
        assert rows == []
        assert len(errors) == 1 and "'sied'" in errors[0] and "'side'" in errors[0]

    def test_every_documented_field_is_accepted(self) -> None:
        rows, errors = parse_decision_rows(
            [{"key": "id:x", "action": "verify_cold", "body": "# a", "side": "de"}]
        )
        assert errors == [] and len(rows) == 1


class TestTemplates:
    def test_each_item_carries_one_template_per_answer(
        self, runner: CliRunner, edited: tuple[Path, Path], engine: str
    ) -> None:
        de, _en = edited
        _report_id, items = _report(runner, de, engine)
        (item,) = [i for i in items if i["key"] == "id:s0-m"]
        assert item["answers"] == ["body", "replace", "keep_twin"]
        assert item["templates"] == [
            {"key": "id:s0-m", "action": "translate_edit", "body": BODY_PLACEHOLDER},
            {
                "key": "id:s0-m",
                "action": "translate_edit",
                "replace": [list(pair) for pair in REPLACE_PLACEHOLDER],
            },
            {"key": "id:s0-m", "action": "translate_edit", "choice": "keep_twin"},
        ]

    def test_a_filled_template_applies(
        self, runner: CliRunner, edited: tuple[Path, Path], engine: str
    ) -> None:
        de, en = edited
        report_id, items = _report(runner, de, engine)
        (item,) = [i for i in items if i["key"] == "id:s0-m"]
        row = dict(item["templates"][0], body="#\n# EN text new")
        result = _apply(runner, de, engine, report_id, row)
        assert result.exit_code == 0, result.output + _stderr(result)
        assert "# EN text new" in en.read_text("utf-8")

    def test_side_appears_where_a_body_needs_it(self) -> None:
        assert answer_templates("id:x", "verify_translation", ("confirm", "body")) == [
            {"key": "id:x", "action": "verify_translation", "choice": "confirm"},
            {
                "key": "id:x",
                "action": "verify_translation",
                "body": BODY_PLACEHOLDER,
                "side": SIDE_PLACEHOLDER,
            },
        ]

    def test_an_unedited_side_placeholder_is_refused(self) -> None:
        (template,) = answer_templates("id:x", "verify_cold", ("body",))
        _rows, errors = parse_decision_rows([template])
        assert errors and "'side' must be 'de' or 'en'" in errors[0]
