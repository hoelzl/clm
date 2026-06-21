"""Reproduction / characterization tests for issues #364, #365, #366.

These three issues all stem from one incident: a split deck whose two halves were
edited and committed twice *without* an intervening ``clm slides sync``, so the
structural watermark fell behind. A later sync then errored against the stale
baseline with an opaque ``id-less localized cells ... edited on both decks`` message,
even though the halves were mutually consistent at git HEAD.

This module pins down what master does for each issue, so we can see which concerns
recent work (the #363 watermark CLI, the ``--baseline`` / ``--rebaseline`` /
``synced_commit`` staleness work) addressed and what these PRs add:

- ``TestBothSidedIdlessDriftDegradesToConflict`` (#365, FIXED increment 1) — a both-sided
  edit to *one* hash-only id-less localized cell that pairs positionally degrades to a
  per-cell, deferred conflict (the deck no longer rolls back); a genuine both-sided edit
  stays defer-only even under a de-wins decision.
- ``TestOneSidedIdlessWinnerResolves`` (#365, FIXED increment 2) — when paired cells each
  drifted on exactly one side (a clear winner per cell), sync translates the winning edit
  onto its positional twin instead of deferring, and a fully-resolved pass advances the
  watermark.
- ``TestBothSidedMarkdownDowngradesToInSync`` (#365, increment 2) — a both-edited
  *markdown* cell the judge finds already equivalent downgrades to in_sync (no write, no
  defer); a divergent one, and any both-sided *code* cell (no judge for runnable code),
  still defers.
- ``TestIdlessDriftErrorIsLocalized`` (#364 item 4, FIXED) — when the structure is
  UNPAIRABLE (so #365 cannot degrade), the deck-wide error now pins to the drifted
  cell's owning slide group, echoes the offending cell, and steers to ``--rebaseline``.

The reproduction harness mirrors ``tests/slides/test_sync_issue_269.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.sync_apply import DECISION_DE_WINS, _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


# ---------------------------------------------------------------------------
# Deck builders (a valid split pair)
# ---------------------------------------------------------------------------


def _title(lang: str, sid: str = "title", txt: str = "T") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _idless_code(lang: str, body: str) -> str:
    """A hash-only id-less localized code cell — no slide_id, no nameable construct."""
    return f'# %% lang="{lang}"\n{body}\n'


def _idless_md(lang: str, body: str) -> str:
    """A hash-only id-less localized markdown cell — no slide_id, no narrative role."""
    return f'# %% [markdown] lang="{lang}"\n# {body}\n'


def _deck(*parts: str) -> str:
    return "\n".join(parts)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _sync(tmp: Path, baseline: str, de0: str, en0: str, de1: str, en1: str, *, judge=None):
    """Establish a baseline, apply the edits, return ``(plan, result, de_after, en_after)``."""
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    if baseline == "git-head":
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "Test")
        _git(tmp, "add", "-A")
        _git(tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    else:
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(
            plan,
            judge=judge,
            translator=StaticSlideTranslator(default="<<XL>>"),
            watermark_cache=wm,
        )
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


def _in_sync_judge() -> StaticSyncJudge:
    """A judge that always declares the two halves already equivalent (no edit needed)."""
    return StaticSyncJudge(default_proposal=SyncProposal(verdict="in_sync", proposed_text=""))


def _update_judge(text: str) -> StaticSyncJudge:
    """A judge that always proposes ``text`` as the reconciled target body."""
    return StaticSyncJudge(default_proposal=SyncProposal(verdict="update", proposed_text=text))


def _error_issues(plan):
    return [i for i in plan.issues if i.severity == "error"]


# The reproduction of the incident shape: two hash-only id-less localized code cells
# whose DE/EN text legitimately differs (test_queries vs comparison_queries — the real
# `Abfrage` vs `Query` divergence), edited on BOTH halves with no other change to
# establish a direction.
DE0 = _deck(_title("de"), _idless_code("de", "for q in test_queries:\n    run(q)"))
EN0 = _deck(_title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)"))
DE1 = _deck(_title("de"), _idless_code("de", "for q in test_queries:\n    run(q)  # DE-edit"))
EN1 = _deck(_title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)  # EN-edit"))


# ---------------------------------------------------------------------------
# #365 — both-sided id-less-localized drift degrades to a deferred conflict
# (when the cells pair positionally) instead of a whole-deck error.
# ---------------------------------------------------------------------------


class TestBothSidedIdlessDriftDegradesToConflict:
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_both_sided_idless_edit_becomes_a_positional_conflict(
        self, tmp_path: Path, baseline: str
    ):
        # Issue #365: when the halves' id-less localized cells pair positionally, a
        # both-sided edit degrades to a per-cell conflict — NOT a whole-deck error.
        plan, result, de_after, en_after = _sync(tmp_path, baseline, DE0, EN0, DE1, EN1)
        assert any(p.kind == "conflict" for p in plan.proposals)
        assert not _error_issues(plan)
        # The conflict is deferred: watermark held, both edits survive on disk, and
        # the run did not roll the whole deck back (no error).
        assert not result.watermark_recorded
        assert result.deferred >= 1
        assert "# DE-edit" in de_after
        assert "# EN-edit" in en_after

    def test_conflict_is_positionally_identified(self, tmp_path: Path):
        plan, _result, _de_after, _en_after = _sync(tmp_path, "watermark", DE0, EN0, DE1, EN1)
        conflicts = [p for p in plan.proposals if p.kind == "conflict"]
        assert conflicts
        c = conflicts[0]
        # id-less: no slide_id, the synthetic localized role, owning group recorded,
        # and the offending cell named in the reason.
        assert c.slide_id is None
        assert c.role in ("localized-code", "localized-markdown")
        assert c.owning_slide_id == "title"
        assert "test_queries" in c.reason or "comparison_queries" in c.reason

    def test_conflict_is_defer_only_even_under_a_decision(self, tmp_path: Path):
        # Issue #365 increment 1: positional resolution is not implemented yet, so an
        # id-less localized conflict defers regardless of a de-wins decision — it must
        # never mis-target a cell via the (slide_id, role) path, and both edits stay.
        db = tmp_path / "clm-llm.sqlite"
        de_path, en_path = tmp_path / "deck.de.py", tmp_path / "deck.en.py"
        de_path.write_text(DE0, encoding="utf-8")
        en_path.write_text(EN0, encoding="utf-8")
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
        de_path.write_text(DE1, encoding="utf-8")
        en_path.write_text(EN1, encoding="utf-8")
        wm = SyncWatermarkCache(db)
        try:
            plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
            conflicts = [p for p in plan.proposals if p.kind == "conflict"]
            assert conflicts
            decisions = {id(p): DECISION_DE_WINS for p in conflicts}
            result = apply_plan(
                plan,
                judge=None,
                translator=StaticSlideTranslator(default="<<XL>>"),
                watermark_cache=wm,
                decisions=decisions,
            )
        finally:
            wm.close()
        assert not result.errors
        assert result.deferred >= 1
        assert not result.watermark_recorded
        assert "# DE-edit" in de_path.read_text(encoding="utf-8")
        assert "# EN-edit" in en_path.read_text(encoding="utf-8")

    def test_unpairable_structure_still_errors(self, tmp_path: Path):
        # When the halves' id-less localized structure is NOT parallel (here DE has an
        # extra id-less cell EN lacks), positional pairing is unsound, so the located
        # error (Issue #364 item 4) is kept rather than a mispaired conflict.
        de0 = _deck(_title("de"), _idless_code("de", "for q in test_queries:\n    run(q)"))
        en0 = _deck(_title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)"))
        de1 = _deck(
            _title("de"),
            _idless_code("de", "for q in test_queries:\n    run(q)  # DE-edit"),
            _idless_code("de", "extra_de()"),
        )
        en1 = _deck(
            _title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)  # EN")
        )
        plan, result, _de_after, _en_after = _sync(tmp_path, "watermark", de0, en0, de1, en1)
        assert _error_issues(plan) or result.errors
        assert not result.watermark_recorded


# ---------------------------------------------------------------------------
# #364 item 4 — when the structure is UNPAIRABLE (so #365 cannot degrade to a
# conflict), the deck-wide error is still localized to a cell (FIXED in #417).
# ---------------------------------------------------------------------------

# DE grew an extra id-less cell EN lacks: the (group, kind) structure diverges, so
# positional pairing is unsound and the located error path stays in force.
DE0_U = _deck(_title("de"), _idless_code("de", "for q in test_queries:\n    run(q)"))
EN0_U = _deck(_title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)"))
DE1_U = _deck(
    _title("de"),
    _idless_code("de", "for q in test_queries:\n    run(q)  # DE-edit"),
    _idless_code("de", "extra_de()"),
)
EN1_U = _deck(
    _title("en"), _idless_code("en", "for q in comparison_queries:\n    run(q)  # EN-edit")
)


class TestIdlessDriftErrorIsLocalized:
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_error_names_owning_group_and_offending_cell(self, tmp_path: Path, baseline: str):
        plan, _result, _de_after, _en_after = _sync(tmp_path, baseline, DE0_U, EN0_U, DE1_U, EN1_U)
        errors = _error_issues(plan)
        assert errors
        # Located: the error pins to the drifted cell's owning slide group (was always
        # None) AND echoes the offending cell's first line so the author can find it.
        assert any(e.slide_id == "title" for e in errors)
        assert any("test_queries" in e.reason or "comparison_queries" in e.reason for e in errors)

    def test_error_steers_to_rebaseline_not_just_assign_ids(self, tmp_path: Path):
        # #364 item 3: the old message only said "assign slide_ids" (which does not help
        # the stale-watermark case). The new message leads with the actual common fix.
        plan, _result, _de_after, _en_after = _sync(
            tmp_path, "watermark", DE0_U, EN0_U, DE1_U, EN1_U
        )
        reason = "\n".join(e.reason for e in _error_issues(plan))
        assert "--rebaseline" in reason


# ---------------------------------------------------------------------------
# #365 increment 2 — resolve a side: a per-cell *one-sided* winner inside a
# both-sided deck drift is translated onto its positional twin (not just
# deferred), and a both-sided *markdown* false conflict the judge finds already
# equivalent downgrades to in_sync. Both let the watermark advance.
# ---------------------------------------------------------------------------


# Two id-less code cells; DE edits ONLY the first, EN edits ONLY the second. Each
# half's stream drifts (so the deck-level both-sided branch fires) yet every cell
# drifted on exactly one side — a clear per-cell winner.
ONE_DE0 = _deck(_title("de"), _idless_code("de", "a = 1"), _idless_code("de", "b = 2"))
ONE_EN0 = _deck(_title("en"), _idless_code("en", "a = 1"), _idless_code("en", "b = 2"))
ONE_DE1 = _deck(_title("de"), _idless_code("de", "a = 1  # DE"), _idless_code("de", "b = 2"))
ONE_EN1 = _deck(_title("en"), _idless_code("en", "a = 1"), _idless_code("en", "b = 2  # EN"))


class TestOneSidedIdlessWinnerResolves:
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_one_sided_cells_resolve_as_edits_not_conflicts(self, tmp_path: Path, baseline: str):
        plan, result, de_after, en_after = _sync(
            tmp_path, baseline, ONE_DE0, ONE_EN0, ONE_DE1, ONE_EN1
        )
        # No deck-wide error and NO conflict: each cell had a clear winner, so the
        # classifier emits resolvable id-less localized edits instead of deferrals.
        assert not _error_issues(plan)
        assert not any(p.kind == "conflict" for p in plan.proposals)
        edits = [p for p in plan.proposals if p.kind == "edit" and p.role == "localized-code"]
        assert len(edits) == 2
        assert {p.direction for p in edits} == {"de->en", "en->de"}
        # Both edits applied, nothing deferred.
        assert result.applied_edit >= 2
        assert result.deferred == 0
        # The winning side's edit survives; the losing twin is overwritten by the
        # static translation of the winner ("<<XL>>").
        assert "# DE" in de_after  # DE's edit to cell 0 stays
        assert "# EN" in en_after  # EN's edit to cell 1 stays
        assert "<<XL>>" in en_after  # cell 0 EN twin re-translated from DE
        assert "<<XL>>" in de_after  # cell 1 DE twin re-translated from EN

    def test_fully_resolved_pass_advances_the_watermark(self, tmp_path: Path):
        # Every drifted cell resolved (no deferral) → a clean pass → full advance, so a
        # re-run sees no drift (the stale-baseline incident is fully reconciled).
        _plan, result, _de, _en = _sync(tmp_path, "watermark", ONE_DE0, ONE_EN0, ONE_DE1, ONE_EN1)
        assert result.watermark_recorded
        assert not result.errors

    def test_one_sided_markdown_winner_uses_the_judge(self, tmp_path: Path):
        # A markdown one-sided winner reconciles through the judge (not the code
        # translator): DE edits cell 0, EN edits cell 1; the judge proposes the target.
        de0 = _deck(_title("de"), _idless_md("de", "Eins"), _idless_md("de", "Zwei"))
        en0 = _deck(_title("en"), _idless_md("en", "One"), _idless_md("en", "Two"))
        de1 = _deck(_title("de"), _idless_md("de", "Eins geaendert"), _idless_md("de", "Zwei"))
        en1 = _deck(_title("en"), _idless_md("en", "One"), _idless_md("en", "Two changed"))
        plan, result, de_after, en_after = _sync(
            tmp_path, "watermark", de0, en0, de1, en1, judge=_update_judge("# RECONCILED")
        )
        assert not _error_issues(plan)
        assert not any(p.kind == "conflict" for p in plan.proposals)
        assert result.applied_edit >= 2
        assert result.deferred == 0
        assert "RECONCILED" in de_after and "RECONCILED" in en_after


class TestBothSidedMarkdownDowngradesToInSync:
    def test_judge_equivalent_both_sided_markdown_downgrades(self, tmp_path: Path):
        # One id-less markdown cell edited on BOTH halves (no winner) → a conflict.
        # Increment 2: the judge finds the halves already equivalent, so it downgrades
        # to in_sync (no write, no defer) and the watermark advances.
        de0 = _deck(_title("de"), _idless_md("de", "Hallo"))
        en0 = _deck(_title("en"), _idless_md("en", "Hello"))
        de1 = _deck(_title("de"), _idless_md("de", "Hallo Welt"))
        en1 = _deck(_title("en"), _idless_md("en", "Hello World"))
        plan, result, de_after, en_after = _sync(
            tmp_path, "watermark", de0, en0, de1, en1, judge=_in_sync_judge()
        )
        # The classifier still emits a (both-sided) conflict; the apply downgrades it.
        assert any(p.kind == "conflict" and p.role == "localized-markdown" for p in plan.proposals)
        assert result.in_sync >= 1
        assert result.deferred == 0
        assert result.watermark_recorded
        # No write: both edited bodies are left exactly as authored.
        assert "Hallo Welt" in de_after and "Hello World" in en_after

    def test_judge_divergent_both_sided_markdown_still_defers(self, tmp_path: Path):
        # The conservative side: the judge proposes an update (a genuine divergence), so
        # the both-sided conflict is NOT downgraded — it defers, holding the watermark.
        de0 = _deck(_title("de"), _idless_md("de", "Hallo"))
        en0 = _deck(_title("en"), _idless_md("en", "Hello"))
        de1 = _deck(_title("de"), _idless_md("de", "Komplett anders"))
        en1 = _deck(_title("en"), _idless_md("en", "Totally different"))
        _plan, result, de_after, en_after = _sync(
            tmp_path, "watermark", de0, en0, de1, en1, judge=_update_judge("# x")
        )
        assert result.in_sync == 0
        assert result.deferred >= 1
        assert not result.watermark_recorded
        # Defer-only: both edits stay on disk, nothing overwritten.
        assert "Komplett anders" in de_after and "Totally different" in en_after

    def test_both_sided_code_has_no_judge_path_and_defers(self, tmp_path: Path):
        # Code cannot be judged for equivalence (the markdown judge prompt does not fit
        # runnable code), so a both-sided id-less *code* conflict always defers even with
        # a judge present — the increment-1 safety floor is preserved.
        de0 = _deck(_title("de"), _idless_code("de", "x = 1"))
        en0 = _deck(_title("en"), _idless_code("en", "x = 1"))
        de1 = _deck(_title("de"), _idless_code("de", "x = 1  # DE"))
        en1 = _deck(_title("en"), _idless_code("en", "x = 1  # EN"))
        _plan, result, de_after, en_after = _sync(
            tmp_path, "watermark", de0, en0, de1, en1, judge=_in_sync_judge()
        )
        assert result.in_sync == 0
        assert result.deferred >= 1
        assert not result.watermark_recorded
        assert "# DE" in de_after and "# EN" in en_after
