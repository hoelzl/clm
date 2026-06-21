"""Reproduction / characterization tests for issues #364, #365, #366.

These three issues all stem from one incident: a split deck whose two halves were
edited and committed twice *without* an intervening ``clm slides sync``, so the
structural watermark fell behind. A later sync then errored against the stale
baseline with an opaque ``id-less localized cells ... edited on both decks`` message,
even though the halves were mutually consistent at git HEAD.

This module pins down what master does for each issue, so we can see which concerns
recent work (the #363 watermark CLI, the ``--baseline`` / ``--rebaseline`` /
``synced_commit`` staleness work) has already addressed and which remain open:

- ``TestBothSidedIdlessDriftStillHardErrors`` (#365) — a both-sided edit to a hash-only
  id-less localized cell still hard-errors with no single direction; NOT yet degraded to
  a per-cell / positional conflict (the strict-xfail records that gap).
- ``TestIdlessDriftErrorIsLocalized`` (#364 item 4, FIXED) — that error now pins to the
  drifted cell's owning slide group and echoes the offending cell, and steers to
  ``--rebaseline`` rather than only "assign slide_ids".

The reproduction harness mirrors ``tests/slides/test_sync_issue_269.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.slides.sync_apply import _record_watermark, apply_plan
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


def _deck(*parts: str) -> str:
    return "\n".join(parts)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _sync(tmp: Path, baseline: str, de0: str, en0: str, de1: str, en1: str):
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
            plan, judge=None, translator=StaticSlideTranslator(default="<<XL>>"), watermark_cache=wm
        )
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


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
# #365 — both-sided id-less-localized drift still hard-errors (NOT yet degraded)
# ---------------------------------------------------------------------------


class TestBothSidedIdlessDriftStillHardErrors:
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_both_sided_idless_edit_errors_with_no_single_direction(
        self, tmp_path: Path, baseline: str
    ):
        plan, result, de_after, en_after = _sync(tmp_path, baseline, DE0, EN0, DE1, EN1)
        errors = _error_issues(plan)
        assert errors, "expected the both-sided id-less drift to surface an error"
        assert any("both decks" in e.reason for e in errors)
        # The cardinal #269 safety still holds: nothing written, watermark held, both
        # edits survive on disk (the error is loud, not a silent drop or destructive heal).
        assert not result.watermark_recorded
        assert "# DE-edit" in de_after
        assert "# EN-edit" in en_after

    @pytest.mark.xfail(
        reason="#365: both-sided id-less-localized drift should degrade to a per-cell / "
        "positional conflict the author can resolve, not a whole-deck hard error.",
        strict=True,
    )
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_both_sided_idless_edit_degrades_to_positional_conflict(
        self, tmp_path: Path, baseline: str
    ):
        plan, _result, _de_after, _en_after = _sync(tmp_path, baseline, DE0, EN0, DE1, EN1)
        # Desired (#365): the drift is paired positionally within its slide group and
        # surfaced as a resolvable conflict proposal, not an irreconcilable error.
        assert any(p.kind == "conflict" for p in plan.proposals)
        assert not _error_issues(plan)


# ---------------------------------------------------------------------------
# #364 item 4 — the id-less-localized error is now localized to a cell (FIXED)
# ---------------------------------------------------------------------------


class TestIdlessDriftErrorIsLocalized:
    @pytest.mark.parametrize("baseline", ["git-head", "watermark"])
    def test_error_names_owning_group_and_offending_cell(self, tmp_path: Path, baseline: str):
        plan, _result, _de_after, _en_after = _sync(tmp_path, baseline, DE0, EN0, DE1, EN1)
        errors = _error_issues(plan)
        assert errors
        # Located: the error pins to the drifted cell's owning slide group (was always
        # None) AND echoes the offending cell's first line so the author can find it.
        assert any(e.slide_id == "title" for e in errors)
        assert any("test_queries" in e.reason or "comparison_queries" in e.reason for e in errors)

    def test_error_steers_to_rebaseline_not_just_assign_ids(self, tmp_path: Path):
        # #364 item 3: the old message only said "assign slide_ids" (which does not help
        # the stale-watermark case). The new message leads with the actual common fix.
        plan, _result, _de_after, _en_after = _sync(tmp_path, "watermark", DE0, EN0, DE1, EN1)
        reason = "\n".join(e.reason for e in _error_issues(plan))
        assert "--rebaseline" in reason
