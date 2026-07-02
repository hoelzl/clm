"""CLI tests for the ``CLM_SYNC_ENGINE=v3`` verb dispatch (#520 Phase 3, §12.5).

One dispatch point at the verb layer: ``report`` / ``apply`` switch to the v3
engine under the env flag (v2 stays the default), ``record`` exists only
there. The tests drive the full dogfood loop through the CLI — record →
report clean → mutate → report flags → apply → report clean — and pin the
envelope's stable booleans plus the flag hygiene in both directions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group


@pytest.fixture
def cli_runner():
    # Click 8.1 needs ``mix_stderr=False``; Click 8.2+ removed the parameter.
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
    start = output.index("{")
    return json.loads(output[start:])


V3 = {"CLM_SYNC_ENGINE": "v3"}


class TestDispatch:
    def test_record_requires_the_v3_engine(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["record", str(de)])
        assert result.exit_code != 0
        assert "CLM_SYNC_ENGINE=v3" in result.output

    def test_v3_only_apply_flags_are_rejected_under_v2(self, cli_runner: CliRunner, tmp_path: Path):
        de, _ = _write_pair(tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--dry-run"])
        assert result.exit_code != 0
        assert "CLM_SYNC_ENGINE=v3" in result.output

    def test_v2_only_report_flags_are_rejected_under_v3(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, _ = _write_pair(tmp_path)
        result = cli_runner.invoke(
            slides_sync_group, ["report", str(de), "--use-watermark"], env=V3
        )
        assert result.exit_code != 0
        assert "v2 engine" in result.output

    def test_report_under_v2_still_runs_the_v2_engine(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch
    ):
        # No env flag: the v2 path runs (its envelope has no "engine": "v3").
        de, _ = _write_pair(tmp_path)
        monkeypatch.setenv("CLM_JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
        result = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        if result.exit_code == 2:  # no git repo around tmp: v2 needs a baseline
            assert "engine" not in (result.output or "")
        else:
            payload = _json_payload(result.output)
            assert payload.get("engine") != "v3"


class TestV3Loop:
    def test_record_report_mutate_apply_report(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)

        # A never-recorded deck is cold: work pending (exit 1), agent needed.
        cold = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"], env=V3)
        assert cold.exit_code == 1, cold.output
        payload = _json_payload(cold.output)
        assert payload["schema"] == 3 and payload["engine"] == "v3"
        assert payload["is_clean"] is False
        assert payload["needs_agent"] is True
        assert {i["action"] for i in payload["items"]} == {"verify_cold"}
        assert all(i["answers"] == ["confirm"] for i in payload["items"])

        # record blesses the current state (verify-gated).
        record = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"], env=V3)
        assert record.exit_code == 0, record.output
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()

        clean = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"], env=V3)
        assert clean.exit_code == 0, clean.output
        assert _json_payload(clean.output)["is_clean"] is True

        # One shared edit -> one mechanical item -> apply propagates it.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        flagged = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"], env=V3)
        assert flagged.exit_code == 1
        items = _json_payload(flagged.output)["items"]
        assert [i["action"] for i in items] == ["propagate_shared_edit"]

        applied = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"], env=V3)
        assert applied.exit_code == 0, applied.output
        assert "x = 42" in en.read_text(encoding="utf-8")

        again = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"], env=V3)
        assert again.exit_code == 0, again.output

    def test_apply_decisions_from_stdin(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)], env=V3).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        decisions = json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]})
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
            env=V3,
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")

    def test_apply_residue_exits_one(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)], env=V3).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"], env=V3)
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["counts"]["pending"] == 1
        assert "DE neu" not in en.read_text(encoding="utf-8")

    def test_record_refuses_a_structurally_corrupt_pair(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        de, en = _write_pair(tmp_path)
        # Corrupt the EN half: drop the localized twin so the ids are asymmetric.
        en.write_text(
            en.read_text(encoding="utf-8").replace('slide_id="s0-m"', 'slide_id="s0-x"'),
            "utf-8",
        )
        result = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"], env=V3)
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["refused"] == 1
        assert not (tmp_path / ".clm" / "sync-ledger.json").is_file()

    def test_report_over_a_directory_aggregates(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["report", str(tmp_path), "--json"], env=V3)
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["engine"] == "v3"
        assert len(payload["pairs"]) == 1
