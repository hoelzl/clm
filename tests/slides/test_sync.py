"""Tests for :mod:`clm.slides.sync`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncCache
from clm.infrastructure.llm.ollama_client import (
    StaticSyncJudge,
    SyncProposal,
    sync_key,
)
from clm.slides.sync import SyncOptions, sync_split_pair

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pair(
    tmp_path: Path,
    *,
    de: str,
    en: str,
    stem: str = "slides_intro",
) -> tuple[Path, Path]:
    de_path = tmp_path / f"{stem}.de.py"
    en_path = tmp_path / f"{stem}.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _slide_cell(*, lang: str, slide_id: str, body: str, tag: str = "slide") -> str:
    """Build a percent-format slide cell as a single string ending in newline."""
    return f'# %% [markdown] lang="{lang}" tags=["{tag}"] slide_id="{slide_id}"\n{body.strip()}\n'


# ---------------------------------------------------------------------------
# Pair walking
# ---------------------------------------------------------------------------


class TestPairWalking:
    def test_in_sync_pair_records_in_sync_outcome(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung\n#\n# - Punkt eins")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction\n#\n# - Point one")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="in_sync", proposed_text="# placeholder")
        )
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        assert result.pairs_visited == 1
        assert result.pairs_in_sync == 1
        assert result.pairs_proposed == 0
        assert result.outcomes[0].verdict == "in_sync"
        assert result.outcomes[0].slide_id == "intro"
        assert result.outcomes[0].role == "slide"

    def test_update_pair_produces_diff(self, tmp_path: Path):
        de = _slide_cell(
            lang="de",
            slide_id="intro",
            body="# ## Einleitung\n#\n# - Punkt eins\n# - Punkt zwei",
        )
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction\n#\n# - Point one")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        proposed_en = "# ## Introduction\n#\n# - Point one\n# - Point two"
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(
                verdict="update",
                proposed_text=proposed_en,
                reason="DE added a new bullet",
            )
        )
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        assert result.pairs_proposed == 1
        outcome = result.outcomes[0]
        assert outcome.verdict == "update"
        assert outcome.proposal is not None
        assert outcome.proposal.proposed_text == proposed_en
        assert outcome.diff  # non-empty unified diff
        assert "Point two" in outcome.diff
        assert outcome.reason == "DE added a new bullet"
        assert outcome.direction == "de->en"

    def test_source_lang_en_reverses_direction(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung\n#\n# - alt")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction\n#\n# - new")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(
            default_proposal=SyncProposal(
                verdict="update", proposed_text="# ## Einleitung\n#\n# - neu"
            )
        )
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="en", judge=judge))

        outcome = result.outcomes[0]
        assert outcome.direction == "en->de"
        assert outcome.proposal is not None
        assert "neu" in outcome.proposal.proposed_text

    def test_error_when_no_judge(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=None))

        assert result.pairs_visited == 1
        assert result.pairs_error == 1
        assert result.outcomes[0].verdict == "error"
        assert "no judge" in result.outcomes[0].error

    def test_judge_failure_fails_soft(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        # Static judge with no mapping and no default → raises OllamaError.
        judge = StaticSyncJudge()
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        assert result.pairs_error == 1
        assert result.outcomes[0].verdict == "error"
        assert "no static sync proposal" in result.outcomes[0].error

    def test_multiple_pairs_in_one_file(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="a", body="# ## A") + _slide_cell(
            lang="de", slide_id="b", body="# ## B"
        )
        en = _slide_cell(lang="en", slide_id="a", body="# ## A en") + _slide_cell(
            lang="en", slide_id="b", body="# ## B en"
        )
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        assert result.pairs_visited == 2
        slide_ids = {o.slide_id for o in result.outcomes}
        assert slide_ids == {"a", "b"}


# ---------------------------------------------------------------------------
# Structural issues
# ---------------------------------------------------------------------------


class TestStructuralIssues:
    def test_slide_id_only_on_de_side(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="shared", body="# ## A") + _slide_cell(
            lang="de", slide_id="de-only", body="# ## DE only"
        )
        en = _slide_cell(lang="en", slide_id="shared", body="# ## A")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        assert result.pairs_visited == 1  # only the shared slide_id paired
        assert len(result.issues) == 1
        issue = result.issues[0]
        assert issue.slide_id == "de-only"
        assert issue.severity == "warning"
        assert issue.de_count == 1
        assert issue.en_count == 0

    def test_count_mismatch_within_slide_id(self, tmp_path: Path):
        # Two voiceover cells on DE side, one on EN — structural mismatch.
        de = (
            _slide_cell(lang="de", slide_id="x", body="# ## X")
            + _slide_cell(lang="de", slide_id="x", body="# - DE narration A", tag="voiceover")
            + _slide_cell(lang="de", slide_id="x", body="# - DE narration B", tag="voiceover")
        )
        en = _slide_cell(lang="en", slide_id="x", body="# ## X") + _slide_cell(
            lang="en", slide_id="x", body="# - EN narration", tag="voiceover"
        )
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        # The "slide" role paired cleanly; the "voiceover" role didn't.
        assert any(o.role == "slide" for o in result.outcomes)
        assert not any(o.role == "voiceover" for o in result.outcomes)
        assert len(result.issues) == 1
        assert result.issues[0].slide_id == "x"
        assert result.issues[0].severity == "error"


# ---------------------------------------------------------------------------
# Cache integration
# ---------------------------------------------------------------------------


class TestCacheIntegration:
    def test_cache_hit_avoids_second_judge_call(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        cache = SyncCache(tmp_path / "clm-llm.sqlite")
        proposal = SyncProposal(verdict="update", proposed_text="# ## Introduction\n# - new")
        judge = StaticSyncJudge(default_proposal=proposal)

        try:
            result1 = sync_split_pair(
                de_path,
                en_path,
                SyncOptions(source_lang="de", judge=judge, cache=cache),
            )
            assert len(judge.calls) == 1
            assert result1.cache_hits == 0
            assert result1.pairs_proposed == 1

            result2 = sync_split_pair(
                de_path,
                en_path,
                SyncOptions(source_lang="de", judge=judge, cache=cache),
            )
            assert len(judge.calls) == 1  # not called again
            assert result2.cache_hits == 1
            assert result2.pairs_proposed == 1
            assert result2.outcomes[0].cached is True
        finally:
            cache.close()

    def test_cache_ignores_mismatched_direction(self, tmp_path: Path):
        # If the cache has a 'en->de' entry but the current options use
        # source_lang='de' (direction 'de->en'), the cache should be
        # bypassed and the judge re-fired.
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        cache = SyncCache(tmp_path / "clm-llm.sqlite")
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# updated")
        )
        try:
            # First run: source_lang=en, populates cache as 'en->de'.
            sync_split_pair(
                de_path,
                en_path,
                SyncOptions(source_lang="en", judge=judge, cache=cache),
            )
            assert len(judge.calls) == 1

            # Second run: source_lang=de, direction 'de->en' — cache entry
            # doesn't match, judge fires again.
            sync_split_pair(
                de_path,
                en_path,
                SyncOptions(source_lang="de", judge=judge, cache=cache),
            )
            assert len(judge.calls) == 2
        finally:
            cache.close()

    def test_cache_writes_proposal_on_fresh_call(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        cache = SyncCache(tmp_path / "clm-llm.sqlite")
        proposal = SyncProposal(verdict="in_sync", proposed_text="# ## Introduction")
        judge = StaticSyncJudge(default_proposal=proposal)
        try:
            sync_split_pair(
                de_path,
                en_path,
                SyncOptions(source_lang="de", judge=judge, cache=cache),
            )
            entries = cache.iter_entries()
            assert len(entries) == 1
            de_hash, en_hash, prompt_version, direction, payload, _ = entries[0]
            assert direction == "de->en"
            restored = SyncProposal.from_json(payload)
            assert restored.verdict == "in_sync"
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Role filtering
# ---------------------------------------------------------------------------


class TestRoleFiltering:
    def test_code_cells_are_skipped(self, tmp_path: Path):
        # Shared code cells should not be synced.
        de = (
            _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
            + '# %% tags=["keep"]\nx = 1\n'
        )
        en = (
            _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
            + '# %% tags=["keep"]\nx = 1\n'
        )
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        # Only the markdown slide pair shows up.
        assert result.pairs_visited == 1
        assert result.outcomes[0].role == "slide"

    def test_cells_without_slide_id_are_skipped(self, tmp_path: Path):
        # A cell with no slide_id cannot be paired — it shouldn't appear.
        de = (
            _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung")
            + '# %% [markdown] lang="de" tags=["subslide"]\n# - lonely DE bullet\n'
        )
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        # Only the intro pair visited. The lonely DE bullet has no
        # slide_id, so it doesn't even surface as an issue.
        assert result.pairs_visited == 1
        assert result.issues == []

    def test_voiceover_cells_are_synced(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## Einleitung") + _slide_cell(
            lang="de",
            slide_id="intro",
            body="# Wir beginnen mit der Einleitung.",
            tag="voiceover",
        )
        en = _slide_cell(lang="en", slide_id="intro", body="# ## Introduction") + _slide_cell(
            lang="en",
            slide_id="intro",
            body="# Let's start with the introduction.",
            tag="voiceover",
        )
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text="x"))
        result = sync_split_pair(de_path, en_path, SyncOptions(source_lang="de", judge=judge))

        roles = {o.role for o in result.outcomes}
        assert roles == {"slide", "voiceover"}


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_invalid_source_lang_raises(self, tmp_path: Path):
        de = _slide_cell(lang="de", slide_id="intro", body="# ## A")
        en = _slide_cell(lang="en", slide_id="intro", body="# ## A")
        de_path, en_path = _write_pair(tmp_path, de=de, en=en)

        with pytest.raises(ValueError, match="source_lang"):
            sync_split_pair(de_path, en_path, SyncOptions(source_lang="fr"))
