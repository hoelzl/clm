"""CLI tests for the sync verbs (#520; sole engine since the Phase 4 cutover).

The tests drive the full loop through the CLI — record → report clean →
mutate → report flags → apply → report clean — and pin the envelope's
stable booleans.
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


class TestSyncLoop:
    def test_record_report_mutate_apply_report(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)

        # A never-recorded deck is cold: work pending (exit 1), agent needed.
        cold = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert cold.exit_code == 1, cold.output
        payload = _json_payload(cold.output)
        assert payload["schema"] == 3 and payload["engine"] == "v3"
        assert payload["is_clean"] is False
        assert payload["needs_agent"] is True
        assert {i["action"] for i in payload["items"]} == {"verify_cold"}
        # id-keyed cold members also advertise `body` (inline stale-twin recovery,
        # issue #572); positional ones stay confirm-only (no addressable id).
        for i in payload["items"]:
            expected = ["confirm", "body"] if i["key"].startswith("id:") else ["confirm"]
            assert i["answers"] == expected, i

        # record blesses the current state (verify-gated).
        record = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert record.exit_code == 0, record.output
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()

        clean = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert clean.exit_code == 0, clean.output
        assert _json_payload(clean.output)["is_clean"] is True

        # One shared edit -> one mechanical item -> apply propagates it.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        flagged = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert flagged.exit_code == 1
        items = _json_payload(flagged.output)["items"]
        assert [i["action"] for i in items] == ["propagate_shared_edit"]

        applied = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert applied.exit_code == 0, applied.output
        assert "x = 42" in en.read_text(encoding="utf-8")

        again = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert again.exit_code == 0, again.output

    def test_apply_decisions_from_stdin(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        decisions = json.dumps({"decisions": [{"key": "id:s0-m", "body": "# EN new"}]})
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert result.exit_code == 0, result.output
        assert "# EN new" in en.read_text(encoding="utf-8")

    def test_cold_body_recovery_fixes_a_stale_twin(self, cli_runner: CliRunner, tmp_path: Path):
        # Issue #572: on a cold deck an id-keyed member whose EN was rewritten
        # (DE twin now stale) is framed verify_cold — which now also offers a
        # `body` answer. Supplying it with `side` overwrites the stale twin in
        # one pass instead of `confirm` banking the stale German.
        de, en = _write_pair(tmp_path)
        # The DE twin of s0-m is a stale placeholder relative to the EN body.
        de.write_text(
            de.read_text(encoding="utf-8").replace("# DE Text", "# *(placeholder)*"), "utf-8"
        )

        report = _json_payload(
            cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"]).output
        )
        s0m = next(i for i in report["items"] if i["key"] == "id:s0-m")
        assert s0m["action"] == "verify_cold"
        assert s0m["answers"] == ["confirm", "body"]

        # Fix the stale DE twin inline; confirm the rest of the cold pairs.
        rows = []
        for item in report["items"]:
            if item["key"] == "id:s0-m":
                rows.append({"key": item["key"], "body": "# DE frisch übersetzt", "side": "de"})
            else:
                rows.append({"key": item["key"], "choice": "confirm"})
        applied = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=json.dumps({"decisions": rows}),
        )
        assert applied.exit_code == 0, applied.output
        de_text = de.read_text(encoding="utf-8")
        assert "# DE frisch übersetzt" in de_text
        assert "placeholder" not in de_text

        clean = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert clean.exit_code == 0, clean.output
        assert _json_payload(clean.output)["is_clean"] is True

    def test_apply_residue_exits_one(self, cli_runner: CliRunner, tmp_path: Path):
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        de.write_text(de.read_text(encoding="utf-8").replace("DE Text", "DE neu"), "utf-8")
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
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
        result = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["refused"] == 1
        assert not (tmp_path / ".clm" / "sync-ledger.json").is_file()

    def test_report_over_a_directory_aggregates(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path)
        result = cli_runner.invoke(slides_sync_group, ["report", str(tmp_path), "--json"])
        assert result.exit_code == 1, result.output
        payload = _json_payload(result.output)
        assert payload["engine"] == "v3"
        assert len(payload["pairs"]) == 1

    def test_confirm_only_apply_persists_the_ledger(self, cli_runner: CliRunner, tmp_path: Path):
        # Review regression: a confirm-only apply mutates no file, but its
        # ledger updates must still be saved — silently discarding them made
        # every confirmation a no-op.
        de, _en = _write_pair(tmp_path)
        cold = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        decisions = json.dumps(
            {
                "decisions": [
                    {"key": item["key"], "choice": "confirm"}
                    for item in _json_payload(cold.output)["items"]
                ]
            }
        )
        result = cli_runner.invoke(
            slides_sync_group,
            ["apply", str(de), "--decisions", "-", "--json"],
            input=decisions,
        )
        assert result.exit_code == 0, result.output
        payload = _json_payload(result.output)
        assert payload["ledger_recorded"] is True
        assert (tmp_path / ".clm" / "sync-ledger.json").is_file()
        again = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
        assert again.exit_code == 0, again.output

    def test_apply_never_records_a_structurally_corrupt_pair(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Review regression: the lens tolerates (observes) an id-asymmetry
        # the structural gate refuses — apply may write its mechanical items,
        # but the ledger must not bless members of a corrupt pair.
        de, en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        en.write_text(
            en.read_text(encoding="utf-8").replace('slide_id="s0-m"', 'slide_id="s0-x"'),
            "utf-8",
        )
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        ledger_before = (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8")
        result = cli_runner.invoke(slides_sync_group, ["apply", str(de), "--json"])
        assert result.exit_code != 0, result.output
        payload = _json_payload(result.output)
        assert payload["ledger_recorded"] is False
        assert payload["verify_violations"]
        assert (tmp_path / ".clm" / "sync-ledger.json").read_text(encoding="utf-8") == ledger_before

    def test_directory_sweep_warns_on_solo_halves(self, cli_runner: CliRunner, tmp_path: Path):
        _write_pair(tmp_path)
        (tmp_path / "slides_solo.de.py").write_text(DE, encoding="utf-8")
        result = cli_runner.invoke(slides_sync_group, ["report", str(tmp_path), "--json"])
        payload = _json_payload(result.output)
        assert any("slides_solo" in s for s in payload["skipped_solos"])
        stderr = getattr(result, "stderr", "") or result.output
        assert "no twin half found" in stderr

    def test_rerecord_sweep_is_write_free_on_clean_pairs(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # Issue #555: a repo-wide re-record must not bump confirmed_commit on
        # unchanged members — the committed ledger stays byte-identical even
        # though HEAD has moved since the first record.
        import subprocess

        de, _en = _write_pair(tmp_path)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        git("add", ".")
        git("commit", "-q", "-m", "base")

        first = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert first.exit_code == 0, first.output
        ledger_path = tmp_path / ".clm" / "sync-ledger.json"
        before = ledger_path.read_bytes()
        assert _head_sha(tmp_path) in before.decode("utf-8")  # first record stamps HEAD

        # Move HEAD without touching the pair, then re-record.
        git("add", ".")
        git("commit", "-q", "-m", "record")
        (tmp_path / "other.txt").write_text("unrelated\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "move HEAD")

        second = cli_runner.invoke(slides_sync_group, ["record", str(de), "--json"])
        assert second.exit_code == 0, second.output
        payload = _json_payload(second.output)
        assert payload["unchanged"] == 1
        assert payload["pairs"][0]["ledger_changed"] is False
        assert ledger_path.read_bytes() == before

    def test_bare_deck_path_defaults_to_report(self, cli_runner: CliRunner, tmp_path: Path):
        de, _en = _write_pair(tmp_path)
        assert cli_runner.invoke(slides_sync_group, ["record", str(de)]).exit_code == 0
        result = cli_runner.invoke(slides_sync_group, [str(de), "--json"])
        assert result.exit_code == 0, result.output
        assert _json_payload(result.output)["is_clean"] is True


class TestSinceView:
    def test_since_diffs_against_the_ref_not_the_ledger(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        # --since is a forensic VIEW (design §12.3): the baseline is the bundle
        # at the ref; the ledger is neither consulted nor required.
        import subprocess

        de, en = _write_pair(tmp_path)

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        git("add", ".")
        git("commit", "-q", "-m", "base")

        # No ledger, unchanged since HEAD -> the window view is clean even
        # though the ledger view would report every member cold.
        clean = cli_runner.invoke(
            slides_sync_group, ["report", str(de), "--since", "HEAD", "--json"]
        )
        assert clean.exit_code == 0, clean.output
        payload = _json_payload(clean.output)
        assert payload["is_clean"] is True
        assert payload["baseline"] == "since:" + _head_sha(tmp_path)

        # A shared edit in the window shows up as exactly that item.
        de.write_text(de.read_text(encoding="utf-8").replace("x = 1", "x = 42"), "utf-8")
        flagged = cli_runner.invoke(
            slides_sync_group, ["report", str(de), "--since", "HEAD", "--json"]
        )
        assert flagged.exit_code == 1, flagged.output
        items = _json_payload(flagged.output)["items"]
        assert [i["action"] for i in items] == ["propagate_shared_edit"]
        # The view never wrote a ledger.
        assert not (tmp_path / ".clm" / "sync-ledger.json").is_file()


def _head_sha(cwd: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()
