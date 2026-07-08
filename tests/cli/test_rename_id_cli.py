"""CLI + end-to-end tests for ``clm slides rename-id`` (issue #572).

The money test (:meth:`TestRenameThenEdit.test_rename_then_edit_frames_translate_edit`)
reproduces the reported footgun and proves the fix: after a manual id rename the
ledger stays warm, so a subsequent edit of the renamed cell frames
``translate_edit`` (with a fresh twin body answer) — never the silent
``verify_cold`` that would bank the stale twin on ``confirm``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.rename_id import rename_id_cmd
from clm.cli.commands.slides.sync import slides_sync_group


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"

DE = (
    HEADER_DE
    + '# %% [markdown] lang="de" tags=["slide"] slide_id="s0"\n#\n# # Titel\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="de" slide_id="s0-m"\n# DE Text\n'
)
EN = (
    HEADER_EN
    + '# %% [markdown] lang="en" tags=["slide"] slide_id="s0"\n#\n# # Title\n\n'
    + '# %% tags=["keep"]\nx = 1\n\n'
    + '# %% [markdown] lang="en" slide_id="s0-m"\n# EN text\n'
)


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    de = tmp_path / "slides_t.de.py"
    en = tmp_path / "slides_t.en.py"
    de.write_text(DE, encoding="utf-8")
    en.write_text(EN, encoding="utf-8")
    return de, en


def _json_payload(output: str) -> dict:
    return json.loads(output[output.index("{") :])


def _record(cli_runner, de: Path) -> None:
    assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0


def _report(cli_runner, de: Path) -> dict:
    res = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
    return _json_payload(res.output)


class TestRenameThenEdit:
    def test_rename_then_edit_frames_translate_edit(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)
        assert _report(cli_runner, de)["is_clean"] is True

        # Rename the non-anchor member id on BOTH halves + the ledger.
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["ledger_migrated"] is True
        assert payload["slide_id_hits"] == {"de": 1, "en": 1}
        assert 'slide_id="s0-x"' in de.read_text(encoding="utf-8")
        assert 'slide_id="s0-x"' in en.read_text(encoding="utf-8")

        # The ledger stayed warm: a pure rename reports clean, NOT cold.
        after_rename = _report(cli_runner, de)
        assert after_rename["is_clean"] is True, after_rename
        assert not any(i["action"] == "verify_cold" for i in after_rename["items"])

        # Now edit the EN body of the renamed cell. Because the baseline was
        # migrated (not dropped to cold), this frames translate_edit — the twin
        # can be re-translated — instead of a verify_cold that would confirm the
        # stale German.
        en.write_text(
            en.read_text(encoding="utf-8").replace("EN text", "EN text rewritten"), "utf-8"
        )
        after_edit = _report(cli_runner, de)
        actions = {i["action"] for i in after_edit["items"]}
        assert "verify_cold" not in actions, after_edit
        edited = next(i for i in after_edit["items"] if i["key"] == "id:s0-x")
        assert edited["action"] == "translate_edit"
        assert "body" in edited["answers"]

        # And the loop closes: supplying the fresh DE twin resolves it to clean.
        decisions = json.dumps({"decisions": [{"key": "id:s0-x", "body": "# DE neu"}]})
        applied = cli_runner.invoke(
            slides_sync_group, ["apply", str(de), "--decisions", "-", "--json"], input=decisions
        )
        assert applied.exit_code == 0, applied.output
        assert "# DE neu" in de.read_text(encoding="utf-8")
        assert _report(cli_runner, de)["is_clean"] is True

    def test_rename_anchor_stays_clean(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0", "s0-new"])
        assert res.exit_code == 0, res.output
        assert 'slide_id="s0-new"' in de.read_text(encoding="utf-8")

        # The positional `x = 1` under the s0 group cascaded its group token —
        # the deck is still fully in sync.
        report = _report(cli_runner, de)
        assert report["is_clean"] is True, report


class TestRenameGuards:
    def test_missing_old_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "nope", "whatever", "--json"])
        assert res.exit_code == 2
        assert "no cell carries" in _json_payload(res.output)["error"]

    def test_collision_with_existing_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0", "--json"])
        assert res.exit_code == 2
        assert "already exists" in _json_payload(res.output)["error"]

    def test_same_old_new_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-m", "--json"])
        assert res.exit_code == 2
        assert "same id" in _json_payload(res.output)["error"]

    def test_invalid_new_id_rejected(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "bad id", "--json"])
        assert res.exit_code == 2
        assert "not a usable slide_id" in _json_payload(res.output)["error"]

    def test_dry_run_writes_nothing(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        _record(cli_runner, de)
        before_de, before_en = de.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")
        ledger_before = (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8")

        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--report-only", "--json"])
        assert res.exit_code == 0, res.output
        assert _json_payload(res.output)["report_only"] is True

        assert de.read_text(encoding="utf-8") == before_de
        assert en.read_text(encoding="utf-8") == before_en
        assert (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8") == ledger_before

    def test_rename_without_ledger_still_rewrites_files(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)  # never recorded — no ledger
        res = cli_runner.invoke(rename_id_cmd, [str(de), "s0-m", "s0-x", "--json"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["ledger_migrated"] is False
        assert 'slide_id="s0-x"' in de.read_text(encoding="utf-8")
