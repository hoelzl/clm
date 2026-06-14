"""CLI tests for ``clm slides sync --rebaseline`` and the stale-watermark hint (#364).

A watermark goes *stale* when both halves of a split deck are edited and committed
without an intervening ``sync``: the recorded baseline falls behind, so a later sync
errors/conflicts against it even though the halves are mutually consistent. These
tests reproduce that state (seed an old watermark + git-commit newer, consistent
content) and assert:

- a normal run surfaces an actionable stale-watermark hint;
- ``--rebaseline`` resets the watermark when git HEAD is clean;
- ``--rebaseline`` refuses when git HEAD shows real changes (so it cannot mask an
  un-synced edit).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import CACHE_DB_NAME, slides_sync_cmd
from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import ordered_sync_cells

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _deck(lang: str, body: str, *, sid: str = "intro") -> str:
    return (
        f"# j2 from 'macros.j2' import header_{lang}\n"
        f'# {{{{ header_{lang}("T") }}}}\n'
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n'
        f"# {body}\n"
    )


def _commit_pair(tmp_path: Path, de_text: str, en_text: str, *, stem: str = "slides_intro"):
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de_text, encoding="utf-8")
    en_path.write_text(en_text, encoding="utf-8")

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True
        )

    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "Test")
    _git("add", "-A")
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    return de_path, en_path


def _seed_watermark(cache_dir: Path, de_path: Path, en_path: Path, *, de_text: str, en_text: str):
    cache_dir.mkdir(parents=True, exist_ok=True)
    wm = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    try:
        for lang, text in (("de", de_text), ("en", en_text)):
            cells = ordered_sync_cells(parse_cells(text), lang)
            wm.put_deck(
                de_path=str(de_path),
                en_path=str(en_path),
                lang=lang,
                cells=[
                    (c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells
                ],
            )
    finally:
        wm.close()


def _rows(cache_dir: Path) -> int:
    wm = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    try:
        return len(wm.iter_entries())
    finally:
        wm.close()


def _stale_consistent(tmp_path: Path):
    """A committed, mutually-consistent pair with an *older* watermark (stale).

    Returns ``(de_path, en_path, cache_dir)``. The current committed bodies differ
    from the seeded watermark on *both* halves, so a watermark-baseline sync sees a
    two-sided conflict; git HEAD (== current) is clean.
    """
    de_path, en_path = _commit_pair(tmp_path, _deck("de", "neu"), _deck("en", "new"))
    cache = tmp_path / "cache"
    _seed_watermark(cache, de_path, en_path, de_text=_deck("de", "alt"), en_text=_deck("en", "old"))
    return de_path, en_path, cache


class TestStaleHint:
    def test_hint_on_dry_run(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--cache-dir", str(cache), str(de_path)]
        )
        # A two-sided conflict against the stale watermark — not clean.
        assert res.exit_code != 0
        assert "watermark is stale" in res.stderr
        assert "--rebaseline" in res.stderr

    def test_hint_in_json(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--json", "--cache-dir", str(cache), str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["rebaseline_hint"] is True

    def test_no_hint_when_consistent(self, cli_runner, tmp_path):
        # Watermark matches the committed content → clean, no hint.
        de_path, en_path = _commit_pair(tmp_path, _deck("de", "x"), _deck("en", "y"))
        cache = tmp_path / "cache"
        _seed_watermark(cache, de_path, en_path, de_text=_deck("de", "x"), en_text=_deck("en", "y"))
        res = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code == 0
        assert "watermark is stale" not in res.stderr


class TestRebaseline:
    def test_resets_stale_watermark(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code == 0, res.output + res.stderr
        assert "re-baselined" in res.output
        # The watermark now matches the current state: a follow-up sync is clean.
        follow = cli_runner.invoke(
            slides_sync_cmd, ["--dry-run", "--cache-dir", str(cache), str(de_path)]
        )
        assert follow.exit_code == 0
        assert "watermark is stale" not in follow.stderr

    def test_json(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--json", "--cache-dir", str(cache), str(de_path)]
        )
        payload = json.loads(res.output[res.output.find("{") :])
        assert payload["rebaselined"] is True
        assert payload["mode"] == "rebaseline"

    def test_refuses_on_real_divergence(self, cli_runner, tmp_path):
        # Commit a pair, then edit one half on disk (uncommitted) → git HEAD is NOT
        # clean, so --rebaseline must refuse rather than mask the un-synced edit.
        de_path, en_path = _commit_pair(tmp_path, _deck("de", "x"), _deck("en", "y"))
        cache = tmp_path / "cache"
        _seed_watermark(
            cache, de_path, en_path, de_text=_deck("de", "old"), en_text=_deck("en", "old")
        )
        de_path.write_text(_deck("de", "edited-uncommitted"), encoding="utf-8")
        rows_before = _rows(cache)

        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code == 2
        assert "refusing to --rebaseline" in res.output
        assert _rows(cache) == rows_before  # watermark untouched

    def test_rejects_dry_run_combo(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--dry-run", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code != 0
        assert "mutually exclusive" in (res.stderr + res.output)

    def test_rejects_no_cache_combo(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--no-cache", "--cache-dir", str(cache), str(de_path)]
        )
        assert res.exit_code != 0
        assert "no-cache" in (res.stderr + res.output)

    def test_rejects_directory(self, cli_runner, tmp_path):
        de_path, _en, cache = _stale_consistent(tmp_path)
        res = cli_runner.invoke(
            slides_sync_cmd, ["--rebaseline", "--cache-dir", str(cache), str(tmp_path)]
        )
        assert res.exit_code != 0
        assert "single deck pair" in (res.stderr + res.output)
