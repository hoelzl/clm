"""Tests for sync-watermark key canonicalization across git worktrees (issue #435).

The watermark is keyed by the absolute ``(de_path, en_path)`` strings. From a
linked git worktree ``Path.resolve()`` yields the worktree path, which used to
miss the rows recorded from the main checkout (→ silent cold-start to git HEAD).
``SyncWatermarkCache`` now canonicalizes the key to the main-checkout path via
:func:`to_main_worktree_path`, so a worktree and the main checkout share both
the cache file and the keys inside it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm import cache as cache_mod
from clm.infrastructure.llm.cache import (
    SyncWatermarkCache,
    to_main_worktree_path,
)


@pytest.fixture
def git_available():
    if shutil.which("git") is None:  # pragma: no cover - CI always has git
        pytest.skip("git not available")


@pytest.fixture(autouse=True)
def _clear_remap_cache():
    """The worktree remap is memoized per process; clear it so each test's
    freshly-created repo/worktree is resolved afresh."""
    cache_mod._worktree_remap_for_dir.cache_clear()
    yield
    cache_mod._worktree_remap_for_dir.cache_clear()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "slides").mkdir()
    (path / "slides" / "x.de.py").write_text("# de\n", encoding="utf-8")
    (path / "slides" / "x.en.py").write_text("# en\n", encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "init", cwd=path)


_CELLS = [(0, "s1", "code", "h0", None), (1, None, "markdown", "h1", None)]


class TestToMainWorktreePath:
    def test_main_checkout_path_unchanged(self, tmp_path: Path, git_available):
        repo = tmp_path / "repo"
        _init_repo(repo)
        p = repo / "slides" / "x.de.py"
        assert to_main_worktree_path(p) == p

    def test_worktree_path_remaps_to_main(self, tmp_path: Path, git_available):
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "wts" / "feature"
        _git("worktree", "add", str(wt), "-b", "feature", cwd=repo)
        wt_file = wt / "slides" / "x.de.py"
        main_file = repo / "slides" / "x.de.py"
        assert to_main_worktree_path(wt_file).resolve() == main_file.resolve()

    def test_outside_repo_unchanged(self, tmp_path: Path):
        p = tmp_path / "loose" / "x.de.py"
        p.parent.mkdir(parents=True)
        p.write_text("# de\n", encoding="utf-8")
        assert to_main_worktree_path(p) == p


class TestWatermarkAcrossWorktree:
    def _record(self, db_path: Path, de: Path, en: Path) -> None:
        cache = SyncWatermarkCache(db_path)
        try:
            cache.put_deck(de_path=str(de), en_path=str(en), lang="de", cells=_CELLS)
            cache.put_deck(de_path=str(de), en_path=str(en), lang="en", cells=_CELLS)
            cache.set_synced_commit(str(de), str(en), "deadbeef")
        finally:
            cache.close()

    def test_record_in_main_read_in_worktree(self, tmp_path: Path, git_available):
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "wts" / "feature"
        _git("worktree", "add", str(wt), "-b", "feature", cwd=repo)
        db = tmp_path / "shared.sqlite"  # the shared cache file (#374 anchoring)

        main_de = (repo / "slides" / "x.de.py").resolve()
        main_en = (repo / "slides" / "x.en.py").resolve()
        self._record(db, main_de, main_en)

        # Read using the WORKTREE-resolved paths — the pre-#435 bug missed here.
        wt_de = (wt / "slides" / "x.de.py").resolve()
        wt_en = (wt / "slides" / "x.en.py").resolve()
        cache = SyncWatermarkCache(db)
        try:
            assert cache.has_pair(str(wt_de), str(wt_en)) is True
            assert cache.get_deck(str(wt_de), str(wt_en), "de") == _CELLS
            assert cache.get_synced_commit(str(wt_de), str(wt_en)) == "deadbeef"
        finally:
            cache.close()

    def test_write_in_worktree_keys_on_main(self, tmp_path: Path, git_available):
        repo = tmp_path / "repo"
        _init_repo(repo)
        wt = tmp_path / "wts" / "feature"
        _git("worktree", "add", str(wt), "-b", "feature", cwd=repo)
        db = tmp_path / "shared.sqlite"

        # Write from the WORKTREE; the row must land on the canonical (main) key,
        # so exactly ONE pair exists and the main checkout finds it.
        wt_de = (wt / "slides" / "x.de.py").resolve()
        wt_en = (wt / "slides" / "x.en.py").resolve()
        self._record(db, wt_de, wt_en)

        cache = SyncWatermarkCache(db)
        try:
            main_de = (repo / "slides" / "x.de.py").resolve()
            main_en = (repo / "slides" / "x.en.py").resolve()
            assert cache.has_pair(str(main_de), str(main_en)) is True
            # Exactly one stored pair, keyed by the MAIN path (no worktree dup).
            keyed = {(de, en) for (de, en, *_rest) in cache.iter_entries()}
            assert len(keyed) == 1
            (stored_de, stored_en) = next(iter(keyed))
            assert "worktrees" not in stored_de
            assert Path(stored_de).resolve() == main_de
        finally:
            cache.close()
