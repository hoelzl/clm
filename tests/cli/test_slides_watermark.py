"""CLI tests for ``clm slides watermark`` (Issue #363).

The ``watermark`` group inspects and maintains the structural watermark that
``clm slides sync`` records in ``clm-llm.sqlite``. These tests seed watermark rows
directly into a temp cache (via :class:`SyncWatermarkCache`) and drive the CLI
surface — so they exercise pair resolution, on-disk orphan detection, the dry-run
gate, and row deletion without a live sync.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import CACHE_DB_NAME
from clm.cli.commands.slides.watermark import (
    watermark_clear_cmd,
    watermark_list_cmd,
    watermark_prune_cmd,
)
from clm.infrastructure.llm.cache import SyncWatermarkCache


@pytest.fixture
def cli_runner():
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


_DECK = (
    "# j2 from 'macros.j2' import header_de\n"
    '# {{ header_de("Titel") }}\n'
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
    "# Hallo\n"
)


def _make_pair(directory: Path, stem: str = "slides_intro") -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    de_path = directory / f"{stem}.de.py"
    en_path = directory / f"{stem}.en.py"
    de_path.write_text(_DECK, encoding="utf-8")
    en_path.write_text(_DECK, encoding="utf-8")
    return de_path, en_path


def _seed(
    cache_dir: Path,
    de_path: Path | str,
    en_path: Path | str,
    *,
    langs: tuple[str, ...] = ("de", "en"),
) -> None:
    """Seed a watermark for a pair, keyed by the resolved-path strings sync writes."""
    cache = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    de_key = str(Path(de_path).resolve())
    en_key = str(Path(en_path).resolve())
    for lang in langs:
        cache.put_deck(
            de_path=de_key,
            en_path=en_key,
            lang=lang,
            cells=[(0, "intro", "slide", f"h-{lang}", None)],
        )
    cache.close()


def _count_rows(cache_dir: Path) -> int:
    cache = SyncWatermarkCache(cache_dir / CACHE_DB_NAME)
    try:
        return len(cache.iter_entries())
    finally:
        cache.close()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_empty(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        res = cli_runner.invoke(watermark_list_cmd, ["--cache-dir", str(cache)])
        assert res.exit_code == 0
        assert "no watermarks found." in res.output

    def test_lists_ok_and_orphan(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        _seed(
            cache,
            tmp_path / "decks" / "gone.de.py",
            tmp_path / "decks" / "gone.en.py",
            langs=("de",),
        )

        res = cli_runner.invoke(watermark_list_cmd, ["--cache-dir", str(cache)])
        assert res.exit_code == 0
        assert "OK    " in res.output
        assert "ORPHAN" in res.output
        assert "2 pair(s), 1 orphan(ed)." in res.output

    def test_orphans_filter(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        _seed(
            cache,
            tmp_path / "decks" / "gone.de.py",
            tmp_path / "decks" / "gone.en.py",
            langs=("de",),
        )

        res = cli_runner.invoke(watermark_list_cmd, ["--cache-dir", str(cache), "--orphans"])
        assert res.exit_code == 0
        assert "gone.de.py" in res.output
        assert "slides_intro.de.py" not in res.output

    def test_json(self, cli_runner, tmp_path):
        import json

        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        res = cli_runner.invoke(watermark_list_cmd, ["--cache-dir", str(cache), "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert len(data) == 1
        assert data[0]["rows"] == 2
        assert data[0]["orphan"] is False
        assert data[0]["langs"] == {"de": 1, "en": 1}

    def test_path_scoping(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de_a, en_a = _make_pair(tmp_path / "a")
        de_b, en_b = _make_pair(tmp_path / "b")
        _seed(cache, de_a, en_a)
        _seed(cache, de_b, en_b)

        res = cli_runner.invoke(
            watermark_list_cmd, ["--cache-dir", str(cache), str(tmp_path / "a")]
        )
        assert res.exit_code == 0
        assert str(Path("a") / "slides_intro.de.py") in res.output or "/a/" in res.output
        assert "1 pair(s)" in res.output


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_by_de_half(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        assert _count_rows(cache) == 2

        res = cli_runner.invoke(watermark_clear_cmd, ["--cache-dir", str(cache), str(de)])
        assert res.exit_code == 0
        assert "cleared 2 row(s)" in res.output
        assert _count_rows(cache) == 0

    def test_clear_by_en_twin(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        res = cli_runner.invoke(watermark_clear_cmd, ["--cache-dir", str(cache), str(en)])
        assert res.exit_code == 0
        assert _count_rows(cache) == 0

    def test_dry_run_keeps_rows(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        res = cli_runner.invoke(
            watermark_clear_cmd, ["--cache-dir", str(cache), "--dry-run", str(de)]
        )
        assert res.exit_code == 0
        assert "would clear 2 row(s)" in res.output
        assert _count_rows(cache) == 2

    def test_clear_missing_watermark_is_noop(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, _en = _make_pair(tmp_path / "decks")
        res = cli_runner.invoke(watermark_clear_cmd, ["--cache-dir", str(cache), str(de)])
        assert res.exit_code == 0
        assert "nothing to clear" in res.output

    def test_clear_directory_requires_yes(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        decks = tmp_path / "decks"
        de, en = _make_pair(decks)
        _seed(cache, de, en)
        # No --yes and declined prompt → aborts, rows survive.
        res = cli_runner.invoke(
            watermark_clear_cmd, ["--cache-dir", str(cache), str(decks)], input="n\n"
        )
        assert res.exit_code != 0
        assert _count_rows(cache) == 2

    def test_clear_directory_with_yes(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        decks = tmp_path / "decks"
        de, en = _make_pair(decks)
        de2, en2 = _make_pair(decks, stem="slides_two")
        _seed(cache, de, en)
        _seed(cache, de2, en2)
        res = cli_runner.invoke(
            watermark_clear_cmd, ["--cache-dir", str(cache), "--yes", str(decks)]
        )
        assert res.exit_code == 0
        assert _count_rows(cache) == 0

    def test_clear_directory_json_requires_yes(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        decks = tmp_path / "decks"
        de, en = _make_pair(decks)
        _seed(cache, de, en)
        res = cli_runner.invoke(
            watermark_clear_cmd, ["--cache-dir", str(cache), "--json", str(decks)]
        )
        assert res.exit_code != 0
        assert _count_rows(cache) == 2


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_prune_removes_only_orphans(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)  # live
        _seed(
            cache,
            tmp_path / "decks" / "gone.de.py",
            tmp_path / "decks" / "gone.en.py",
            langs=("de",),
        )

        res = cli_runner.invoke(watermark_prune_cmd, ["--cache-dir", str(cache)])
        assert res.exit_code == 0
        assert "pruned 1 row(s)" in res.output
        # The live pair's 2 rows remain; the orphan's 1 row is gone.
        assert _count_rows(cache) == 2

    def test_prune_dry_run(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        _seed(
            cache,
            tmp_path / "decks" / "gone.de.py",
            tmp_path / "decks" / "gone.en.py",
            langs=("de",),
        )
        res = cli_runner.invoke(watermark_prune_cmd, ["--cache-dir", str(cache), "--dry-run"])
        assert res.exit_code == 0
        assert "would prune" in res.output
        assert _count_rows(cache) == 1

    def test_prune_none(self, cli_runner, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        de, en = _make_pair(tmp_path / "decks")
        _seed(cache, de, en)
        res = cli_runner.invoke(watermark_prune_cmd, ["--cache-dir", str(cache)])
        assert res.exit_code == 0
        assert "no orphaned watermarks." in res.output
        assert _count_rows(cache) == 2
