"""Tests for :mod:`clm.slides.sync_direction` (direction auto-detection)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncSnapshotCache
from clm.slides.sync_direction import infer_source_lang
from clm.slides.sync_writeback import cell_content_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slide_cell(*, lang: str, slide_id: str, body: str, tag: str = "slide") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["{tag}"] slide_id="{slide_id}"\n{body.strip()}\n'


def _write_pair(
    tmp_path: Path,
    *,
    de_body: str,
    en_body: str,
    slide_id: str = "intro",
    stem: str = "slides_intro",
) -> tuple[Path, Path]:
    de = _slide_cell(lang="de", slide_id=slide_id, body=de_body)
    en = _slide_cell(lang="en", slide_id=slide_id, body=en_body)
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside ``repo`` and return stdout (or raise)."""
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def _init_repo(repo: Path) -> None:
    """Create a fresh git repo with deterministic author config."""
    _git(repo, "init", "-q")
    # Avoid relying on the developer's global config (some environments
    # have neither user.name nor user.email).
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, message: str, *paths: Path) -> None:
    rels = [str(p.relative_to(repo)) for p in paths]
    _git(repo, "add", "--", *rels)
    _git(repo, "commit", "-q", "-m", message)


# ---------------------------------------------------------------------------
# Snapshot-based inference
# ---------------------------------------------------------------------------


class TestSnapshotInference:
    def test_en_side_drifted_returns_en_as_source(self, tmp_path: Path):
        """Snapshot hash matches current DE but not current EN → EN drifted → source=en."""
        de_path, en_path = _write_pair(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction (updated)",
        )
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            # Snapshot row: DE hash matches current; EN hash captured a
            # PRIOR EN body (so current EN differs).
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung"),
                en_hash=cell_content_hash("# ## Introduction"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang == "en"
            assert inference.signal == "snapshot"
            assert "EN drifted" in inference.reason
        finally:
            cache.close()

    def test_de_side_drifted_returns_de_as_source(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            de_body="# ## Einleitung (geändert)",
            en_body="# ## Introduction",
        )
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung"),
                en_hash=cell_content_hash("# ## Introduction"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang == "de"
            assert inference.signal == "snapshot"
        finally:
            cache.close()

    def test_both_sides_in_sync_returns_no_signal(self, tmp_path: Path):
        """Snapshot rows that all match current state give no direction signal."""
        de_path, en_path = _write_pair(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction",
        )
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung"),
                en_hash=cell_content_hash("# ## Introduction"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            # Snapshot fell through to "none" — caller may still fall to git.
            assert inference.source_lang is None
            # The reason mentions the snapshot path was tried.
            assert "snapshot" in inference.reason
        finally:
            cache.close()

    def test_both_sides_drifted_is_ambiguous(self, tmp_path: Path):
        """A 3-way merge case (both sides drifted) bails to manual."""
        de_path, en_path = _write_pair(
            tmp_path,
            de_body="# ## Einleitung (geändert)",
            en_body="# ## Introduction (changed)",
        )
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung"),
                en_hash=cell_content_hash("# ## Introduction"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang is None
            assert inference.signal == "snapshot"
            assert "both sides drifted" in inference.reason
        finally:
            cache.close()

    def test_no_snapshot_cache_falls_through(self, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A")
        inference = infer_source_lang(de_path, en_path, snapshot_cache=None)
        # No snapshot AND no git → "none".
        assert inference.source_lang is None
        assert inference.signal == "none"

    def test_disagreeing_rows_are_ambiguous(self, tmp_path: Path):
        """Two snapshot rows pointing at different sides → ambiguous."""
        de = _slide_cell(lang="de", slide_id="a", body="# ## A geändert") + _slide_cell(
            lang="de", slide_id="b", body="# ## B"
        )
        en = _slide_cell(lang="en", slide_id="a", body="# ## A") + _slide_cell(
            lang="en", slide_id="b", body="# ## B changed"
        )
        de_path = tmp_path / "x.de.py"
        en_path = tmp_path / "x.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            # Row for 'a': original was "# ## A" on both sides → DE drifted.
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="a",
                role="slide",
                de_hash=cell_content_hash("# ## A"),
                en_hash=cell_content_hash("# ## A"),
                direction="de->en",
            )
            # Row for 'b': original was "# ## B" on both sides → EN drifted.
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="b",
                role="slide",
                de_hash=cell_content_hash("# ## B"),
                en_hash=cell_content_hash("# ## B"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang is None
            assert inference.signal == "snapshot"
            assert "disagree" in inference.reason
        finally:
            cache.close()

    def test_rows_for_a_different_file_pair_are_ignored(self, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A changed")
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            # Put a row for a completely different file pair.
            cache.put(
                de_path=str(tmp_path / "other.de.py"),
                en_path=str(tmp_path / "other.en.py"),
                slide_id="intro",
                role="slide",
                de_hash="aaaa",
                en_hash="bbbb",
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            # No matching rows for THIS pair → no snapshot evidence.
            assert inference.source_lang is None
            assert "no snapshot rows for this pair" in inference.reason
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Git-timestamp inference
# ---------------------------------------------------------------------------


# Real-`git` repos (init/commit for timestamp inference); ~3s/test. Runs in CI's
# integration step, excluded from the per-commit fast suite.
@pytest.mark.integration
class TestGitTimestampInference:
    def test_more_recent_commit_wins(self, tmp_path: Path):
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A old", en_body="# ## A old")
        _commit(tmp_path, "initial pair", de_path, en_path)

        # Wait so the next commit's timestamp is strictly greater.
        time.sleep(1.1)

        # Touch EN only and commit — EN becomes the newer side.
        en_path.write_text(
            _slide_cell(lang="en", slide_id="intro", body="# ## A new"),
            encoding="utf-8",
        )
        _commit(tmp_path, "edit en", en_path)

        inference = infer_source_lang(de_path, en_path, snapshot_cache=None)
        assert inference.source_lang == "en"
        assert inference.signal == "git-timestamp"

    def test_equal_timestamps_falls_back(self, tmp_path: Path):
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A")
        # Single commit touches both files → identical %ct on both.
        _commit(tmp_path, "initial pair", de_path, en_path)

        inference = infer_source_lang(de_path, en_path, snapshot_cache=None)
        assert inference.source_lang is None
        assert inference.signal == "none"
        assert "equal" in inference.reason

    def test_untracked_file_falls_back(self, tmp_path: Path):
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A")
        # Track only DE; EN remains untracked.
        _commit(tmp_path, "de only", de_path)

        inference = infer_source_lang(de_path, en_path, snapshot_cache=None)
        assert inference.source_lang is None
        # Reason mentions which file is untracked.
        assert en_path.name in inference.reason

    def test_no_git_repo_falls_back(self, tmp_path: Path):
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A")
        # tmp_path is NOT a git repo.
        inference = infer_source_lang(de_path, en_path, snapshot_cache=None)
        assert inference.source_lang is None
        assert inference.signal == "none"


# ---------------------------------------------------------------------------
# Snapshot/git interaction
# ---------------------------------------------------------------------------


# Real-`git` repos (snapshot-vs-git timestamp interaction); ~3s/test. Runs in
# CI's integration step, excluded from the per-commit fast suite.
@pytest.mark.integration
class TestSnapshotGitInteraction:
    def test_snapshot_wins_over_agreeing_git(self, tmp_path: Path):
        """When both signals point at the same side, snapshot is named in the result."""
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(
            tmp_path, de_body="# ## Einleitung", en_body="# ## Introduction"
        )
        _commit(tmp_path, "initial pair", de_path, en_path)

        # Mutate EN on disk + a snapshot that captured the old state.
        time.sleep(1.1)
        new_en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction (updated)")
        en_path.write_text(new_en, encoding="utf-8")
        _commit(tmp_path, "edit en", en_path)

        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung"),
                en_hash=cell_content_hash("# ## Introduction"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang == "en"
            # Snapshot's verdict takes precedence in the signal label even
            # though git agrees.
            assert inference.signal == "snapshot"
        finally:
            cache.close()

    def test_snapshot_and_git_disagree_is_ambiguous(self, tmp_path: Path):
        """Snapshot says one side, git says the other — fall back."""
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(
            tmp_path, de_body="# ## Einleitung", en_body="# ## Introduction"
        )
        _commit(tmp_path, "initial pair", de_path, en_path)

        # Mutate DE on disk and commit; git timestamp now points at DE.
        time.sleep(1.1)
        de_path.write_text(
            _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung (geändert)"),
            encoding="utf-8",
        )
        _commit(tmp_path, "edit de", de_path)

        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            # But the snapshot row says EN drifted (current DE matches
            # snapshot.de_hash AFTER our edit — fake by setting the
            # snapshot.de_hash to the CURRENT DE content, and
            # snapshot.en_hash to a fake "old" value so EN looks drifted).
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role="slide",
                de_hash=cell_content_hash("# ## Einleitung (geändert)"),  # matches current DE
                en_hash=cell_content_hash("# ## SOMETHING ELSE"),  # mismatches current EN
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            # Snapshot says EN, git says DE → ambiguous.
            assert inference.source_lang is None
            assert inference.signal == "snapshot+git-timestamp"
            assert "disagree" in inference.reason or "but git timestamps" in inference.reason
        finally:
            cache.close()

    def test_git_used_when_snapshot_has_no_rows(self, tmp_path: Path):
        _init_repo(tmp_path)
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A old", en_body="# ## A old")
        _commit(tmp_path, "initial pair", de_path, en_path)

        time.sleep(1.1)
        de_path.write_text(
            _slide_cell(lang="de", slide_id="intro", body="# ## A new"),
            encoding="utf-8",
        )
        _commit(tmp_path, "edit de", de_path)

        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang == "de"
            assert inference.signal == "git-timestamp"
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_snapshot_row_for_missing_slide_id_is_skipped(self, tmp_path: Path):
        """A snapshot row whose slide_id has been removed from the file should not crash."""
        de_path, en_path = _write_pair(tmp_path, de_body="# ## A", en_body="# ## A")
        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="vanished",
                role="slide",
                de_hash="aaa",
                en_hash="bbb",
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            # No matched rows → "none" — caller falls to git or asks.
            assert inference.source_lang is None
            assert "no longer in the files" in inference.reason
        finally:
            cache.close()

    @pytest.mark.parametrize(
        "role_tag",
        ["slide", "subslide", "voiceover", "notes"],
    )
    def test_all_sync_roles_index_correctly(self, tmp_path: Path, role_tag: str):
        """Direction inference must walk the same roles the sync engine does."""
        de = _slide_cell(lang="de", slide_id="intro", body="# narrative DE", tag=role_tag)
        en = _slide_cell(lang="en", slide_id="intro", body="# narrative EN changed", tag=role_tag)
        de_path = tmp_path / "x.de.py"
        en_path = tmp_path / "x.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")

        cache = SyncSnapshotCache(tmp_path / "clm-llm.sqlite")
        try:
            cache.put(
                de_path=str(de_path),
                en_path=str(en_path),
                slide_id="intro",
                role=role_tag,
                de_hash=cell_content_hash("# narrative DE"),
                en_hash=cell_content_hash("# narrative EN"),
                direction="de->en",
            )
            inference = infer_source_lang(de_path, en_path, cache)
            assert inference.source_lang == "en"
        finally:
            cache.close()
