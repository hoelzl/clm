"""The decision-document answer contract, through the real CLI, on BOTH engines.

Every test here runs twice: on the positional engine (the default) and on the
sequence core (``CLM_SYNC_CORE=sequence``). The answer layer is shared
(``clm.slides.doc_apply.Decision`` / ``parse_decision_rows``), so its contract
must hold on both.
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


@pytest.fixture(params=["positional", "sequence"])
def engine(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "sequence":
        monkeypatch.setenv("CLM_SYNC_CORE", "sequence")
    else:
        monkeypatch.delenv("CLM_SYNC_CORE", raising=False)
    return request.param


@pytest.fixture
def pair(tmp_path: Path, runner: CliRunner, engine: str) -> tuple[Path, Path]:
    de, en = _write_pair(tmp_path)
    assert runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
    return de, en


def _report(runner: CliRunner, de: Path) -> dict:
    return _json_payload(runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output)


def _report_id(runner: CliRunner, de: Path, engine: str) -> str:
    payload = _report(runner, de)
    return payload["pairs"][0]["report_id"] if engine == "sequence" else payload["report_id"]


def _is_clean(runner: CliRunner, de: Path) -> bool:
    return _report(runner, de)["is_clean"] is True


def _apply(runner: CliRunner, de: Path, engine: str, *decisions: dict, extra: tuple = ()):
    document = {
        "schema": SEQUENCE_WIRE_SCHEMA if engine == "sequence" else WIRE_SCHEMA,
        "report_id": _report_id(runner, de, engine),
        "decisions": list(decisions),
    }
    return runner.invoke(
        slides_sync_group,
        ["apply", str(de), "--decisions", "-", "--json", *extra],
        input=json.dumps(document),
    )


def _rejections(result, engine: str) -> list[str]:
    """Each rejected row's reason, from either engine's apply payload."""
    payload = _json_payload(result.output)
    if engine == "sequence":
        return [r["reason"] for r in payload["rejected"]]
    return [i["reason"] for i in payload["items"] if i["status"] == "rejected"]


class TestBodyWithSideOnAnUnchangedTwin:
    """#1182: ``body`` + ``side`` on a member that frames nothing is the
    German-only fix: it writes that side and records the member."""

    def test_writes_the_named_side_and_records(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner, de, engine, {"key": "id:s0-m", "body": "#\n# korrigiert", "side": "de"}
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert de.read_text("utf-8") == DE.replace("# DE Text", "#\n# korrigiert")
        assert en.read_text("utf-8") == EN
        assert _is_clean(runner, de)

    def test_writes_the_english_side_too(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(runner, de, engine, {"key": "id:s0-m", "body": "#\n# fixed", "side": "en"})
        assert result.exit_code == 0, result.output + _stderr(result)
        assert en.read_text("utf-8") == EN.replace("# EN text", "#\n# fixed")
        assert de.read_text("utf-8") == DE
        assert _is_clean(runner, de)

    def test_without_side_is_rejected_naming_side(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(runner, de, engine, {"key": "id:s0-m", "body": "# korrigiert"})
        assert result.exit_code == 1, result.output + _stderr(result)
        reasons = _rejections(result, engine)
        assert len(reasons) == 1 and "'side'" in reasons[0], reasons
        assert de.read_text("utf-8") == DE and en.read_text("utf-8") == EN

    def test_a_body_that_already_holds_writes_nothing(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(runner, de, engine, {"key": "id:s0-m", "body": "# DE Text", "side": "de"})
        assert result.exit_code == 0, result.output + _stderr(result)
        assert _rejections(result, engine) == []
        assert de.read_text("utf-8") == DE and en.read_text("utf-8") == EN
        if engine == "positional":
            payload = _json_payload(result.output)
            assert payload["counts"].get("already_applied") == 1

    def test_a_delimiter_in_the_body_is_rejected(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0-m", "body": "# a\n# %% [markdown]\n# b", "side": "de"},
        )
        assert result.exit_code == 1, result.output + _stderr(result)
        reasons = _rejections(result, engine)
        assert len(reasons) == 1 and "delimiter" in reasons[0], reasons
        assert de.read_text("utf-8") == DE and en.read_text("utf-8") == EN

    def test_a_shared_member_is_rejected(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        report = _report(runner, de)
        assert report["is_clean"] is True
        # the shared code cell ``x = 1`` has no id: address it the engine's way
        key = "seq:3" if engine == "sequence" else "pos:s0/code/0"
        result = _apply(runner, de, engine, {"key": key, "body": "x = 2", "side": "de"})
        assert result.exit_code == 1, result.output + _stderr(result)
        reasons = _rejections(result, engine)
        assert len(reasons) == 1 and "shared" in reasons[0], reasons
        assert de.read_text("utf-8") == DE and en.read_text("utf-8") == EN

    def test_next_to_a_framed_edit_elsewhere(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        de.write_text(DE.replace("# # Titel", "# # Neuer Titel"), "utf-8")
        result = _apply(
            runner,
            de,
            engine,
            {"key": "id:s0", "body": "#\n# # New Title"},
            {"key": "id:s0-m", "body": "# korrigiert", "side": "de"},
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert "# korrigiert" in de.read_text("utf-8")
        assert "# # New Title" in en.read_text("utf-8")
        assert _is_clean(runner, de)

    def test_the_title_macro(self, runner: CliRunner, pair: tuple[Path, Path], engine: str) -> None:
        de, en = pair
        body = '# {{ header_de("Neuer Titel") }}'
        result = _apply(runner, de, engine, {"key": "id:title", "body": body, "side": "de"})
        assert result.exit_code == 0, result.output + _stderr(result)
        assert de.read_text("utf-8") == DE.replace("Titel DE", "Neuer Titel")
        assert en.read_text("utf-8") == EN
        assert _is_clean(runner, de)

    def test_a_member_with_only_a_mechanical_row(
        self, runner: CliRunner, pair: tuple[Path, Path], engine: str
    ) -> None:
        de, en = pair
        de.write_text(DE.replace('slide_id="s0-m"', 'tags=["alt"] slide_id="s0-m"'), "utf-8")
        result = _apply(
            runner, de, engine, {"key": "id:s0-m", "body": "#\n# korrigiert", "side": "de"}
        )
        assert result.exit_code == 0, result.output + _stderr(result)
        assert "# korrigiert" in de.read_text("utf-8")
        assert 'tags=["alt"]' in en.read_text("utf-8")
        assert _is_clean(runner, de)
