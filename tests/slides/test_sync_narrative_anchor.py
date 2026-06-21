"""Issue #403 Phase B — narrative (voiceover/notes) anchoring in ``clm slides sync``.

Phase A made an id-less narrative *placeable* (it no longer collapses several
voiceovers onto one ``(slide_id, role)`` key or errors on a leading greeting). Phase B
gives the engine an **identity** for those narratives — the n-th narrative of its role
under its owning slide — recorded as the watermark ``anchor`` column, so a later sync
can detect an **edit** (not just an add), recognize an already-paired narrative as
in-sync instead of re-adding it, and pair an id-less half with its id'd twin (report
#10's destructive doubling). These tests pin those behaviors and the data-safety nets.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.slides.sync_apply import _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _title(lang: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="title"\n# # T\n'


def _slide(lang: str, sid: str, txt: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _code(lang: str, body: str, sid: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _vo(lang: str, body: str, sid: str | None = None) -> str:
    s = f' slide_id="{sid}"' if sid else ""
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"]{s}\n{body}\n'


def _deck(*parts: str) -> str:
    return "\n".join(parts)


def _vo_count(text: str) -> int:
    return text.count('tags=["voiceover"]')


def _sync(
    tmp: Path,
    de0: str,
    en0: str,
    de1: str,
    en1: str,
    *,
    mapping: dict[str, str] | None = None,
    update_to: str | None = None,
):
    """Record a watermark from (de0, en0), apply (de1, en1), return (plan, result, de, en)."""
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    wm = SyncWatermarkCache(db)
    _record_watermark(wm, de_path, en_path)
    wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    judge = (
        StaticSyncJudge(default_proposal=SyncProposal(verdict="update", proposed_text=update_to))
        if update_to is not None
        else None
    )
    translator = StaticSlideTranslator(mapping=mapping or {}, default="<<XL>>")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=wm)
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Report #10 — id-less vs id'd voiceover: destructive doubling is fixed
# ---------------------------------------------------------------------------


class TestIdlessIddPairing:
    def test_idless_de_pairs_with_idd_en_no_doubling(self, tmp_path: Path):
        # The two halves disagree on whether the voiceover carries a slide_id: DE
        # id-less, EN id'd (== owning slide). They are the SAME narrative and must
        # pair, not each become a fresh add that doubles both decks (report #10).
        de = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello", sid="title"))
        plan, result, de_after, en_after = _sync(tmp_path, de, en, de, en)
        assert result.errors == []
        assert _vo_count(de_after) == 1  # NOT doubled
        assert _vo_count(en_after) == 1
        assert plan.is_noop

    def test_both_sided_idless_recognized_in_sync(self, tmp_path: Path):
        # Both halves id-less: pre-Phase-B this was a perpetual both-direction refusal;
        # now the anchor pairing recognizes them as already in sync.
        de = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello"))
        plan, result, de_after, en_after = _sync(tmp_path, de, en, de, en)
        assert result.errors == []
        assert plan.is_noop
        assert _vo_count(de_after) == 1
        assert _vo_count(en_after) == 1


# ---------------------------------------------------------------------------
# Edit detection — the capability Phase B adds over the add-only Phase A path
# ---------------------------------------------------------------------------


class TestNarrativeEditDetection:
    def test_edit_voiceover_one_side_propagates(self, tmp_path: Path):
        # Editing an id-less voiceover on one half now propagates to the other (the
        # add-only `_append_idless_adds` route could not detect this).
        de0 = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo welt"))
        en0 = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello world"))
        de1 = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo NEUE welt"))
        plan, result, _de, en_after = _sync(tmp_path, de0, en0, de1, en0, update_to="# Hello NEW")
        assert [p.kind for p in plan.proposals] == ["edit"]
        assert plan.proposals[0].direction == "de->en"
        assert result.applied_edit == 1
        assert "# Hello NEW" in en_after

    def test_edit_both_sides_is_conflict(self, tmp_path: Path):
        de0 = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# a"))
        en0 = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# a"))
        de1 = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# a DE"))
        en1 = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# a EN"))
        plan, result, _de, _en = _sync(tmp_path, de0, en0, de1, en1)
        assert any(p.kind == "conflict" for p in plan.proposals)
        assert result.watermark_recorded is False  # divergence not baselined

    def test_remove_voiceover_one_side_propagates(self, tmp_path: Path):
        de0 = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en0 = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello"))
        de1 = _deck(_title("de"), _code("de", "print(1)", "c1"))  # voiceover removed
        plan, result, _de, en_after = _sync(tmp_path, de0, en0, de1, en0)
        assert any(p.kind == "remove" for p in plan.proposals)
        assert result.applied_remove == 1
        assert _vo_count(en_after) == 0


# ---------------------------------------------------------------------------
# Occurrence ordinal — several narratives per slide stay distinct (#6 / §6.1)
# ---------------------------------------------------------------------------


class TestOccurrenceOrdinal:
    def test_two_voiceovers_under_one_slide_each_editable(self, tmp_path: Path):
        # Two id-less voiceovers after two code cells under one slide: editing the
        # SECOND must propagate to the second only (occurrence ordinal is load-bearing).
        de0 = _deck(
            _title("de"),
            _code("de", "print(1)", "c1"),
            _vo("de", "# erste"),
            _code("de", "print(2)", "c2"),
            _vo("de", "# zweite"),
        )
        en0 = _deck(
            _title("en"),
            _code("en", "print(1)", "c1"),
            _vo("en", "# first"),
            _code("en", "print(2)", "c2"),
            _vo("en", "# second"),
        )
        de1 = de0.replace("# zweite", "# zweite BEARBEITET")
        plan, result, _de, en_after = _sync(tmp_path, de0, en0, de1, en0, update_to="# second EDIT")
        assert [p.kind for p in plan.proposals] == ["edit"]
        assert result.applied_edit == 1
        assert "# second EDIT" in en_after
        assert "# first" in en_after  # the first voiceover is untouched


# ---------------------------------------------------------------------------
# Report #10 fix #2 — mass-add of shadowed narratives is refused, loudly
# ---------------------------------------------------------------------------


class TestMassAddGuard:
    def test_mass_idless_add_under_existing_voiceovers_is_refused(self, tmp_path: Path):
        # A baseline where every slide already has an id'd EN voiceover; then DE grows a
        # SECOND id-less voiceover under each of three slides whose EN twin can't pair
        # (its predecessor differs) — a mass of shadowed adds = mis-aligned halves.
        slides = ["a", "b", "c"]
        de0 = _deck(
            *[s for sid in slides for s in (_slide("de", sid, sid), _vo("de", f"# vo {sid}"))]
        )
        en0 = _deck(
            *[
                s
                for sid in slides
                for s in (_slide("en", sid, sid), _vo("en", f"# vo {sid}", sid=sid))
            ]
        )
        # DE adds a second id-less voiceover under each slide (different content).
        de1 = _deck(
            *[
                s
                for sid in slides
                for s in (
                    _slide("de", sid, sid),
                    _vo("de", f"# vo {sid}"),
                    _vo("de", f"# extra {sid}"),
                )
            ]
        )
        plan, result, _de, en_after = _sync(tmp_path, de0, en0, de1, en0)
        assert any(p.kind == "refuse" for p in plan.proposals)
        assert result.watermark_recorded is False
        assert "# extra" not in en_after  # nothing written — duplicates avoided


# ---------------------------------------------------------------------------
# Watermark anchor channel round-trips (recording side)
# ---------------------------------------------------------------------------


class TestAnchorRecording:
    def test_record_watermark_writes_narrative_anchors(self, tmp_path: Path):
        de = _deck(_title("de"), _code("de", "print(1)", "c1"), _vo("de", "# Hallo"))
        en = _deck(_title("en"), _code("en", "print(1)", "c1"), _vo("en", "# Hello"))
        de_path, en_path = tmp_path / "d.de.py", tmp_path / "d.en.py"
        de_path.write_text(de, encoding="utf-8")
        en_path.write_text(en, encoding="utf-8")
        wm = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _record_watermark(wm, de_path, en_path)
            de_anchors = wm.get_deck_anchors(str(de_path), str(en_path), "de")
        finally:
            wm.close()
        # The single narrative row (position 2: title, code, voiceover) records an
        # ``id:`` anchor on its predecessor code cell; the slide/code rows do not.
        assert de_anchors == {2: "id:c1#0"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)
