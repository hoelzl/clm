"""Tests for :mod:`clm.slides.sync_apply` (Issue #166, Phase 2 apply engine)."""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_plan import Proposal, SyncPlan, build_sync_plan, ordered_sync_cells
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
            cells=[(c.position, c.slide_id, c.role, c.content_hash) for c in cells],
        )


def _update_judge(text: str) -> StaticSyncJudge:
    return StaticSyncJudge(default_proposal=SyncProposal(verdict="update", proposed_text=text))


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

    def test_move_is_deferred_and_watermark_not_advanced(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
            plan.proposals.append(
                Proposal(kind="move", role="slide", direction="de->en", slide_id="a")
            )
            result = apply_plan(plan, judge=None, watermark_cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.deferred == 1
        assert result.applied == 0
        assert result.watermark_recorded is False
        assert recorded is False  # un-applied move must not be baselined

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
        # judge): the remove still applies; the error is recorded.
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

        assert result.applied_remove == 1
        assert result.has_errors  # the edit had no judge
        en_ids = [
            c.metadata.slide_id
            for c in parse_cells(en_path.read_text("utf-8"))
            if c.metadata.slide_id
        ]
        assert en_ids == ["a"]
