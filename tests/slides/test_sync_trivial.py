"""Tests for :mod:`clm.slides.sync_trivial`."""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncSnapshotCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.sync import SyncOptions, sync_split_pair
from clm.slides.sync_trivial import apply_trivial_proposals, is_trivial_diff

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _slide_cell(*, lang: str, slide_id: str, body: str, tag: str = "slide") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["{tag}"] slide_id="{slide_id}"\n{body.strip()}\n'


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


def _propose(text: str, *, reason: str = "") -> SyncProposal:
    return SyncProposal(verdict="update", proposed_text=text, reason=reason)


def _sync(
    tmp_path: Path,
    *,
    de_body: str,
    en_body: str,
    proposal: SyncProposal,
    source_lang: str = "de",
):
    de_cell = _slide_cell(lang="de", slide_id="intro", body=de_body)
    en_cell = _slide_cell(lang="en", slide_id="intro", body=en_body)
    de_path, en_path = _write_pair(tmp_path, de=de_cell, en=en_cell)

    judge = StaticSyncJudge(default_proposal=proposal)
    result = sync_split_pair(
        de_path,
        en_path,
        SyncOptions(source_lang=source_lang, judge=judge),
    )
    return de_path, en_path, result


# ---------------------------------------------------------------------------
# is_trivial_diff
# ---------------------------------------------------------------------------


class TestIsTrivialDiff:
    def test_identical_bodies_are_trivial(self):
        body = "# ## Intro\n# - one"
        assert is_trivial_diff(body, body) is True

    def test_crlf_vs_lf_is_trivial(self):
        target = "# ## Intro\r\n# - one"
        proposed = "# ## Intro\n# - one"
        assert is_trivial_diff(target, proposed) is True

    def test_trailing_newline_is_trivial(self):
        target = "# ## Intro\n# - one"
        proposed = "# ## Intro\n# - one\n"
        assert is_trivial_diff(target, proposed) is True

    def test_one_line_whitespace_only_is_trivial(self):
        target = "# ## Intro\n# - one\n# - two"
        # second bullet adds an internal double-space
        proposed = "# ## Intro\n# - one\n# -  two"
        assert is_trivial_diff(target, proposed) is True

    def test_one_line_leading_whitespace_change_is_trivial(self):
        target = "# ## Intro\n#  - one"  # extra space after #
        proposed = "# ## Intro\n# - one"
        assert is_trivial_diff(target, proposed) is True

    def test_one_line_content_change_is_not_trivial(self):
        target = "# ## Intro\n# - one"
        proposed = "# ## Intro\n# - One"  # capitalisation flip — non-trivial
        assert is_trivial_diff(target, proposed) is False

    def test_two_line_changes_are_not_trivial(self):
        target = "# ## Intro\n# - a\n# - b"
        proposed = "# ## Intro\n# -  a\n# -  b"
        assert is_trivial_diff(target, proposed) is False

    def test_different_line_counts_are_not_trivial(self):
        target = "# ## Intro\n# - one"
        proposed = "# ## Intro\n# - one\n# - two"
        assert is_trivial_diff(target, proposed) is False

    def test_punctuation_change_is_not_trivial(self):
        target = "# ## Intro\n# - one."
        proposed = "# ## Intro\n# - one"
        assert is_trivial_diff(target, proposed) is False


# ---------------------------------------------------------------------------
# apply_trivial_proposals
# ---------------------------------------------------------------------------


class TestApplyTrivialProposals:
    def test_trivial_proposal_writes_target_and_bumps_counter(self, tmp_path: Path):
        # EN side has an extra space inside a bullet — pure whitespace diff.
        de_path, en_path, result = _sync(
            tmp_path,
            de_body="# ## Einleitung\n# - eins",
            en_body="# ## Introduction\n# -  one",
            proposal=_propose("# ## Introduction\n# - one"),
        )

        applied = apply_trivial_proposals(result)

        assert len(applied) == 1
        assert applied[0].applied_trivially is True
        assert result.pairs_auto_applied == 1
        # File rewritten with the proposed (single-space) bullet.
        en_text = en_path.read_text(encoding="utf-8")
        assert "# - one" in en_text
        assert "# -  one" not in en_text
        # DE file untouched.
        assert "eins" in de_path.read_text(encoding="utf-8")

    def test_non_trivial_proposal_is_left_alone(self, tmp_path: Path):
        de_path, en_path, result = _sync(
            tmp_path,
            de_body="# ## Einleitung\n# - eins\n# - zwei",
            en_body="# ## Introduction\n# - one",
            proposal=_propose("# ## Introduction\n# - one\n# - two"),
        )

        applied = apply_trivial_proposals(result)

        assert applied == []
        assert result.pairs_auto_applied == 0
        # outcome's verdict stays "update" — walker can still pick it up.
        assert result.outcomes[0].verdict == "update"
        assert result.outcomes[0].applied_trivially is False
        # File unchanged: still missing "two".
        assert "two" not in en_path.read_text(encoding="utf-8")

    def test_in_sync_and_error_outcomes_are_ignored(self, tmp_path: Path):
        # In-sync proposal — no update at all.
        de_cell = _slide_cell(lang="de", slide_id="intro", body="# ## A")
        en_cell = _slide_cell(lang="en", slide_id="intro", body="# ## A")
        de_path, en_path = _write_pair(tmp_path, de=de_cell, en=en_cell)
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="in_sync", proposed_text="", reason="")
        )
        result = sync_split_pair(
            de_path,
            en_path,
            SyncOptions(source_lang="de", judge=judge),
        )

        applied = apply_trivial_proposals(result)
        assert applied == []
        assert result.pairs_auto_applied == 0

    def test_records_snapshot_when_cache_provided(self, tmp_path: Path):
        de_path, en_path, result = _sync(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction\r\n",  # CRLF that the proposal will normalise
            proposal=_propose("# ## Introduction"),
        )

        cache = SyncSnapshotCache(tmp_path / "snap.sqlite")
        try:
            apply_trivial_proposals(result, snapshot_cache=cache)
            entries = list(cache.iter_entries())
        finally:
            cache.close()

        assert result.pairs_auto_applied == 1
        assert len(entries) == 1
        de_path_s, en_path_s, slide_id, role, de_hash, en_hash, direction, _accepted_at = entries[0]
        assert slide_id == "intro"
        assert role == "slide"
        assert direction == "de->en"
        assert de_path_s == str(de_path)
        assert en_path_s == str(en_path)
        # Both hashes are non-empty hex sha256 strings.
        assert len(de_hash) == 64
        assert len(en_hash) == 64

    def test_no_snapshot_cache_still_writes_file(self, tmp_path: Path):
        de_path, en_path, result = _sync(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction ",  # trailing whitespace on one line
            proposal=_propose("# ## Introduction"),
        )

        applied = apply_trivial_proposals(result, snapshot_cache=None)
        assert len(applied) == 1
        assert "# ## Introduction" in en_path.read_text(encoding="utf-8")
        # No trailing space after the rewrite.
        assert "Introduction " not in en_path.read_text(encoding="utf-8")

    def test_double_apply_is_idempotent(self, tmp_path: Path):
        _, en_path, result = _sync(
            tmp_path,
            de_body="# ## Einleitung",
            en_body="# ## Introduction ",
            proposal=_propose("# ## Introduction"),
        )

        first = apply_trivial_proposals(result)
        second = apply_trivial_proposals(result)

        assert len(first) == 1
        assert second == []
        assert result.pairs_auto_applied == 1
