"""CLI + end-to-end tests for ``clm slides rename`` (issue #991).

The money test: a recorded deck renamed through the command reports
``is_clean`` afterwards — the ledger section was re-keyed, not dropped to
cold — and its build-cache rows follow the files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.rename import rename_cmd
from clm.cli.commands.slides.sync import slides_sync_group
from clm.cli.main import cli

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
VO_DE = '# %% [markdown] lang="de" tags=["notes"] slide_id="s0-vo" for_slide="s0"\n# Sprich.\n'
VO_EN = '# %% [markdown] lang="en" tags=["notes"] slide_id="s0-vo" for_slide="s0"\n# Speak.\n'


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _write_pair(topic: Path, stem: str = "slides_old") -> tuple[Path, Path]:
    topic.mkdir(parents=True, exist_ok=True)
    de = topic / f"{stem}.de.py"
    en = topic / f"{stem}.en.py"
    de.write_text(DE, encoding="utf-8")
    en.write_text(EN, encoding="utf-8")
    return de, en


def _write_companions(topic: Path, stem: str = "old") -> tuple[Path, Path]:
    vo = topic / "voiceover"
    vo.mkdir(exist_ok=True)
    de = vo / f"voiceover_{stem}.de.py"
    en = vo / f"voiceover_{stem}.en.py"
    de.write_text(VO_DE, encoding="utf-8")
    en.write_text(VO_EN, encoding="utf-8")
    return de, en


def _json_payload(output: str) -> dict:
    return json.loads(output[output.index("{") :])


def _record(cli_runner, de: Path) -> None:
    res = cli_runner.invoke(slides_sync_group, ["record", str(de)])
    assert res.exit_code == 0, res.output


def _report(cli_runner, de: Path) -> dict:
    res = cli_runner.invoke(slides_sync_group, ["report", str(de), "--json"])
    return _json_payload(res.output)


def _invoke(cli_runner, args: list[str], *, cache_db: Path | None = None):
    """Run through the real ``clm`` entry point so ``ctx.obj`` carries the DB paths."""
    prefix = ["--cache-db-path", str(cache_db)] if cache_db is not None else []
    return cli_runner.invoke(cli, [*prefix, "slides", "rename", *args])


class TestRenameKeepsTheLedgerWarm:
    def test_recorded_deck_is_clean_after_rename(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        de, en = _write_pair(topic)
        vo_de, vo_en = _write_companions(topic)
        _record(cli_runner, de)
        assert _report(cli_runner, de)["is_clean"] is True

        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json", "--no-validate"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["old_stem"] == "slides_old"
        assert payload["new_stem"] == "slides_new"
        assert payload["ledger_section"] is True
        assert payload["ledger_migrated"] is True
        assert {m["role"] for m in payload["moves"]} == {
            "de",
            "en",
            "de_companion",
            "en_companion",
        }

        new_de = topic / "slides_new.de.py"
        assert new_de.exists() and (topic / "slides_new.en.py").exists()
        assert not de.exists() and not en.exists()
        assert (topic / "voiceover" / "voiceover_new.de.py").exists()
        assert not vo_de.exists() and not vo_en.exists()

        # The point of the command: not cold, not even a single framed row.
        after = _report(cli_runner, new_de)
        assert after["is_clean"] is True, after
        assert after["deck_key"] == "slides_new"
        assert not any(i["action"] == "verify_cold" for i in after["items"])

    def test_rename_then_edit_still_frames_translate_edit(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        """The carried baseline keeps framing edits — the #572 property, one
        level up: a stem rename must not turn the next edit into a cold
        ``confirm`` that could bank a stale twin."""
        topic = tmp_path / "topic_100_x"
        de, _ = _write_pair(topic)
        _record(cli_runner, de)
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--no-validate"])
        assert res.exit_code == 0, res.output
        new_en = topic / "slides_new.en.py"
        new_en.write_text(
            new_en.read_text(encoding="utf-8").replace("EN text", "EN text rewritten"), "utf-8"
        )
        report = _report(cli_runner, topic / "slides_new.de.py")
        edited = next(i for i in report["items"] if i["key"] == "id:s0-m")
        assert edited["action"] == "translate_edit"

    def test_unrecorded_deck_renames_with_nothing_to_migrate(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        topic = tmp_path / "topic_100_x"
        de, _ = _write_pair(topic)
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json", "--no-validate"])
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["ledger_section"] is False
        assert payload["ledger_migrated"] is False
        assert (topic / "slides_new.de.py").exists()


class TestCacheAndGit:
    def test_cache_rows_follow_the_files(self, cli_runner: CliRunner, tmp_path: Path):
        from nbformat.v4 import new_code_cell, new_notebook

        from clm.infrastructure.database.cache_path_migration import _connect, _distinct_paths
        from clm.infrastructure.database.db_operations import DatabaseManager
        from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
        from tests.infrastructure.database.test_cache_path_migration import _result

        topic = tmp_path / "topic_100_x"
        de, en = _write_pair(topic)
        cache_db = tmp_path / "clm_cache.db"
        with DatabaseManager(cache_db) as m:
            m.store_latest_result(str(de), "h1", "corr", _result(), retain_count=3)
            m.store_latest_result(str(en), "h2", "corr", _result(), retain_count=3)
        nb = new_notebook()
        nb.cells = [new_code_cell("print(1)")]
        with ExecutedNotebookCache(cache_db) as c:
            c.store(str(de), "h1", "de", "python", nb)

        res = _invoke(
            cli_runner, [str(de), "slides_new", "--json", "--no-validate"], cache_db=cache_db
        )
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["cache"]["rows_rewritten"] >= 3

        conn = _connect(cache_db)
        try:
            processed = set(_distinct_paths(conn, "processed_files", "file_path"))
            executed = set(_distinct_paths(conn, "executed_notebooks", "input_file"))
        finally:
            conn.close()
        assert processed == {str(topic / "slides_new.de.py"), str(topic / "slides_new.en.py")}
        assert executed == {str(topic / "slides_new.de.py")}

    def test_dry_run_touches_nothing_but_reports_the_cache(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        from clm.infrastructure.database.db_operations import DatabaseManager
        from tests.infrastructure.database.test_cache_path_migration import _result

        topic = tmp_path / "topic_100_x"
        de, en = _write_pair(topic)
        _record(cli_runner, de)
        cache_db = tmp_path / "clm_cache.db"
        with DatabaseManager(cache_db) as m:
            m.store_latest_result(str(de), "h1", "corr", _result(), retain_count=3)
        ledger_before = (topic / ".clm" / "sync-ledger.json").read_bytes()

        res = _invoke(cli_runner, [str(de), "slides_new", "--dry-run", "--json"], cache_db=cache_db)
        assert res.exit_code == 0, res.output
        payload = _json_payload(res.output)
        assert payload["report_only"] is True
        assert payload["cache"]["dry_run"] is True
        assert payload["cache"]["rows_rewritten"] == 1
        assert payload["validation"] is None
        assert de.exists() and en.exists()
        assert not (topic / "slides_new.de.py").exists()
        assert (topic / ".clm" / "sync-ledger.json").read_bytes() == ledger_before

    def test_no_cache_migrate_skips_the_cache(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        de, _ = _write_pair(topic)
        res = _invoke(
            cli_runner,
            [str(de), "slides_new", "--json", "--no-validate", "--no-cache-migrate"],
            cache_db=tmp_path / "clm_cache.db",
        )
        assert res.exit_code == 0, res.output
        assert _json_payload(res.output)["cache"] is None

    def test_git_mv_is_used_inside_a_work_tree(self, cli_runner: CliRunner, tmp_path: Path):
        repo = tmp_path / "repo"
        topic = repo / "slides" / "topic_100_x"
        de, en = _write_pair(topic)
        _write_companions(topic)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "init"],
            cwd=repo,
            check=True,
        )

        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json", "--no-validate"])
        assert res.exit_code == 0, res.output
        assert _json_payload(res.output)["git_mv"] is True
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        renamed = [line for line in status.splitlines() if line.startswith("R")]
        assert len(renamed) == 4, status


class TestValidation:
    def test_valid_deck_reports_ok(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        de, _ = _write_pair(topic)
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json"])
        assert res.exit_code == 0, res.output
        validation = _json_payload(res.output)["validation"]
        assert validation["errors"] == []

    def test_validation_errors_exit_one_after_a_completed_rename(
        self, cli_runner: CliRunner, tmp_path: Path
    ):
        """The rename lands regardless; the exit code reports the deck's state."""
        topic = tmp_path / "topic_100_x"
        de, en = _write_pair(topic)
        # A dangling for_slide in a companion is a pairing error clm validate reports.
        vo = topic / "voiceover"
        vo.mkdir()
        (vo / "voiceover_old.de.py").write_text(
            '# %% [markdown] lang="de" tags=["notes"] for_slide="gone"\n# x\n', encoding="utf-8"
        )
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json"])
        assert res.exit_code == 1, res.output
        payload = _json_payload(res.output)
        assert payload["validation"]["errors"], payload
        assert (topic / "slides_new.de.py").exists()
        assert (vo / "voiceover_new.de.py").exists()


class TestGuards:
    def test_missing_twin_rejected_without_single(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        topic.mkdir()
        de = topic / "slides_old.de.py"
        de.write_text(DE, encoding="utf-8")
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json"])
        assert res.exit_code == 2
        assert "no EN twin" in _json_payload(res.output)["error"]

    def test_single_renames_the_lone_half(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        topic.mkdir()
        de = topic / "slides_old.de.py"
        de.write_text(DE, encoding="utf-8")
        res = cli_runner.invoke(
            rename_cmd, [str(de), "slides_new", "--single", "--json", "--no-validate"]
        )
        assert res.exit_code == 0, res.output
        assert [m["role"] for m in _json_payload(res.output)["moves"]] == ["de"]
        assert (topic / "slides_new.de.py").exists()

    def test_collision_rejected_before_anything_moves(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        de, en = _write_pair(topic)
        (topic / "slides_new.de.py").write_text("taken", encoding="utf-8")
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new", "--json"])
        assert res.exit_code == 2
        assert "already exists" in _json_payload(res.output)["error"]
        assert de.exists() and en.exists()

    def test_bad_stem_rejected_as_usage_error(self, cli_runner: CliRunner, tmp_path: Path):
        topic = tmp_path / "topic_100_x"
        de, _ = _write_pair(topic)
        res = cli_runner.invoke(rename_cmd, [str(de), "slides_new.de.py"])
        assert res.exit_code == 2
        assert "bare stem" in res.output

    def test_listed_in_slides_help(self, cli_runner: CliRunner):
        res = cli_runner.invoke(cli, ["slides", "--help"])
        assert res.exit_code == 0
        assert " rename " in res.output or "  rename" in res.output
