"""Tests for the non-interactive ``--conflict`` policy (Issue #447).

Covers the policy builder (which conflicts it resolves vs omits), the
identity-preserving ``_conflict_as_edit`` bugfix, and the end-to-end apply behavior
(de-wins overwrites the loser; leave defers; the equivalence gate downgrades a false
conflict; the ``*-safe`` escalate tier defers a conflict whose loser carries
independent content and resolves one whose loser does not).
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.sync_apply import (
    DECISION_DE_WINS,
    DECISION_DE_WINS_SAFE,
    _conflict_as_edit,
    apply_plan,
    conflict_policy_decisions,
)
from clm.slides.sync_plan import Proposal, SyncPlan


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de_body: str, en_body: str, sid: str = "a") -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(_slide("de", sid, de_body), encoding="utf-8")
    en_path.write_text(_slide("en", sid, en_body), encoding="utf-8")
    return de_path, en_path


def _conflict_plan(de_path: Path, en_path: Path, sid: str = "a") -> SyncPlan:
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals.append(Proposal(kind="conflict", role="slide", direction=None, slide_id=sid))
    return plan


class _DirectionalJudge:
    """A judge whose verdict depends on the (source_lang, target_lang) direction.

    Lets a test drive the equivalence gate (both directions) and the escalate
    containment check (loser→winner) independently.
    """

    prompt_version = "test"

    def __init__(self, verdicts: dict[tuple[str, str], str], text: str = "OVERWRITTEN") -> None:
        self._verdicts = verdicts
        self._text = text

    def propose(self, source_text, target_text, *, source_lang, target_lang):  # noqa: ANN001
        verdict = self._verdicts.get((source_lang, target_lang), "update")
        proposed = self._text if verdict == "update" else target_text
        return SyncProposal(verdict=verdict, proposed_text=proposed)


# ---------------------------------------------------------------------------
# Policy builder + recast (pure)
# ---------------------------------------------------------------------------


def test_leave_policy_is_empty():
    plan = SyncPlan(de_path=Path("d"), en_path=Path("e"), baseline_source="watermark")
    plan.proposals.append(Proposal(kind="conflict", role="slide", direction=None, slide_id="a"))
    assert conflict_policy_decisions(plan, "leave") == {}


def test_de_wins_maps_both_edited_conflict():
    plan = SyncPlan(de_path=Path("d"), en_path=Path("e"), baseline_source="watermark")
    p = Proposal(kind="conflict", role="slide", direction=None, slide_id="a")
    plan.proposals.append(p)
    assert conflict_policy_decisions(plan, "de-wins") == {id(p): DECISION_DE_WINS}


def test_policy_omits_remove_vs_edit():
    plan = SyncPlan(de_path=Path("d"), en_path=Path("e"), baseline_source="watermark")
    plan.proposals.append(
        Proposal(
            kind="conflict",
            role="slide",
            direction=None,
            slide_id="a",
            conflict_subtype="remove-vs-edit",
        )
    )
    assert conflict_policy_decisions(plan, "de-wins") == {}


def test_policy_omits_idless_localized_conflict():
    plan = SyncPlan(de_path=Path("d"), en_path=Path("e"), baseline_source="watermark")
    # An id-less localized conflict: slide_id None + a localized role.
    plan.proposals.append(
        Proposal(kind="conflict", role="localized-markdown", direction=None, slide_id=None)
    )
    assert conflict_policy_decisions(plan, "de-wins") == {}


def test_conflict_as_edit_preserves_narrative_identity():
    p = Proposal(
        kind="conflict",
        role="voiceover",
        direction=None,
        slide_id=None,
        anchor="construct:foo",
        owning_slide_id="intro",
        anchor_occ=2,
        source_position=4,
        target_position=5,
    )
    edit = _conflict_as_edit(p, "de->en")
    assert edit.kind == "edit" and edit.direction == "de->en"
    assert edit.anchor == "construct:foo"
    assert edit.owning_slide_id == "intro"
    assert edit.anchor_occ == 2
    assert (edit.source_position, edit.target_position) == (4, 5)


# ---------------------------------------------------------------------------
# End-to-end apply
# ---------------------------------------------------------------------------


def test_de_wins_overwrites_the_losing_half(tmp_path: Path):
    de_path, en_path = _write_pair(tmp_path, "# DE neu", "# EN alt")
    plan = _conflict_plan(de_path, en_path)
    judge = StaticSyncJudge(
        default_proposal=SyncProposal(verdict="update", proposed_text="# DE→EN")
    )
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        result = apply_plan(
            plan,
            judge=judge,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "de-wins"),
        )
    finally:
        cache.close()
    assert result.conflicts_resolved == 1
    assert result.deferred == 0
    assert "DE→EN" in en_path.read_text(encoding="utf-8")


def test_leave_defers_the_conflict(tmp_path: Path):
    de_path, en_path = _write_pair(tmp_path, "# DE neu", "# EN alt")
    plan = _conflict_plan(de_path, en_path)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        result = apply_plan(
            plan,
            judge=None,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "leave"),
        )
    finally:
        cache.close()
    assert result.deferred == 1
    assert result.conflicts_resolved == 0


def test_equivalence_gate_downgrades_a_false_conflict(tmp_path: Path):
    de_path, en_path = _write_pair(tmp_path, "# same", "# same")
    plan = _conflict_plan(de_path, en_path)
    # A judge that finds both directions in_sync → the halves are already equivalent.
    judge = _DirectionalJudge({("de", "en"): "in_sync", ("en", "de"): "in_sync"})
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        before = en_path.read_text(encoding="utf-8")
        result = apply_plan(
            plan,
            judge=judge,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "de-wins"),
        )
    finally:
        cache.close()
    assert result.in_sync >= 1
    assert result.conflicts_resolved == 0
    assert en_path.read_text(encoding="utf-8") == before  # no overwrite


def test_safe_policy_escalates_when_loser_has_independent_content(tmp_path: Path):
    de_path, en_path = _write_pair(tmp_path, "# DE neu", "# EN independent")
    plan = _conflict_plan(de_path, en_path)
    # Not equivalent (de→en update); EN→DE also update → EN has content DE lacks → escalate.
    judge = _DirectionalJudge({("de", "en"): "update", ("en", "de"): "update"})
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        before = en_path.read_text(encoding="utf-8")
        result = apply_plan(
            plan,
            judge=judge,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "de-wins-safe"),
        )
    finally:
        cache.close()
    assert result.conflicts_escalated == 1
    assert result.deferred == 1
    assert result.conflicts_resolved == 0
    assert en_path.read_text(encoding="utf-8") == before  # escalated → not overwritten


def test_safe_policy_resolves_when_loser_has_no_independent_content(tmp_path: Path):
    de_path, en_path = _write_pair(tmp_path, "# DE neu", "# EN alt")
    plan = _conflict_plan(de_path, en_path)
    # Not equivalent (de→en update), but EN→DE in_sync → DE already reflects EN → safe to
    # overwrite EN. The de→en resolution then writes the proposed text.
    judge = _DirectionalJudge({("de", "en"): "update", ("en", "de"): "in_sync"}, text="# DE→EN")
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        result = apply_plan(
            plan,
            judge=judge,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "de-wins-safe"),
        )
    finally:
        cache.close()
    assert result.conflicts_resolved == 1
    assert result.conflicts_escalated == 0
    assert "DE→EN" in en_path.read_text(encoding="utf-8")


def test_de_wins_does_not_defer_regular_edits(tmp_path: Path):
    # The overlay regression (#447): a de-wins policy is a conflict-only overlay — the
    # deterministic edits on OTHER cells must still batch-apply, not get swept into
    # interactive-mode deferral (the bug a conflicts-only `decisions=` map caused).
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(
        _slide("de", "a", "# DE neu") + _slide("de", "b", "# B neu"), encoding="utf-8"
    )
    en_path.write_text(
        _slide("en", "a", "# EN alt") + _slide("en", "b", "# B old"), encoding="utf-8"
    )
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals.append(Proposal(kind="conflict", role="slide", direction=None, slide_id="a"))
    plan.proposals.append(Proposal(kind="edit", role="slide", direction="de->en", slide_id="b"))
    judge = StaticSyncJudge(default_proposal=SyncProposal(verdict="update", proposed_text="# NEW"))
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        result = apply_plan(
            plan,
            judge=judge,
            watermark_cache=cache,
            conflict_decisions=conflict_policy_decisions(plan, "de-wins"),
        )
    finally:
        cache.close()
    assert result.conflicts_resolved == 1  # the conflict resolved
    assert result.applied_edit >= 1  # the regular edit STILL applied (not deferred)
    assert result.deferred == 0
