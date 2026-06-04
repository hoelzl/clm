"""Tests for :mod:`clm.slides.sync_plan` (Issue #166, Phase 1 classifier)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import (
    BaselineCell,
    CurrentCell,
    build_sync_plan,
    classify_changes,
    ordered_sync_cells,
    render_plan,
)

DE = Path("a.de.py")
EN = Path("a.en.py")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def cur(pos: int, sid: str | None, h: str, role: str = "slide", line: int = 1) -> CurrentCell:
    return CurrentCell(position=pos, slide_id=sid, role=role, content_hash=h, line_number=line)


def base(pos: int, sid: str | None, h: str, role: str = "slide") -> BaselineCell:
    return BaselineCell(position=pos, slide_id=sid, role=role, content_hash=h)


def classify(de_cur, en_cur, de_base, en_base, source: str = "watermark"):
    return classify_changes(
        de_cur, en_cur, de_base, en_base, de_path=DE, en_path=EN, baseline_source=source
    )


# ---------------------------------------------------------------------------
# Core classification (pure, no IO)
# ---------------------------------------------------------------------------


class TestInSync:
    def test_unchanged_pair_is_noop(self):
        plan = classify(
            [cur(0, "intro", "d0")],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.is_noop
        assert plan.proposals == []
        assert plan.in_sync_count == 1
        assert "already" in plan.summary()


class TestAdd:
    def test_idless_cell_on_de_is_add_de_to_en(self):
        plan = classify(
            [cur(0, "intro", "d0"), cur(1, None, "dNEW")],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("add") == 1
        add = next(p for p in plan.proposals if p.kind == "add")
        assert add.direction == "de->en"
        assert add.slide_id is None
        assert add.translation_pending is True
        assert plan.in_sync_count == 1

    def test_idless_cell_on_en_is_add_en_to_de(self):
        plan = classify(
            [cur(0, "intro", "d0")],
            [cur(0, "intro", "e0"), cur(1, None, "eNEW")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        add = next(p for p in plan.proposals if p.kind == "add")
        assert add.direction == "en->de"
        assert add.slide_id is None

    def test_idless_survives_commit_semantics(self):
        """An id-less cell is 'new' even if the baseline already lists the
        deck — i.e. it is detected by absence of an id, not by git state."""
        plan = classify(
            [cur(0, "intro", "d0"), cur(1, None, "x")],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("add") == 1

    def test_missing_counterpart_is_add(self):
        plan = classify(
            [cur(0, "solo", "d0")],
            [],
            [base(0, "solo", "d0")],
            [],
        )
        assert plan.count("add") == 1
        add = next(p for p in plan.proposals if p.kind == "add")
        assert add.direction == "de->en"
        assert add.slide_id == "solo"


class TestEdit:
    def test_edit_on_de_only(self):
        plan = classify(
            [cur(0, "intro", "dNEW")],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "dOLD")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("edit") == 1
        edit = plan.proposals[0]
        assert edit.direction == "de->en"
        assert edit.slide_id == "intro"

    def test_edit_on_en_only(self):
        plan = classify(
            [cur(0, "intro", "d0")],
            [cur(0, "intro", "eNEW")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "eOLD")],
        )
        assert plan.proposals[0].direction == "en->de"


class TestConflict:
    def test_both_edited_is_conflict(self):
        plan = classify(
            [cur(0, "intro", "dNEW")],
            [cur(0, "intro", "eNEW")],
            [base(0, "intro", "dOLD")],
            [base(0, "intro", "eOLD")],
        )
        assert plan.count("conflict") == 1
        conflict = plan.proposals[0]
        assert conflict.direction is None
        assert conflict.slide_id == "intro"

    def test_removed_de_but_edited_en_is_conflict(self):
        plan = classify(
            [],
            [cur(0, "intro", "eNEW")],
            [base(0, "intro", "dOLD")],
            [base(0, "intro", "eOLD")],
        )
        assert plan.count("conflict") == 1
        assert "removed on DE but edited on EN" in plan.proposals[0].reason


class TestRemove:
    def test_remove_de_propagates_to_en(self):
        plan = classify(
            [],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("remove") == 1
        remove = plan.proposals[0]
        assert remove.direction == "de->en"
        assert remove.slide_id == "intro"

    def test_remove_en_propagates_to_de(self):
        plan = classify(
            [cur(0, "intro", "d0")],
            [],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.proposals[0].direction == "en->de"


class TestMove:
    def test_reorder_on_de_only_is_move(self):
        # baseline order a,b,c ; DE now c,a,b ; EN unchanged.
        de_cur = [cur(0, "c", "hc"), cur(1, "a", "ha"), cur(2, "b", "hb")]
        en_cur = [cur(0, "a", "ea"), cur(1, "b", "eb"), cur(2, "c", "ec")]
        de_base = [base(0, "a", "ha"), base(1, "b", "hb"), base(2, "c", "hc")]
        en_base = [base(0, "a", "ea"), base(1, "b", "eb"), base(2, "c", "ec")]
        plan = classify(de_cur, en_cur, de_base, en_base)
        assert plan.count("move") == 1
        move = next(p for p in plan.proposals if p.kind == "move")
        assert move.slide_id == "c"
        assert move.direction == "de->en"
        assert plan.in_sync_count == 2

    def test_reorder_on_both_decks_is_order_conflict(self):
        # baseline a,b,c,d ; both decks reorder to a,c,b,d identically.
        de_cur = [cur(0, "a", "ha"), cur(1, "c", "hc"), cur(2, "b", "hb"), cur(3, "d", "hd")]
        en_cur = [cur(0, "a", "ea"), cur(1, "c", "ec"), cur(2, "b", "eb"), cur(3, "d", "ed")]
        de_base = [base(0, "a", "ha"), base(1, "b", "hb"), base(2, "c", "hc"), base(3, "d", "hd")]
        en_base = [base(0, "a", "ea"), base(1, "b", "eb"), base(2, "c", "ec"), base(3, "d", "ed")]
        plan = classify(de_cur, en_cur, de_base, en_base)
        assert plan.count("move") == 0
        assert any("order drifted on both" in i.reason for i in plan.issues)
        assert plan.in_sync_count == 4

    def test_insert_in_middle_is_not_a_spurious_move(self):
        # An id-less slide inserted between b and c shifts positions but must
        # NOT be read as a reorder of the unchanged cells.
        de_cur = [cur(0, "a", "ha"), cur(1, "b", "hb"), cur(2, None, "new"), cur(3, "c", "hc")]
        en_cur = [cur(0, "a", "ea"), cur(1, "b", "eb"), cur(2, "c", "ec")]
        de_base = [base(0, "a", "ha"), base(1, "b", "hb"), base(2, "c", "hc")]
        en_base = [base(0, "a", "ea"), base(1, "b", "eb"), base(2, "c", "ec")]
        plan = classify(de_cur, en_cur, de_base, en_base)
        assert plan.count("move") == 0
        assert plan.count("add") == 1
        assert plan.in_sync_count == 3


class TestDuplicateId:
    def test_copy_paste_is_detected_and_renamed(self):
        # The watermark identifies the original (matches the baseline hash);
        # the other duplicate is a copy → a rename proposal, not an error.
        plan = classify(
            [cur(0, "dup", "h0"), cur(1, "dup", "hNEW")],  # original h0 + edited copy
            [cur(0, "dup", "e0")],
            [base(0, "dup", "h0")],
            [base(0, "dup", "e0")],
        )
        assert not plan.has_errors
        renames = [p for p in plan.proposals if p.kind == "rename"]
        assert len(renames) == 1
        assert renames[0].slide_id == "dup"
        assert renames[0].direction == "de->en"
        assert renames[0].content_hash == "hNEW"  # the copy, not the original
        assert plan.in_sync_count == 1  # the original pairs normally

    def test_ambiguous_duplicate_both_drifted_is_error(self):
        # Neither duplicate matches the baseline → can't tell which is original.
        plan = classify(
            [cur(0, "dup", "hA"), cur(1, "dup", "hB")],
            [cur(0, "dup", "e0")],
            [base(0, "dup", "h0")],
            [base(0, "dup", "e0")],
        )
        assert plan.has_errors
        assert plan.count("rename") == 0

    def test_duplicate_with_no_baseline_is_error(self):
        # Cold start: no baseline to identify the original → error, not a guess.
        plan = classify(
            [cur(0, "dup", "h0"), cur(1, "dup", "h1")],
            [cur(0, "dup", "e0")],
            None,
            None,
            source="none",
        )
        assert plan.has_errors
        assert plan.count("rename") == 0


class TestColdStart:
    def test_shared_id_counts_as_in_sync(self):
        plan = classify(
            [cur(0, "intro", "d0")],
            [cur(0, "intro", "e0")],
            None,
            None,
            source="none",
        )
        assert plan.in_sync_count == 1
        assert plan.count("add") == 0
        assert "baseline=none" in plan.summary()

    def test_idless_add_in_cold_start(self):
        plan = classify(
            [cur(0, None, "dNEW")],
            [],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 1
        assert plan.proposals[0].slide_id is None

    def test_one_side_only_id_is_cold_add(self):
        plan = classify(
            [cur(0, "solo", "d0")],
            [],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 1
        assert "one side only" in plan.proposals[0].reason

    def test_no_baseline_summary_is_explicit(self):
        plan = classify([], [], None, None, source="none")
        assert plan.is_noop
        summary = plan.summary()
        assert "baseline=none" in summary
        assert "cannot detect" in summary


class TestBothDirectionsRefusal:
    """Adds that would flow both ways become ``refuse`` proposals (#216).

    The resolver decides at plan time that a both-directions cold-start / id-less
    pair cannot be auto-paired, so it emits ``refuse`` items instead of
    bidirectional adds the apply engine would double (id-carrying) or defer with
    an error (id-less). A one-directional case keeps its adds.
    """

    def test_cold_start_parallel_idless_refuses(self):
        plan = classify(
            [cur(0, None, "d0"), cur(1, None, "d1")],
            [cur(0, None, "e0"), cur(1, None, "e1")],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 0
        assert plan.count("refuse") == 4
        assert all(p.disposition == "refuse" for p in plan.refusals)
        assert {p.direction for p in plan.refusals} == {"de->en", "en->de"}

    def test_cold_start_mismatched_ids_refuses(self):
        plan = classify(
            [cur(0, "d1", "d0"), cur(1, "d2", "d1h")],
            [cur(0, "e1", "e0"), cur(1, "e2", "e1h")],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 0
        assert plan.count("refuse") == 4

    def test_cold_start_half_idd_refuses(self):
        plan = classify(
            [cur(0, None, "d0"), cur(1, None, "d1")],
            [cur(0, "s1", "e0"), cur(1, "s2", "e1")],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 0
        assert plan.count("refuse") == 4

    def test_cold_start_one_direction_still_adds(self):
        # New content on one side only is the legitimate single-language case —
        # never refused.
        plan = classify(
            [cur(0, None, "d0"), cur(1, None, "d1")],
            [],
            None,
            None,
            source="none",
        )
        assert plan.count("add") == 2
        assert plan.count("refuse") == 0

    def test_baseline_both_sides_idless_refuses(self):
        plan = classify(
            [cur(0, "intro", "d0"), cur(1, None, "dNEW")],
            [cur(0, "intro", "e0"), cur(1, None, "eNEW")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("add") == 0
        assert plan.count("refuse") == 2
        assert plan.in_sync_count == 1

    def test_baseline_idcarrying_both_directions_still_adds(self):
        # Against a real baseline, two new id'd slides (one per side) are genuinely
        # distinct — their ids were absent from the baseline — not a mismatched
        # pair, so they apply rather than refuse.
        plan = classify(
            [cur(0, "intro", "d0"), cur(1, "newde", "dNEW")],
            [cur(0, "intro", "e0"), cur(1, "newen", "eNEW")],
            [base(0, "intro", "d0")],
            [base(0, "intro", "e0")],
        )
        assert plan.count("add") == 2
        assert plan.count("refuse") == 0


# ---------------------------------------------------------------------------
# build_sync_plan (IO wrapper: baseline resolution)
# ---------------------------------------------------------------------------


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


class TestBuildSyncPlanWatermark:
    def test_edit_detected_against_watermark(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung\n#\n# - Punkt eins")
        en = _slide("en", "intro", "# ## Introduction\n#\n# - Point one")
        de_path, en_path = _write_pair(tmp_path, de, en)

        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author edits the DE deck (adds a bullet).
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n#\n# - Punkt eins\n# - Punkt zwei"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert plan.baseline_source == "watermark"
        assert plan.count("edit") == 1
        assert plan.proposals[0].direction == "de->en"

    def test_idless_add_detected_against_watermark(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)

        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author appends a brand-new, id-less slide on the DE side.
            de_path.write_text(
                de + _slide_idless("de", "# ## Neues Thema"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert plan.baseline_source == "watermark"
        assert plan.count("add") == 1
        assert plan.proposals[0].slide_id is None
        assert plan.proposals[0].direction == "de->en"


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


class TestBuildSyncPlanNoBaseline:
    def test_no_watermark_no_git_is_baseline_none(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung") + _slide_idless("de", "# ## Neu")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)

        plan = build_sync_plan(de_path, en_path, allow_git_fallback=False)

        assert plan.baseline_source == "none"
        # id-less add is still found; intro pairs as in-sync.
        assert plan.count("add") == 1
        assert plan.in_sync_count == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestBuildSyncPlanGitFallback:
    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)

    def test_git_head_used_when_no_watermark(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)

        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "config", "user.email", "t@example.com")
        self._git(tmp_path, "config", "user.name", "Test")
        self._git(tmp_path, "add", "-A")
        self._git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")

        # After committing the baseline, append a new id-less slide on DE.
        de_path.write_text(de + _slide_idless("de", "# ## Neues Thema"), encoding="utf-8")

        plan = build_sync_plan(de_path, en_path)  # no watermark cache → git HEAD

        assert plan.baseline_source == "git-head"
        assert plan.count("add") == 1
        assert plan.proposals[0].slide_id is None

    def test_commit_after_edit_still_detects_idless_add(self, tmp_path: Path):
        """The named failure mode: committing the edited deck before syncing
        does not hide the add — id-less-ness is git-immune."""
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)

        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "config", "user.email", "t@example.com")
        self._git(tmp_path, "config", "user.name", "Test")
        self._git(tmp_path, "add", "-A")
        self._git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")

        # Author adds an id-less slide AND commits before syncing.
        de_path.write_text(de + _slide_idless("de", "# ## Neues Thema"), encoding="utf-8")
        self._git(tmp_path, "add", "-A")
        self._git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "added slide")

        plan = build_sync_plan(de_path, en_path)

        # HEAD now contains the add, so git-diff-HEAD would miss it — but the
        # id-less marker still flags it.
        assert plan.count("add") == 1
        assert plan.proposals[0].slide_id is None

    def _commit_pair(self, tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
        de_path, en_path = _write_pair(tmp_path, de, en)
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "config", "user.email", "t@example.com")
        self._git(tmp_path, "config", "user.name", "Test")
        self._git(tmp_path, "add", "-A")
        self._git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
        return de_path, en_path

    def test_committed_idless_pair_mints_with_provider(self, tmp_path: Path):
        # Issue #225: a COMMITTED never-id'd pair resolves to a git-HEAD baseline that
        # carries no ids — functionally a cold start. It must bootstrap (mint), not
        # refuse: the baseline is demoted to "none" so the cold mint candidacy fires.
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B"),
            _slide_idless("en", "# ## A") + _slide_idless("en", "# ## B"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.baseline_source == "none"  # demoted from git-head (#225)
        assert plan.count("mint") == 1
        assert plan.count("add") == 0
        assert plan.count("refuse") == 0

    def test_committed_idless_pair_refuses_without_provider(self, tmp_path: Path):
        # No provider → no verifier → it must REFUSE (never silently add/double).
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B"),
            _slide_idless("en", "# ## A") + _slide_idless("en", "# ## B"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=False)
        assert plan.baseline_source == "none"
        assert plan.count("refuse") == 4
        assert plan.count("add") == 0
        assert plan.count("mint") == 0

    def test_committed_half_idd_pair_adopts_with_provider(self, tmp_path: Path):
        # A COMMITTED half-id'd pair (DE id-less, EN id'd) bootstraps via adopt — the
        # id-less DE half adopts EN's existing ids — instead of the keyed baseline path
        # that would translate-and-insert both directions and double the deck.
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B"),
            _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.baseline_source == "none"
        assert plan.count("adopt") == 1
        assert plan.count("add") == 0
        assert plan.count("refuse") == 0

    def test_committed_half_idd_pair_never_doubles_without_provider(self, tmp_path: Path):
        # REGRESSION GUARD for the silent-doubling bug uncovered alongside #225: a
        # committed half-id'd pair used to emit 4 ADDS on the git-HEAD baseline path
        # (id-less de->en + id'd en->de) → translate-insert both → DOUBLE both decks.
        # With the demotion it refuses instead (add == 0), even with no provider.
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B"),
            _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=False)
        assert plan.count("add") == 0  # the doubling adds are gone
        assert plan.count("refuse") == 4
        assert plan.count("mint") == 0
        assert plan.count("adopt") == 0

    def test_committed_mismatched_id_pair_refuses_never_doubles(self, tmp_path: Path):
        # Both halves committed but id'd with DISJOINT ids (per-half assign-ids): the
        # ids do not pair across decks, so the git-HEAD baseline keys are useless and
        # the keyed path used to emit 4 id-carrying adds (both directions) → DOUBLE.
        # The pair shares no keying → demoted to cold → refused (mismatched stays
        # refuse), with or without a provider.
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide("de", "d1", "# ## A") + _slide("de", "d2", "# ## B"),
            _slide("en", "e1", "# ## A") + _slide("en", "e2", "# ## B"),
        )
        for provider in (True, False):
            plan = build_sync_plan(de_path, en_path, provider_available=provider)
            assert plan.baseline_source == "none"  # demoted: no shared keying
            assert plan.count("add") == 0  # the doubling adds are gone
            assert plan.count("refuse") == 4
            assert plan.count("mint") == 0
            assert plan.count("adopt") == 0

    def test_committed_idd_pair_edit_still_uses_git_head(self, tmp_path: Path):
        # REGRESSION: a fully-id'd committed pair keeps its real git-HEAD baseline —
        # an edit is detected as an edit, never demoted to a cold-start bootstrap.
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide("de", "s1", "# ## A") + _slide("de", "s2", "# ## B"),
            _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B"),
        )
        de_path.write_text(
            _slide("de", "s1", "# ## A EDITED") + _slide("de", "s2", "# ## B"), encoding="utf-8"
        )
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.baseline_source == "git-head"  # NOT demoted
        assert plan.count("edit") == 1
        assert plan.count("mint") == 0
        assert plan.count("adopt") == 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestRender:
    def test_render_lists_proposals_and_summary(self):
        plan = classify(
            [cur(0, "intro", "dNEW"), cur(1, None, "x")],
            [cur(0, "intro", "e0")],
            [base(0, "intro", "dOLD")],
            [base(0, "intro", "e0")],
        )
        text = render_plan(plan)
        assert "edit de->en intro/slide" in text
        assert "translation pending" in text
        assert "baseline=watermark" in text
