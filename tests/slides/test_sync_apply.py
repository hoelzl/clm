"""Tests for :mod:`clm.slides.sync_apply` (Issue #166, Phase 2 apply engine)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncCorrespondenceCache, SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import DECISION_APPLY, DECISION_SKIP, apply_plan
from clm.slides.sync_plan import PlanIssue, Proposal, SyncPlan, build_sync_plan, ordered_sync_cells
from clm.slides.sync_recover import StaticCorrespondenceVerifier
from clm.slides.sync_translate import StaticSlideTranslator
from clm.slides.sync_writeback import FileState

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


def _update_judge(text: str) -> StaticSyncJudge:
    return StaticSyncJudge(default_proposal=SyncProposal(verdict="update", proposed_text=text))


class _CountingTranslator:
    """Wraps a StaticSlideTranslator and counts translate() calls (#216 2b boundary)."""

    prompt_version = "counting"

    def __init__(self, inner: StaticSlideTranslator) -> None:
        self._inner = inner
        self.calls = 0

    def translate(self, **kwargs) -> str:
        self.calls += 1
        return self._inner.translate(**kwargs)


# ---------------------------------------------------------------------------
# FileState primitives
# ---------------------------------------------------------------------------


class TestFileStatePrimitives:
    def test_find_and_replace_cell_body(self, tmp_path: Path):
        path = tmp_path / "deck.en.py"
        path.write_text(
            _slide("en", "a", "# ## A\n# - one") + _slide("en", "b", "# ## B"),
            encoding="utf-8",
        )
        state = FileState.load(path)
        assert state.find_cell("a", "slide") is not None
        assert state.find_cell("missing", "slide") is None
        assert state.replace_cell_body("a", "slide", "# ## A\n# - one\n# - two") is True
        state.flush()
        text = path.read_text(encoding="utf-8")
        assert "- two" in text
        assert "# ## B" in text  # untouched

    def test_delete_cell(self, tmp_path: Path):
        path = tmp_path / "deck.en.py"
        path.write_text(
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
            encoding="utf-8",
        )
        state = FileState.load(path)
        assert state.delete_cell("a", "slide") is True
        state.flush()
        cells = parse_cells(path.read_text(encoding="utf-8"))
        ids = [c.metadata.slide_id for c in cells if c.metadata.slide_id]
        assert ids == ["b"]

    def test_delete_missing_returns_false(self, tmp_path: Path):
        path = tmp_path / "deck.en.py"
        path.write_text(_slide("en", "a", "# ## A"), encoding="utf-8")
        state = FileState.load(path)
        assert state.delete_cell("nope", "slide") is False
        assert state.dirty is False

    def test_delete_last_cell_preserves_terminal_newline(self, tmp_path: Path):
        # Removing the file's last cell must not strip its trailing newline
        # (which split_cells parks on that cell) — else a "No newline at end
        # of file" diff and a pre-commit end-of-file-fixer trip.
        path = tmp_path / "deck.en.py"
        path.write_text(
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
            encoding="utf-8",
        )
        assert path.read_text(encoding="utf-8").endswith("\n")
        state = FileState.load(path)
        assert state.delete_cell("b", "slide") is True
        state.flush()
        assert path.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# apply_plan — edit
# ---------------------------------------------------------------------------


class TestApplyEdit:
    def test_edit_writes_target_body(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung\n# - eins\n# - zwei")
        en = _slide("en", "intro", "# ## Introduction\n# - one")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(de, encoding="utf-8")  # (unchanged here; edit comes below)
            # Author edits DE: add a bullet.
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n# - eins\n# - zwei\n# - drei"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1

            judge = _update_judge("# ## Introduction\n# - one\n# - two\n# - three")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1
        assert "- three" in en_path.read_text(encoding="utf-8")

    def test_edit_idempotent_after_watermark_advance(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung\n# - eins")
        en = _slide("en", "intro", "# ## Introduction\n# - one")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n# - eins\n# - zwei"), encoding="utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            judge = _update_judge("# ## Introduction\n# - one\n# - two")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
            assert result.watermark_recorded is True

            # Re-planning against the advanced watermark is a no-op.
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert plan2.is_noop

    def test_edit_judge_called_once_in_materialize_not_in_execute(self, tmp_path: Path):
        # The model call for an edit happens exactly once, in the materialize pass
        # (#216 resolve-then-apply 2b); the execute pass writes mechanically and
        # never re-invokes the judge.
        de = _slide("de", "intro", "# ## Einleitung\n# - eins")
        en = _slide("en", "intro", "# ## Introduction\n# - one")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n# - eins\n# - zwei"), encoding="utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1
            # StaticSyncJudge records every propose() call in .calls.
            judge = _update_judge("# ## Introduction\n# - one\n# - two")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1
        assert len(judge.calls) == 1  # materialized once; execute did not re-call

    def test_edit_in_sync_verdict_writes_nothing(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(_slide("de", "intro", "# ## Einleitung (neu)"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            before = en_path.read_text(encoding="utf-8")
            judge = StaticSyncJudge(
                default_proposal=SyncProposal(verdict="in_sync", proposed_text="x")
            )
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 0
        assert result.in_sync == 1
        assert en_path.read_text(encoding="utf-8") == before

    def test_edit_without_judge_is_error(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(_slide("de", "intro", "# ## Einleitung (neu)"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 0
        assert result.has_errors
        assert result.watermark_recorded is False  # errors block watermark advance


# ---------------------------------------------------------------------------
# apply_plan — remove
# ---------------------------------------------------------------------------


class TestApplyRemove:
    def test_remove_deletes_target_cell(self, tmp_path: Path):
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author removes slide b from DE.
            de_path.write_text(_slide("de", "a", "# ## A"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("remove") == 1
            result = apply_plan(plan, judge=None, watermark_cache=cache)

            # Re-plan is a no-op after the propagated removal.
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_remove == 1
        en_ids = [
            c.metadata.slide_id
            for c in parse_cells(en_path.read_text("utf-8"))
            if c.metadata.slide_id
        ]
        assert en_ids == ["a"]
        assert plan2.is_noop


# ---------------------------------------------------------------------------
# apply_plan — deferred kinds & watermark discipline
# ---------------------------------------------------------------------------


class TestDeferredAndWatermark:
    def test_cold_start_does_not_advance_watermark(self, tmp_path: Path):
        # baseline=none: shared-id pairs are counted in_sync but never
        # content-verified, so the watermark must NOT be recorded — else
        # unverified cross-language drift gets silently baselined.
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## Apfel"),
            _slide("en", "a", "# ## Totally different content"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = build_sync_plan(de_path, en_path, allow_git_fallback=False)
            assert plan.baseline_source == "none"
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.watermark_recorded is False
        assert recorded is False

    def test_conflict_is_deferred_and_watermark_not_advanced(self, tmp_path: Path):
        # A conflict is isolated (never applied), so the watermark must not
        # advance — un-reconciled state must not be baselined.
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
            plan.proposals.append(
                Proposal(kind="conflict", role="slide", direction=None, slide_id="a")
            )
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.deferred == 1
        assert result.applied == 0
        assert result.watermark_recorded is False
        assert recorded is False  # un-reconciled conflict must not be baselined

    def test_remove_missing_target_is_error(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
            plan.proposals.append(
                Proposal(kind="remove", role="slide", direction="de->en", slide_id="ghost")
            )
            result = apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_remove == 0
        assert result.has_errors

    def test_one_edit_error_does_not_block_a_remove(self, tmp_path: Path):
        # A plan with a remove (deterministic) and an edit that errors (no
        # judge): the per-proposal loop is not aborted by the edit error — the
        # remove is still *processed* in memory (applied_remove == 1). But the
        # buffered temp-swap (Issue #190 item 1) makes the *disk write* atomic:
        # because the pass errored, NEITHER deck is written, so the (in-memory)
        # remove is rolled back on disk and EN keeps both slides.
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
        plan.proposals.append(
            Proposal(kind="remove", role="slide", direction="de->en", slide_id="b")
        )
        plan.proposals.append(Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"))
        result = apply_plan(plan, judge=None, watermark_cache=None)

        assert result.applied_remove == 1  # processed in memory (loop not aborted)
        assert result.has_errors  # the edit had no judge
        en_ids = [
            c.metadata.slide_id
            for c in parse_cells(en_path.read_text("utf-8"))
            if c.metadata.slide_id
        ]
        assert en_ids == ["a", "b"]  # erroring pass writes nothing — remove not persisted


# ---------------------------------------------------------------------------
# apply_plan — buffered temp-swap atomicity (Issue #190 item 1)
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_render_matches_flush_bytes(self, tmp_path: Path):
        # The temp-swap writes FileState.render() verbatim; it must reproduce
        # flush()'s exact bytes, or the "clean path is byte-identical to today"
        # contract breaks.
        path = tmp_path / "deck.en.py"
        path.write_text(
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
            encoding="utf-8",
        )
        state = FileState.load(path)
        assert state.replace_cell_body("a", "slide", "# ## A\n# - one") is True
        rendered = state.render()
        state.flush()
        assert path.read_bytes() == rendered.encode("utf-8")

    def test_clean_pass_persists_and_leaves_no_temp_file(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung\n# - eins")
        en = _slide("en", "intro", "# ## Introduction\n# - one")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n# - eins\n# - zwei"), encoding="utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            judge = _update_judge("# ## Introduction\n# - one\n# - two")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
        finally:
            cache.close()

        assert not result.has_errors
        assert result.applied_edit == 1
        assert "- two" in en_path.read_text(encoding="utf-8")
        # The atomic os.replace must leave no stray temp file behind.
        assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_clean_add_writes_both_decks(self, tmp_path: Path):
        # An id-less add mints an id onto the DE source AND inserts the twin on
        # EN, so a clean pass must persist BOTH decks through the temp-swap.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        de_before = de_path.read_bytes()
        en_before = en_path.read_bytes()
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(mapping={"# ## Neu": "# ## New"})
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert not result.has_errors
        assert result.applied_add == 1
        assert de_path.read_bytes() != de_before  # DE got the minted id
        assert en_path.read_bytes() != en_before  # EN got the inserted twin
        assert "# ## New" in en_path.read_text(encoding="utf-8")
        assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_erroring_pass_writes_neither_deck(self, tmp_path: Path):
        # A successful edit (dirties EN in memory) followed by a proposal that
        # errors: the buffered swap rolls the WHOLE pass back, so the successful
        # edit is not persisted. Pre-#190 the code flushed it.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        de_before = de_path.read_bytes()
        en_before = en_path.read_bytes()

        plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
        # edit "a": the judge rewrites it -> succeeds, dirties EN in memory.
        plan.proposals.append(Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"))
        # remove a ghost cell: target not found -> apply-time error.
        plan.proposals.append(
            Proposal(kind="remove", role="slide", direction="de->en", slide_id="ghost")
        )
        result = apply_plan(plan, judge=_update_judge("# ## A (updated)"), watermark_cache=None)

        assert result.has_errors
        assert result.applied_edit == 1  # the edit succeeded in memory...
        assert en_path.read_bytes() == en_before  # ...but the erroring pass wrote nothing
        assert de_path.read_bytes() == de_before
        assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_classifier_error_also_rolls_back_a_valid_edit(self, tmp_path: Path):
        # A *classifier* error (plan.has_errors, e.g. an unresolvable duplicate id)
        # coexisting with a valid edit: the whole pass rolls back even though no
        # apply-time error occurred. This honors design §11 ("write only if the
        # whole pass is error-free") and future-proofs the later phases that add
        # classifier errors not backed by a physical residual-duplicate fail-safe.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        en_before = en_path.read_bytes()

        plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
        plan.issues.append(
            PlanIssue(severity="error", slide_id="b", reason="duplicate slide_id (synthetic)")
        )
        plan.proposals.append(Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"))
        result = apply_plan(plan, judge=_update_judge("# ## A (updated)"), watermark_cache=None)

        assert plan.has_errors
        assert not result.has_errors  # no APPLY-time error
        assert result.applied_edit == 1  # the edit succeeded in memory...
        assert en_path.read_bytes() == en_before  # ...but the classifier-error pass wrote nothing


# ---------------------------------------------------------------------------
# apply_plan — move (Phase 2b)
# ---------------------------------------------------------------------------


def _vo(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n{body}\n'


def _slide_order(path: Path) -> list[str]:
    return [
        c.metadata.slide_id
        for c in parse_cells(path.read_text(encoding="utf-8"))
        if c.metadata.is_slide_start
    ]


class TestApplyMove:
    def test_reorder_propagates_to_target(self, tmp_path: Path):
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B") + _slide("de", "c", "# ## C")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B") + _slide("en", "c", "# ## C")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author moves c to the front on DE.
            de_path.write_text(
                _slide("de", "c", "# ## C")
                + _slide("de", "a", "# ## A")
                + _slide("de", "b", "# ## B"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("move") >= 1
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_move >= 1
        assert _slide_order(en_path) == ["c", "a", "b"]
        # Byte-clean: identical to building the deck in target order — no
        # spurious blank line from the dragged terminal-newline artifact.
        expected = (
            _slide("en", "c", "# ## C") + _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        )
        assert en_path.read_text(encoding="utf-8") == expected
        assert plan2.is_noop  # idempotent after watermark advance

    def test_move_carries_narrative_companion(self, tmp_path: Path):
        de = (
            _slide("de", "a", "# ## A")
            + _vo("de", "a", "# voiceover a")
            + _slide("de", "b", "# ## B")
            + _vo("de", "b", "# voiceover b")
        )
        en = (
            _slide("en", "a", "# ## A")
            + _vo("en", "a", "# voiceover a")
            + _slide("en", "b", "# ## B")
            + _vo("en", "b", "# voiceover b")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Move the whole b-group ahead of a on DE.
            de_path.write_text(
                _slide("de", "b", "# ## B")
                + _vo("de", "b", "# voiceover b")
                + _slide("de", "a", "# ## A")
                + _vo("de", "a", "# voiceover a"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        # EN slide order mirrors DE, and each voiceover stays under its slide.
        en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
        sync_ids = [(c.metadata.slide_id, c.tags[0]) for c in en_cells if c.metadata.slide_id]
        assert sync_ids == [
            ("b", "slide"),
            ("b", "voiceover"),
            ("a", "slide"),
            ("a", "voiceover"),
        ]

    def test_move_deferred_when_plan_has_an_add(self, tmp_path: Path):
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Reorder AND add an id-less slide on DE.
            de_path.write_text(
                _slide("de", "b", "# ## B")
                + _slide("de", "a", "# ## A")
                + '# %% [markdown] lang="de" tags=["slide"]\n# ## Neu\n',
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("add") == 1
            assert plan.count("move") >= 1
            result = apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        # The add can't be applied (Phase 3), so the move is held back too and
        # the watermark does not advance — EN keeps its original order.
        assert result.applied_move == 0
        assert result.watermark_recorded is False
        assert _slide_order(en_path) == ["a", "b"]

    def test_narrative_reassignment_is_deferred_not_baselined(self, tmp_path: Path):
        # The author swaps the two voiceovers across slides (slide order stays
        # a,b, but notes change which slide they sit under). A slide-group
        # reorder cannot express that, so it must be deferred and surfaced —
        # never silently counted as applied and baselined.
        de = (
            _slide("de", "a", "# ## A")
            + _vo("de", "a", "# vo a")
            + _slide("de", "b", "# ## B")
            + _vo("de", "b", "# vo b")
        )
        en = de.replace('lang="de"', 'lang="en"')
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Swap the voiceovers: vo-b now under slide a, vo-a under slide b.
            swapped = (
                _slide("de", "a", "# ## A")
                + _vo("de", "b", "# vo b")
                + _slide("de", "b", "# ## B")
                + _vo("de", "a", "# vo a")
            )
            de_path.write_text(swapped, encoding="utf-8")
            before_en = en_path.read_text(encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("move") >= 1
            result = apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_move == 0
        assert result.deferred >= 1
        assert result.watermark_recorded is False  # divergence not baselined
        assert en_path.read_text(encoding="utf-8") == before_en  # EN untouched

    def test_classifier_error_defers_move(self, tmp_path: Path):
        # A classifier error (e.g. an id collision elsewhere) makes the pass
        # un-clean: a move must not write to disk while the watermark refuses
        # to advance (the gate and the watermark check share one predicate).
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
            plan.issues.append(PlanIssue(severity="error", slide_id="x", reason="collision"))
            plan.proposals.append(
                Proposal(kind="move", role="slide", direction="de->en", slide_id="b")
            )
            result = apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_move == 0
        assert result.deferred >= 1
        assert result.watermark_recorded is False
        assert _slide_order(en_path) == ["a", "b"]  # EN untouched


# ---------------------------------------------------------------------------
# apply_plan — add (Phase 3: translate + mint + insert)
# ---------------------------------------------------------------------------


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


def _vo_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"]\n{body}\n'


def _cell_for(path: Path, slide_id: str, role: str = "slide"):
    for c in parse_cells(path.read_text(encoding="utf-8")):
        if c.metadata.slide_id == slide_id and role in c.tags:
            return c
    return None


class TestApplyAdd:
    def test_idless_slide_add_mints_en_id_on_both_decks(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author appends a brand-new id-less slide on DE.
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neues Thema"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("add") == 1
            translator = StaticSlideTranslator(mapping={"# ## Neues Thema": "# ## New Topic"})
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 1
        # EN-authority: the id is slugged from the EN (translated) heading and
        # written to BOTH decks.
        de_new = _cell_for(de_path, "new-topic")
        en_new = _cell_for(en_path, "new-topic")
        assert de_new is not None and en_new is not None
        assert "Neues Thema" in de_new.content  # source body unchanged, just stamped
        assert "New Topic" in en_new.content  # translated counterpart
        assert _slide_order(en_path) == ["a", "new-topic"]
        assert en_path.read_text(encoding="utf-8").endswith("\n")
        assert plan2.is_noop  # idempotent: the stamped cell is no longer id-less

    def test_en_to_de_add_slugs_from_en_source(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide_idless("en", "# ## New Topic"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(mapping={"# ## New Topic": "# ## Neues Thema"})
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 1
        de_new = _cell_for(de_path, "new-topic")
        assert de_new is not None
        assert "Neues Thema" in de_new.content  # DE counterpart translated
        assert _slide_order(de_path) == ["a", "new-topic"]

    def test_narrative_companion_inherits_slide_id(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A")
                + _slide_idless("de", "# ## Neu")
                + _vo_idless("de", "# Sprechertext"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(
                mapping={"# ## Neu": "# ## New", "# Sprechertext": "# Narration"}
            )
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 2
        en_cells = parse_cells(en_path.read_text(encoding="utf-8"))
        sync_ids = [(c.metadata.slide_id, c.tags[0]) for c in en_cells if c.metadata.slide_id]
        assert sync_ids == [("a", "slide"), ("new", "slide"), ("new", "voiceover")]
        # The DE voiceover inherited the slide's minted id too.
        assert _cell_for(de_path, "new", "voiceover") is not None

    def test_translator_called_once_per_cell_in_materialize(self, tmp_path: Path):
        # The add path's translations are materialized up front (#216 2b); the
        # execute walk mints + inserts reading the cache, never re-calling the
        # translator. Two new cells (slide + voiceover) => exactly two calls.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A")
                + _slide_idless("de", "# ## Neu")
                + _vo_idless("de", "# Sprechertext"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = _CountingTranslator(
                StaticSlideTranslator(
                    mapping={"# ## Neu": "# ## New", "# Sprechertext": "# Narration"}
                )
            )
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 2
        assert translator.calls == 2  # materialized once each; execute read the cache

    def test_add_in_the_middle_is_anchored(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A")
                + _slide_idless("de", "# ## Mid")
                + _slide("de", "b", "# ## B"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(mapping={"# ## Mid": "# ## Middle"})
            apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert _slide_order(en_path) == ["a", "middle", "b"]

    def test_collision_resolves_against_existing_ids(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "new", "# ## Something"), _slide("en", "new", "# ## Something")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "new", "# ## Something") + _slide_idless("de", "# ## Neu"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            # "Neu" -> "New" -> slug "new", which collides with the existing id.
            translator = StaticSlideTranslator(mapping={"# ## Neu": "# ## New"})
            apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert _cell_for(en_path, "new-2") is not None
        assert _cell_for(de_path, "new-2") is not None

    def test_add_without_translator_is_deferred(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, translator=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 0
        assert result.deferred >= 1
        assert result.watermark_recorded is False
        assert _slide_order(en_path) == ["a"]  # nothing inserted

    def test_translation_failure_defers_the_add(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator()  # no mapping, no default -> raises
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 0
        assert result.has_errors
        assert result.watermark_recorded is False
        assert _slide_order(en_path) == ["a"]  # EN untouched
        # The DE source cell stays id-less, so it is re-detected next run.
        assert any(
            c.metadata.slide_id is None and "slide" in c.tags
            for c in parse_cells(de_path.read_text(encoding="utf-8"))
        )

    def test_bold_heading_mints_meaningful_id(self, tmp_path: Path):
        # A heading with a bold lead-in must still yield an EN-derived slug,
        # not a generic "slide" id.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Wichtig"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(
                mapping={"# ## Wichtig": "# ## **Important** Concept"}
            )
            apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert _cell_for(en_path, "important-concept") is not None
        assert _cell_for(de_path, "important-concept") is not None

    def test_parallel_idless_adds_on_both_decks_are_deferred(self, tmp_path: Path):
        # The author mistakenly added a new slide id-less on BOTH decks. Pairing
        # is out of scope; the engine must defer rather than duplicate.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu"), encoding="utf-8"
            )
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide_idless("en", "# ## New"), encoding="utf-8"
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(
                mapping={"# ## Neu": "# ## New", "# ## New": "# ## Neu"}
            )
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_add == 0
        assert result.deferred >= 2
        assert result.watermark_recorded is False
        # No duplication: each deck still has exactly its slide + one id-less new.
        assert len(_slide_order(de_path)) == 2  # "a" + one id-less
        assert len(_slide_order(en_path)) == 2

    def test_cold_start_mismatched_ids_must_not_double(self, tmp_path: Path):
        # Both halves carry slide_ids, but DIFFERENT ones, and there is no baseline
        # (e.g. assign-ids run per half). The resolver refuses both directions
        # rather than translate-and-insert both sets (which would DOUBLE both
        # decks); #216, the id-CARRYING sibling of the id-less case below.
        de = _slide("de", "d1", "# ## A") + _slide("de", "d2", "# ## B")
        en = _slide("en", "e1", "# ## A") + _slide("en", "e2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None)
        # Plan time: all four would-be adds become refusals; nothing to apply.
        assert plan.count("add") == 0
        assert plan.count("refuse") == 4
        translator = StaticSlideTranslator(default="# ## X")
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=None)
        assert result.applied_add == 0
        assert result.has_errors is False  # a refusal is not an error
        assert result.deferred == 4
        assert len(_slide_order(de_path)) == 2  # no duplication
        assert len(_slide_order(en_path)) == 2

    def test_cold_start_half_idd_must_not_double(self, tmp_path: Path):
        # A half-id'd cold-start pair (one half id-less, the other id'd): the
        # id-less half would add de->en and the id'd half en->de — adds in both
        # directions, so the resolver refuses them all rather than doubling (#216).
        de = _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B")
        en = _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None)
        assert plan.count("add") == 0
        assert plan.count("refuse") == 4
        translator = StaticSlideTranslator(default="# ## X")
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=None)
        assert result.applied_add == 0
        assert result.has_errors is False
        assert result.deferred == 4
        assert len(_slide_order(de_path)) == 2
        assert len(_slide_order(en_path)) == 2


class TestColdStartMint:
    """A both-id-less cold pair mints shared ids once correspondence is confirmed (#216 §12)."""

    def _cold_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        de = _slide_idless("de", "# ## Einleitung") + _slide_idless("de", "# ## Variablen")
        en = _slide_idless("en", "# ## Introduction") + _slide_idless("en", "# ## Variables")
        return _write_pair(tmp_path, de, en)

    def test_confirmed_pair_is_minted(self, tmp_path: Path):
        de_path, en_path = self._cold_pair(tmp_path)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        # The refusals became one pending mint candidate.
        assert plan.count("mint") == 1
        assert plan.count("refuse") == 0
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_mint == 1
        assert result.deferred == 0
        assert result.has_errors is False
        assert verifier.calls == 1
        # Both halves now carry the SAME (EN-authority) ids on every slide.
        de_ids, en_ids = _slide_order(de_path), _slide_order(en_path)
        assert de_ids == en_ids
        assert all(de_ids) and len(de_ids) == 2  # every slide got a real id

    def test_denied_pair_refuses_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path = self._cold_pair(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        before_en = en_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        verifier = StaticCorrespondenceVerifier(default=False)  # all "no"
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_mint == 0
        assert result.deferred == 1
        assert de_path.read_text(encoding="utf-8") == before_de
        assert en_path.read_text(encoding="utf-8") == before_en

    def test_no_verifier_refuses(self, tmp_path: Path):
        de_path, en_path = self._cold_pair(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        result = apply_plan(plan, judge=None, verifier=None, watermark_cache=None)
        assert result.applied_mint == 0
        assert result.deferred == 1
        assert de_path.read_text(encoding="utf-8") == before_de

    def test_no_provider_keeps_refuse(self, tmp_path: Path):
        de_path, en_path = self._cold_pair(tmp_path)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=False)
        assert plan.count("mint") == 0
        assert plan.count("refuse") == 4  # 2 de + 2 en id-less, both directions

    def test_mismatched_id_pair_keeps_refuse_even_with_provider(self, tmp_path: Path):
        # Both id'd with different ids: never a mint candidate (design §12 — refuse).
        de = _slide("de", "d1", "# ## A") + _slide("de", "d2", "# ## B")
        en = _slide("en", "e1", "# ## A") + _slide("en", "e2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("mint") == 0
        assert plan.count("refuse") == 4

    def test_half_idd_pair_is_adopt_not_mint(self, tmp_path: Path):
        # Half-id'd is the adopt case (3.2), never a mint: it becomes ONE shared-id
        # candidate, but `adopt` (reuse the id'd half's existing ids), not `mint`
        # (fresh ids). Full adopt behavior is covered in TestColdStartAdopt.
        de = _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B")
        en = _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("mint") == 0
        assert plan.count("adopt") == 1
        assert plan.count("refuse") == 0

    def test_verifier_result_is_cached(self, tmp_path: Path):
        # The verdict map is memoized by pair fingerprint: a second resolve over the
        # same pairs short-circuits the model (calls stays 1).
        from clm.slides.sync_apply import _resolve_correspondence
        from clm.slides.sync_recover import SlidePair

        cache = SyncCorrespondenceCache(tmp_path / "clm-llm.sqlite")
        try:
            pairs = [SlidePair("# ## A", "# ## A", "", "", "slide")]
            verifier = StaticCorrespondenceVerifier(default=True)
            first = _resolve_correspondence(verifier, cache, pairs)
            second = _resolve_correspondence(verifier, cache, pairs)
            assert first == {0: True} == second
            assert verifier.calls == 1  # the second resolve hit the cache
        finally:
            cache.close()


class TestColdStartAdopt:
    """A half-id'd cold pair adopts the id'd half's *existing* ids once confirmed (#216 §12, 3.2).

    Distinct from a mint: one half is fully id'd, the other fully id-less, and the
    id-less half adopts the id'd half's ids verbatim (a header stamp — no
    translation, no fresh slug), gated by the same correspondence verifier.
    """

    def _half_idd_pair(self, tmp_path: Path, *, idd: str = "en") -> tuple[Path, Path]:
        # The common 1.8-gate shape: one half assign-ids'd (s1, s2), the twin id-less.
        idless = _slide_idless("__", "# ## A") + _slide_idless("__", "# ## B")
        idd_text = _slide("__", "s1", "# ## A") + _slide("__", "s2", "# ## B")
        if idd == "en":
            return _write_pair(tmp_path, idless.replace("__", "de"), idd_text.replace("__", "en"))
        return _write_pair(tmp_path, idd_text.replace("__", "de"), idless.replace("__", "en"))

    def test_half_idd_becomes_adopt_candidate(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 1
        assert plan.count("mint") == 0
        assert plan.count("refuse") == 0
        adopt = next(p for p in plan.proposals if p.kind == "adopt")
        assert adopt.direction == "en->de"  # EN is the fully-id'd authority
        assert adopt.disposition == "pending"

    def test_confirmed_pair_adopts_authority_ids(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        en_before = en_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 1
        assert result.applied_mint == 0
        assert result.deferred == 0
        assert result.has_errors is False
        assert verifier.calls == 1
        # The id-less DE half adopted EN's EXISTING ids verbatim — NOT fresh slugs
        # (a mint would derive "introduction"/"variables" from the headings).
        assert _slide_order(de_path) == ["s1", "s2"]
        assert _slide_order(en_path) == ["s1", "s2"]
        # Only the id-less half was written; the id'd half is byte-identical.
        assert en_path.read_text(encoding="utf-8") == en_before

    def test_de_authority_is_adopted_onto_en(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path, idd="de")
        de_before = de_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        adopt = next(p for p in plan.proposals if p.kind == "adopt")
        assert adopt.direction == "de->en"  # DE is the authority
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 1
        assert _slide_order(de_path) == ["s1", "s2"]
        assert _slide_order(en_path) == ["s1", "s2"]
        assert de_path.read_text(encoding="utf-8") == de_before  # the id'd half untouched

    def test_denied_pair_refuses_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        before_en = en_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        verifier = StaticCorrespondenceVerifier(default=False)  # all "no"
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 0
        assert result.deferred == 1
        assert de_path.read_text(encoding="utf-8") == before_de
        assert en_path.read_text(encoding="utf-8") == before_en

    def test_no_verifier_refuses(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        result = apply_plan(plan, judge=None, verifier=None, watermark_cache=None)
        assert result.applied_adopt == 0
        assert result.deferred == 1
        assert de_path.read_text(encoding="utf-8") == before_de

    def test_no_provider_keeps_refuse(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=False)
        assert plan.count("adopt") == 0
        assert plan.count("refuse") == 4  # 2 id-less de + 2 id'd en, both directions

    def test_mismatched_ids_never_adopt(self, tmp_path: Path):
        # Both id'd with DIFFERENT ids: not a half-id'd pair → refuse, never adopt.
        de = _slide("de", "d1", "# ## A") + _slide("de", "d2", "# ## B")
        en = _slide("en", "e1", "# ## A") + _slide("en", "e2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 0
        assert plan.count("mint") == 0
        assert plan.count("refuse") == 4

    def test_mixed_authority_keeps_refuse(self, tmp_path: Path):
        # DE id'd on slide A, EN id'd on slide B — inconsistent authority → refuse.
        de = _slide("de", "s1", "# ## A") + _slide_idless("de", "# ## B")
        en = _slide_idless("en", "# ## A") + _slide("en", "s2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 0
        assert plan.count("refuse") == 4

    def test_adopt_advances_watermark_and_second_run_is_noop(self, tmp_path: Path):
        de_path, en_path = self._half_idd_pair(tmp_path)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache, provider_available=True)
            verifier = StaticCorrespondenceVerifier(default=True)
            result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=cache)
            assert result.applied_adopt == 1
            assert result.watermark_recorded is True
            # The watermark recorded the POST-stamp state (both halves now id'd), so a
            # second run sees a synced pair and proposes nothing.
            plan2 = build_sync_plan(
                de_path, en_path, watermark_cache=cache, provider_available=True
            )
            assert plan2.count("adopt") == 0
            assert plan2.is_noop
        finally:
            cache.close()

    def test_adopt_with_voiceover_companion_adopts_group(self, tmp_path: Path):
        # A slide + its voiceover companion: the id'd half carries the slide id on
        # both cells; the id-less twin adopts both, so the group stays paired.
        de = (
            _slide_idless("de", "# ## A")
            + _vo_idless("de", "# Sprechertext A")
            + _slide_idless("de", "# ## B")
        )
        en = (
            _slide("en", "s1", "# ## A")
            + '# %% [markdown] lang="en" tags=["voiceover"] slide_id="s1"\n# Narration A\n'
            + _slide("en", "s2", "# ## B")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 1
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 1
        # The DE voiceover companion adopted the slide's id too (group kept intact).
        assert _cell_for(de_path, "s1", role="voiceover") is not None
        assert _slide_order(de_path) == ["s1", "s2"]

    def test_classifier_error_on_authority_blocks_adopt(self, tmp_path: Path):
        # The authority half has a *duplicated* companion id (slide s1 + TWO voiceovers
        # both s1) → `_resolve_duplicates` raises a "lone duplicated companion" error
        # (plan.has_errors). A bootstrap that stamped s1 onto both id-less DE
        # voiceovers would bake a DUPLICATE id and advance the watermark over the
        # corruption — so a classifier error must block the adopt entirely.
        en = (
            _slide("en", "s1", "# ## A")
            + '# %% [markdown] lang="en" tags=["voiceover"] slide_id="s1"\n# VO1\n'
            + '# %% [markdown] lang="en" tags=["voiceover"] slide_id="s1"\n# VO2\n'
        )
        de = _slide_idless("de", "# ## A") + _vo_idless("de", "# S1") + _vo_idless("de", "# S2")
        de_path, en_path = _write_pair(tmp_path, de, en)
        before_de = de_path.read_text(encoding="utf-8")
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache, provider_available=True)
            assert plan.has_errors is True
            assert plan.count("adopt") == 0  # candidacy bails on a classifier error
            verifier = StaticCorrespondenceVerifier(default=True)
            result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=cache)
            assert result.applied_adopt == 0
            assert de_path.read_text(encoding="utf-8") == before_de  # nothing stamped
            assert result.watermark_recorded is False  # watermark held over the error
        finally:
            cache.close()

    def test_apply_defers_adopt_when_plan_has_errors(self, tmp_path: Path):
        # Defense in depth: even if an `adopt` candidate coexists with a classifier
        # error (here injected after planning), the apply-time short-circuit must defer
        # it and write nothing — mirroring the normal flush gate's `not plan.has_errors`.
        de_path, en_path = self._half_idd_pair(tmp_path)
        before_de = de_path.read_text(encoding="utf-8")
        before_en = en_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 1  # a clean half-id'd pair → an adopt candidate
        plan.issues.append(PlanIssue(severity="error", slide_id=None, reason="injected error"))
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 0
        assert result.deferred == 1
        assert de_path.read_text(encoding="utf-8") == before_de
        assert en_path.read_text(encoding="utf-8") == before_en

    def test_adopt_skips_idless_localized_code_cells(self, tmp_path: Path):
        # An id-less localized CODE cell (role_of None) interleaved between the
        # slides: candidacy treats it as an aligned non-sync pair (both id-less) and
        # the stamp walk skips it, so only the slides adopt ids — the code cell stays
        # id-less, never mis-stamped.
        de = (
            _slide_idless("de", "# ## A")
            + '# %% lang="de"\nx = 1\n'
            + _slide_idless("de", "# ## B")
        )
        en = _slide("en", "s1", "# ## A") + '# %% lang="en"\nx = 1\n' + _slide("en", "s2", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 1
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 1
        assert _slide_order(de_path) == ["s1", "s2"]
        assert en_path.read_text(encoding="utf-8") == en  # the id'd half untouched
        # The id-less localized code cell was NOT stamped — it stays id-less.
        de_code = [
            c
            for c in parse_cells(de_path.read_text(encoding="utf-8"))
            if c.metadata.cell_type == "code" and c.metadata.lang == "de"
        ]
        assert len(de_code) == 1
        assert de_code[0].metadata.slide_id is None

    def test_idd_localized_code_on_one_side_refuses(self, tmp_path: Path):
        # If the code cell is id'd on the authority half but id-less on the twin, the
        # two have different role_of (CODE_ROLE vs None) → the streams are not clean
        # twins → adopt declines and the refusal stands (never a guessed code-id stamp).
        de = (
            _slide_idless("de", "# ## A")
            + '# %% lang="de"\nx = 1\n'
            + _slide_idless("de", "# ## B")
        )
        en = (
            _slide("en", "s1", "# ## A")
            + '# %% lang="en" slide_id="c1"\nx = 1\n'
            + _slide("en", "s2", "# ## B")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        assert plan.count("adopt") == 0  # role mismatch on the code pair → refuse


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
class TestColdStartCommittedPair:
    """A COMMITTED id-less pair bootstraps end-to-end (Issue #225).

    The cold-start minter must serve the existing corpus, not just never-committed
    files: a committed id-less pair resolves to a git-HEAD baseline that carries no
    ids, which is demoted to a true cold start so mint/adopt can run.
    """

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)

    def _commit_pair(self, tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
        de_path, en_path = _write_pair(tmp_path, de, en)
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "config", "user.email", "t@example.com")
        self._git(tmp_path, "config", "user.name", "Test")
        self._git(tmp_path, "add", "-A")
        self._git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
        return de_path, en_path

    def test_committed_idless_pair_mints_onto_both_halves(self, tmp_path: Path):
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## Einleitung") + _slide_idless("de", "# ## Variablen"),
            _slide_idless("en", "# ## Introduction") + _slide_idless("en", "# ## Variables"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.baseline_source == "none"  # demoted from git-head (#225)
        assert plan.count("mint") == 1
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_mint == 1
        assert result.has_errors is False
        # Both committed halves now carry the SAME minted ids — the deck is bootstrapped.
        de_ids, en_ids = _slide_order(de_path), _slide_order(en_path)
        assert de_ids == en_ids
        assert all(de_ids) and len(de_ids) == 2

    def test_committed_half_idd_pair_adopts_onto_idless_half(self, tmp_path: Path):
        de_path, en_path = self._commit_pair(
            tmp_path,
            _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B"),
            _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B"),
        )
        en_before = en_path.read_text(encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.count("adopt") == 1
        verifier = StaticCorrespondenceVerifier(default=True)
        result = apply_plan(plan, judge=None, verifier=verifier, watermark_cache=None)
        assert result.applied_adopt == 1
        # The DE half adopted EN's EXISTING ids verbatim; the EN half is untouched.
        assert _slide_order(de_path) == ["s1", "s2"]
        assert en_path.read_text(encoding="utf-8") == en_before

    def test_committed_partial_overlap_mismatched_does_not_double(self, tmp_path: Path):
        # #226: a committed pair sharing `s1` but with a mismatched-id "B" slide
        # (`d1`/`e1`) is refused, not cross-added, so applying it writes NOTHING and
        # never doubles "B" — even with a translator available.
        de = _slide("de", "s1", "# ## A") + _slide("de", "d1", "# ## B")
        en = _slide("en", "s1", "# ## A") + _slide("en", "e1", "# ## B")
        de_path, en_path = self._commit_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        translator = StaticSlideTranslator(default="# ## X")
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=None)
        assert result.applied_add == 0
        assert result.deferred == 2  # the two mismatched adds refused
        assert de_path.read_text(encoding="utf-8") == de  # "B" not duplicated
        assert en_path.read_text(encoding="utf-8") == en
        assert len(_slide_order(de_path)) == 2
        assert len(_slide_order(en_path)) == 2

    def test_committed_half_idd_pair_does_not_double_without_translator(self, tmp_path: Path):
        # The pre-#225 failure mode: with no provider the committed half-id'd pair fell
        # to the keyed baseline path and emitted both-direction adds → applying them
        # (translate+insert) doubled both decks. It must now refuse and write nothing.
        de = _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B")
        en = _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B")
        de_path, en_path = self._commit_pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, provider_available=False)
        translator = StaticSlideTranslator(default="# ## X")
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=None)
        assert result.applied_add == 0
        assert result.applied_adopt == 0
        assert de_path.read_text(encoding="utf-8") == de  # no duplication
        assert en_path.read_text(encoding="utf-8") == en
        assert len(_slide_order(de_path)) == 2
        assert len(_slide_order(en_path)) == 2


# ---------------------------------------------------------------------------
# Blank-separated decks (realistic spacing): inserts/moves must stay byte-clean
# ---------------------------------------------------------------------------


def _bcell(lang: str, body: str, sid: str | None = None) -> str:
    head = f'# %% [markdown] lang="{lang}" tags=["slide"]'
    if sid is not None:
        head += f' slide_id="{sid}"'
    return f"{head}\n{body}"


def _bjoin(*cells: str) -> str:
    """Join cells with a blank-line separator and a terminal newline."""
    return "\n\n".join(cells) + "\n"


class TestBlankSeparatedDecks:
    def test_add_in_middle_is_byte_clean(self, tmp_path: Path):
        de = _bjoin(_bcell("de", "# ## A", "a"), _bcell("de", "# ## B", "b"))
        en = _bjoin(_bcell("en", "# ## A", "a"), _bcell("en", "# ## B", "b"))
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _bjoin(
                    _bcell("de", "# ## A", "a"),
                    _bcell("de", "# ## Mid"),
                    _bcell("de", "# ## B", "b"),
                ),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(mapping={"# ## Mid": "# ## Middle"})
            apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        expected = _bjoin(
            _bcell("en", "# ## A", "a"),
            _bcell("en", "# ## Middle", "middle"),
            _bcell("en", "# ## B", "b"),
        )
        assert en_path.read_text(encoding="utf-8") == expected

    def test_reorder_is_byte_clean(self, tmp_path: Path):
        slides = [("a", "# ## A"), ("b", "# ## B"), ("c", "# ## C")]
        de = _bjoin(*[_bcell("de", body, sid) for sid, body in slides])
        en = _bjoin(*[_bcell("en", body, sid) for sid, body in slides])
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _bjoin(
                    _bcell("de", "# ## C", "c"),
                    _bcell("de", "# ## A", "a"),
                    _bcell("de", "# ## B", "b"),
                ),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            apply_plan(plan, judge=None, watermark_cache=cache)
        finally:
            cache.close()

        expected = _bjoin(
            _bcell("en", "# ## C", "c"),
            _bcell("en", "# ## A", "a"),
            _bcell("en", "# ## B", "b"),
        )
        assert en_path.read_text(encoding="utf-8") == expected


# ---------------------------------------------------------------------------
# apply_plan — rename (copy-pasted duplicate id)
# ---------------------------------------------------------------------------


class TestApplyRename:
    def test_copy_paste_is_renamed_with_counterpart(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "intro", "# ## Einleitung"),
            _slide("en", "intro", "# ## Introduction"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author copy-pastes the intro slide (keeping its id) and edits the
            # copy — two cells now carry slide_id="intro".
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung")
                + _slide("de", "intro", "# ## Neues Kapitel"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("rename") == 1
            assert not plan.has_errors
            translator = StaticSlideTranslator(mapping={"# ## Neues Kapitel": "# ## New Chapter"})
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_rename == 1
        # Original intro untouched; the copy re-minted on both decks.
        de_intro = _cell_for(de_path, "intro")
        assert de_intro is not None and "Einleitung" in de_intro.content
        de_copy = _cell_for(de_path, "new-chapter")
        en_copy = _cell_for(en_path, "new-chapter")
        assert de_copy is not None and "Neues Kapitel" in de_copy.content
        assert en_copy is not None and "New Chapter" in en_copy.content
        assert _slide_order(en_path) == ["intro", "new-chapter"]
        assert plan2.is_noop  # the duplicate is resolved, baselined, idempotent

    def test_copy_without_translator_is_deferred(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "intro", "# ## Einleitung"),
            _slide("en", "intro", "# ## Introduction"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung")
                + _slide("de", "intro", "# ## Neues Kapitel"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result = apply_plan(plan, judge=None, translator=None, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_rename == 0
        assert result.deferred >= 1
        assert result.watermark_recorded is False

    def test_copied_group_with_identical_companion_is_resolved(self, tmp_path: Path):
        # Copy a slide GROUP (slide + voiceover), edit the copied slide but leave
        # the copied voiceover identical to the original. The whole group must be
        # re-minted — companion included — leaving no duplicate.
        de = _slide("de", "intro", "# ## Einleitung") + _vo("de", "intro", "# Sprechertext")
        en = _slide("en", "intro", "# ## Introduction") + _vo("en", "intro", "# Narration")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung")
                + _vo("de", "intro", "# Sprechertext")
                + _slide("de", "intro", "# ## Neues Kapitel")
                + _vo("de", "intro", "# Sprechertext"),  # copy companion = identical
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert not plan.has_errors
            translator = StaticSlideTranslator(
                mapping={
                    "# ## Neues Kapitel": "# ## New Chapter",
                    "# Sprechertext": "# Narration",
                }
            )
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert not result.has_errors
        for path in (de_path, en_path):
            ids = [
                (c.metadata.slide_id, c.tags[0])
                for c in parse_cells(path.read_text(encoding="utf-8"))
                if c.metadata.slide_id
            ]
            # No duplicate "intro"; the copy group is its own slide+companion.
            assert ids == [
                ("intro", "slide"),
                ("intro", "voiceover"),
                ("new-chapter", "slide"),
                ("new-chapter", "voiceover"),
            ]
        assert plan2.is_noop  # idempotent — the duplicate is gone, not re-detected

    def test_standalone_companion_duplicate_is_an_error(self, tmp_path: Path):
        # Only the voiceover is copy-pasted (the slide is NOT duplicated). There
        # is no copied slide to anchor a new id, so it must error, not corrupt.
        de = _slide("de", "intro", "# ## Einleitung") + _vo("de", "intro", "# Sprechertext")
        en = _slide("en", "intro", "# ## Introduction") + _vo("en", "intro", "# Narration")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung")
                + _vo("de", "intro", "# Sprechertext")
                + _vo("de", "intro", "# Sprechertext zwei"),  # second voiceover, same id
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(default="# whatever")
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert plan.has_errors
        assert plan.count("rename") == 0
        assert result.applied_rename == 0
        assert result.watermark_recorded is False

    def test_identical_slide_edited_companion_remints_the_copy_not_original(self, tmp_path: Path):
        # Slide headings byte-identical, but the copied companion is edited. The
        # COPY (by position) must be re-minted, leaving the ORIGINAL's id and its
        # cross-language pairing intact.
        de = _slide("de", "s", "# ## Titel") + _vo("de", "s", "# VO eins")
        en = _slide("en", "s", "# ## Title") + _vo("en", "s", "# VO one")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "s", "# ## Titel")
                + _vo("de", "s", "# VO eins")
                + _slide("de", "s", "# ## Titel")  # identical heading
                + _vo("de", "s", "# VO zwei"),  # edited companion
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(
                mapping={"# ## Titel": "# ## Title", "# VO zwei": "# VO two"}
            )
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert not result.has_errors
        # The ORIGINAL 's' keeps its companion on BOTH decks (EN pairing intact).
        assert _cell_for(de_path, "s", "voiceover").content.strip() == "# VO eins"
        assert _cell_for(en_path, "s", "voiceover").content.strip() == "# VO one"
        # The copy got a fresh id and carries the edited companion.
        assert "VO zwei" in _cell_for(de_path, "title", "voiceover").content
        assert "VO two" in _cell_for(en_path, "title", "voiceover").content

    def test_unidentifiable_duplicate_errors_without_destructive_remove(self, tmp_path: Path):
        # Two 'a' slides, BOTH edited away from the baseline → original cannot be
        # identified → error. Crucially, the errored key must NOT re-enter the
        # diff as a phantom `remove` that deletes the EN cell.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A one")
                + _slide("de", "a", "# ## A two")
                + _slide("de", "b", "# ## B"),
                encoding="utf-8",
            )
            before_en = en_path.read_text(encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(default="# ## X")
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert plan.has_errors
        assert plan.count("remove") == 0  # no phantom remove for the errored 'a'
        assert result.applied_remove == 0
        assert result.watermark_recorded is False
        assert en_path.read_text(encoding="utf-8") == before_en  # EN untouched

    def test_malformed_copy_companion_is_propagated_consistently(self, tmp_path: Path):
        # A copied group whose companion carries a DIFFERENT id than its slide.
        # The slide renames; the mismatched companion is a cell present on DE
        # only with an id unknown to the baseline, so it is now an *id-carrying
        # add* and is propagated to EN (id-carrying adds used to be out of scope
        # and deferred). The safety property holds the new way: the decks end
        # with a CONSISTENT key set rather than a silent cross-deck divergence.
        de = _slide("de", "intro", "# ## Einleitung") + _vo("de", "intro", "# Sprechertext")
        en = _slide("en", "intro", "# ## Introduction") + _vo("en", "intro", "# Narration")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung")
                + _vo("de", "intro", "# Sprechertext")
                + _slide("de", "intro", "# ## Neues Kapitel")
                + _vo("de", "weird", "# Sprechertext copy"),  # companion id != slide id
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            translator = StaticSlideTranslator(default="# ## X")
            result = apply_plan(plan, judge=None, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        # No silent cross-deck divergence: both decks carry the same sync keys,
        # including the once-orphaned companion now propagated to EN.
        assert not result.has_errors, result.errors
        de_keys = set(_keys_of(de_path, "de"))
        en_keys = set(_keys_of(en_path, "en"))
        assert de_keys == en_keys
        assert ("weird", "voiceover") in en_keys


# ---------------------------------------------------------------------------
# apply_plan — per-cell partial watermark advance (Phase 4 part 2b)
# ---------------------------------------------------------------------------


def _keys_of(path: Path, lang: str) -> list[tuple[str | None, str]]:
    cells = ordered_sync_cells(parse_cells(path.read_text("utf-8")), lang)
    return [(c.slide_id, c.role) for c in cells]


class _MarkerJudge:
    """A judge that returns ``update`` only when the source body contains a
    marker, else the ``in_sync`` (verdict != "update") verdict the real judge
    emits when it decides the target already reflects the source edit."""

    prompt_version = "test"

    def __init__(self, update_markers: set[str], proposed: str) -> None:
        self._markers = update_markers
        self._proposed = proposed

    def propose(self, source_text, target_text, *, source_lang, target_lang):  # noqa: ANN001
        if any(m in source_text for m in self._markers):
            return SyncProposal(verdict="update", proposed_text=self._proposed)
        return SyncProposal(verdict="in_sync", proposed_text="")


class TestPartialWatermarkAdvance:
    def test_reconciled_edit_advances_while_deferred_conflict_resurfaces(self, tmp_path: Path):
        # The headline 2b win: an edit on 'a' applied alongside a deferred
        # conflict on 'b'. The watermark advances PER-CELL — 'a' is banked (no
        # longer re-surfaces) while 'b' (preserved at its pre-conflict baseline)
        # is still detected as a conflict on the next run. No data loss.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # DE 'a' edited (a plain edit); BOTH sides of 'b' edited (a conflict).
            de_path.write_text(
                _slide("de", "a", "# ## A-de2") + _slide("de", "b", "# ## B-de2"),
                encoding="utf-8",
            )
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1 and plan.count("conflict") == 1

            judge = _update_judge("# ## A-en2")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1
        assert result.deferred == 1
        assert result.watermark_recorded is True  # partial advance fired
        # Edit 'a' was banked; only the conflict 'b' is left for the next run.
        assert plan2.count("edit") == 0
        assert plan2.count("conflict") == 1
        assert plan2.proposals[0].slide_id == "b"
        # The conflict's cells were never written (isolated); the edit was.
        assert "# ## A-en2" in en_path.read_text("utf-8")
        assert "# ## B-de2" in de_path.read_text("utf-8")
        assert "# ## B-en2" in en_path.read_text("utf-8")

    def test_skipped_edit_advances_sibling_and_resurfaces(self, tmp_path: Path):
        # Interactive: accept the edit on 'a', skip the edit on 'b'. 'a' banks,
        # 'b' re-surfaces next run (its source-side baseline is preserved).
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A-de2") + _slide("de", "b", "# ## B-de2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 2
            decisions = {
                id(p): (DECISION_APPLY if p.slide_id == "a" else DECISION_SKIP)
                for p in plan.proposals
            }
            judge = _update_judge("# ## A-en2")
            result = apply_plan(plan, judge=judge, watermark_cache=cache, decisions=decisions)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1
        assert result.deferred == 1
        assert result.watermark_recorded is True
        # Only the skipped 'b' edit remains; 'a' was banked.
        assert plan2.count("edit") == 1
        assert plan2.proposals[0].slide_id == "b"

    def test_conflict_only_pass_holds_watermark(self, tmp_path: Path):
        # No reconciled write to bank -> not eligible for a partial advance, so
        # the watermark holds (matches all-or-nothing). Proven with a SEEDED
        # cache so the hold is the applied_edit>0 gate, not the missing-baseline
        # guard.
        de = _slide("de", "a", "# ## A")
        en = _slide("en", "a", "# ## A")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(_slide("de", "a", "# ## A-de2"), encoding="utf-8")
            en_path.write_text(_slide("en", "a", "# ## A-en2"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("conflict") == 1 and plan.count("edit") == 0
            before = {
                lang: cache.get_deck(str(de_path), str(en_path), lang) for lang in ("de", "en")
            }
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            after = {
                lang: cache.get_deck(str(de_path), str(en_path), lang) for lang in ("de", "en")
            }
        finally:
            cache.close()

        assert result.deferred == 1
        assert result.applied_edit == 0
        assert result.watermark_recorded is False
        assert after == before  # baseline untouched

    def test_structural_partial_pass_holds_watermark(self, tmp_path: Path):
        # An edit reconciled + a conflict deferred, but the pass also has an
        # id-less ADD (structural). Structure changed, so the partial advance is
        # NOT eligible; the watermark holds whole.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A-de2")
                + _slide("de", "b", "# ## B-de2")
                + '# %% [markdown] lang="de" tags=["slide"]\n# ## Neu\n',
                encoding="utf-8",
            )
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1
            assert plan.count("conflict") == 1
            assert plan.count("add") == 1  # structural
            judge = _update_judge("# ## A-en2")
            translator = StaticSlideTranslator(mapping={"# ## Neu": "# ## New"})
            result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        assert result.deferred >= 1  # the conflict
        assert result.watermark_recorded is False  # structural -> held

    def test_partial_advance_preserves_positions_and_keys(self, tmp_path: Path):
        # After a partial advance the watermark keeps the same (slide_id, role)
        # structure as the decks (content-only invariant) — no rows dropped or
        # re-ordered.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A-de2") + _slide("de", "b", "# ## B-de2"),
                encoding="utf-8",
            )
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            apply_plan(plan, judge=_update_judge("# ## A-en2"), watermark_cache=cache)
            wm_de = [
                (p, s, r) for (p, s, r, _h, _c) in cache.get_deck(str(de_path), str(en_path), "de")
            ]
        finally:
            cache.close()

        assert wm_de == [(0, "a", "slide"), (1, "b", "slide")]
        assert _keys_of(de_path, "de") == [("a", "slide"), ("b", "slide")]

    def test_in_sync_edit_is_reconciled_partial_path(self, tmp_path: Path):
        # An in_sync verdict is a reconciliation ("the target already reflects the
        # source", per SyncProposal) — NOT a deferral. On a partial pass it banks
        # like an applied edit: it must NOT re-surface (else the judge re-declines
        # it every run forever). Only the true deferral (the conflict) re-surfaces.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B") + _slide("de", "c", "# ## C")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B") + _slide("en", "c", "# ## C")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # DE a,b edited (de->en edits); c edited on BOTH (a conflict).
            de_path.write_text(
                _slide("de", "a", "# ## A-de2")
                + _slide("de", "b", "# ## B-de2")
                + _slide("de", "c", "# ## C-de2"),
                encoding="utf-8",
            )
            en_path.write_text(
                _slide("en", "a", "# ## A")
                + _slide("en", "b", "# ## B")
                + _slide("en", "c", "# ## C-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 2 and plan.count("conflict") == 1
            # 'a' -> update (applied); 'b' -> in_sync (judge reconciles, no write).
            judge = _MarkerJudge({"A-de2"}, "# ## A-en2")
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1  # 'a'
        assert result.in_sync == 1  # 'b' reconciled (no write)
        assert result.deferred == 1  # conflict 'c'
        assert result.watermark_recorded is True  # partial advance fired
        # Both 'a' (applied) and 'b' (in_sync) are banked; only 'c' re-surfaces.
        assert plan2.count("edit") == 0
        assert plan2.count("conflict") == 1
        assert plan2.proposals[0].slide_id == "c"

    def test_in_sync_only_edit_is_reconciled_full_path(self, tmp_path: Path):
        # A pass whose only non-applied work is an in_sync edit (deferred==0) goes
        # the full-advance path — and an in_sync verdict banks there too (it is a
        # reconciliation). The next run is a no-op; the edit does not re-surface.
        de = _slide("de", "a", "# ## A")
        en = _slide("en", "a", "# ## A")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(_slide("de", "a", "# ## A-de2"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1
            judge = _MarkerJudge(set(), "x")  # never matches -> always in_sync
            result = apply_plan(plan, judge=judge, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 0
        assert result.in_sync == 1
        assert result.deferred == 0
        assert result.watermark_recorded is True  # full advance (deferred == 0)
        assert plan2.is_noop  # reconciled -> banked -> does not re-surface

    def test_both_decks_reorder_warning_holds_watermark(self, tmp_path: Path):
        # Regression for the high-sev 2b finding: a both-decks reorder is emitted
        # as a *warning* (no move proposal, not an error). The partial advance
        # must NOT fire (it would bake the new order and lose the "resolve
        # ordering manually" signal); plan.issues forces a hold.
        def deck(lang: str, order: list[str]) -> str:
            return "".join(_slide(lang, s, f"# ## {s.upper()}") for s in order)

        de_path, en_path = _write_pair(
            tmp_path, deck("de", ["a", "b", "d", "e"]), deck("en", ["a", "b", "d", "e"])
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Swap a<->b on BOTH decks (both-decks reorder -> warning, no move);
            # edit 'd' on DE only (applied); 'e' edited on both (conflict).
            de_path.write_text(
                _slide("de", "b", "# ## B")
                + _slide("de", "a", "# ## A")
                + _slide("de", "d", "# ## D-de2")
                + _slide("de", "e", "# ## E-de2"),
                encoding="utf-8",
            )
            en_path.write_text(
                _slide("en", "b", "# ## B")
                + _slide("en", "a", "# ## A")
                + _slide("en", "d", "# ## D")
                + _slide("en", "e", "# ## E-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("move") == 0
            assert plan.issues  # the order-drift warning
            result = apply_plan(plan, judge=_update_judge("# ## D-en2"), watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.watermark_recorded is False  # held, not silently baselined
        assert plan2.issues  # the order warning still surfaces next run

    def test_reorder_warning_holds_full_advance_path(self, tmp_path: Path):
        # Same warning hazard but with NO proposals at all (deferred==0), so the
        # pass would otherwise be `_pass_is_clean` and take the FULL advance. The
        # top-level `not plan.issues` gate must hold it there too — else the new
        # order is silently baselined and the "resolve manually" signal vanishes.
        def deck(lang: str, order: list[str]) -> str:
            return "".join(_slide(lang, s, f"# ## {s.upper()}") for s in order)

        de_path, en_path = _write_pair(
            tmp_path, deck("de", ["a", "b", "c"]), deck("en", ["a", "b", "c"])
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Swap a<->b on BOTH decks, no content change anywhere (both-decks
            # reorder -> warning, no proposals, deferred == 0).
            for path, lang in ((de_path, "de"), (en_path, "en")):
                path.write_text(deck(lang, ["b", "a", "c"]), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert not plan.proposals  # nothing to apply
            assert plan.issues  # order-drift warning
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.deferred == 0  # would be _pass_is_clean but for the issue
        assert result.watermark_recorded is False  # held by the not-plan.issues gate
        assert plan2.issues  # warning re-surfaces

    def test_en_side_skipped_edit_advances_sibling_and_resurfaces(self, tmp_path: Path):
        # Mirror of the DE-side skipped-edit test (review symmetry gap): an
        # en->de skipped edit must also re-surface (both decks' baselines are
        # preserved for un-written cells).
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(
                _slide("en", "a", "# ## A-en2") + _slide("en", "b", "# ## B-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 2
            decisions = {
                id(p): (DECISION_APPLY if p.slide_id == "a" else DECISION_SKIP)
                for p in plan.proposals
            }
            result = apply_plan(
                plan, judge=_update_judge("# ## A-de2"), watermark_cache=cache, decisions=decisions
            )
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.applied_edit == 1
        assert result.deferred == 1
        assert result.watermark_recorded is True
        assert plan2.count("edit") == 1
        assert plan2.proposals[0].slide_id == "b"

    def test_removed_de_edited_en_conflict_holds_watermark(self, tmp_path: Path):
        # A "removed on DE / edited on EN" collision is classified as a CONFLICT
        # (not a remove), so it slips the structural gate. But 'b' is gone from
        # DE's current cells, so the partial advance cannot faithfully preserve
        # it — it must hold the whole watermark, else the conflict is dropped and
        # next run mutates into a phantom add re-creating the removed slide.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # DE: edit 'a' (the reconciled write) + REMOVE 'b'. EN: edit 'b'.
            de_path.write_text(_slide("de", "a", "# ## A-de2"), encoding="utf-8")
            en_path.write_text(
                _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B-en2"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1 and plan.count("conflict") == 1
            assert plan.count("remove") == 0  # the removal is a conflict, not a remove
            result = apply_plan(plan, judge=_update_judge("# ## A-en2"), watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.deferred == 1
        assert result.watermark_recorded is False  # held, not dropped
        conflicts = [p.slide_id for p in plan2.proposals if p.kind == "conflict"]
        assert "b" in conflicts  # 'b' re-surfaces as a conflict
        assert plan2.count("add") == 0  # NOT a phantom re-add of the removed slide

    def test_edited_de_removed_en_conflict_holds_watermark(self, tmp_path: Path):
        # Mirror of the above: removed on EN, edited on DE.
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # DE: edit 'a' (reconciled write) + edit 'b'. EN: REMOVE 'b'.
            de_path.write_text(
                _slide("de", "a", "# ## A-de2") + _slide("de", "b", "# ## B-de2"),
                encoding="utf-8",
            )
            en_path.write_text(_slide("en", "a", "# ## A"), encoding="utf-8")
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("edit") == 1 and plan.count("conflict") == 1
            assert plan.count("remove") == 0
            result = apply_plan(plan, judge=_update_judge("# ## A-en2"), watermark_cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.deferred == 1
        assert result.watermark_recorded is False
        conflicts = [p.slide_id for p in plan2.proposals if p.kind == "conflict"]
        assert "b" in conflicts
        assert plan2.count("add") == 0
