"""Tests for :mod:`clm.slides.sync_plan_walker` (Issue #166, Phase 4 part 2).

The walker renders every proposal kind, prompts per proposal, and drives one
atomic :func:`apply_plan`. These tests use synthetic plans for the decision
routing (focused and fast) and ``build_sync_plan`` for a couple of end-to-end
paths (add / move) where the classifier's real output matters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import ApplyResult
from clm.slides.sync_plan import (
    PlanIssue,
    Proposal,
    SyncPlan,
    build_sync_plan,
    ordered_sync_cells,
)
from clm.slides.sync_plan_walker import (
    APPLY,
    AUTO,
    DE_WINS,
    EN_WINS,
    QUIT,
    SKIP,
    PlanWalkResult,
    WalkerOptions,
    render_proposal,
    run_plan_walker,
)
from clm.slides.sync_translate import StaticSlideTranslator

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _vo(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n{body}\n'


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


def _scripted(answers: list[str]):
    """A prompt_fn that returns each answer in order."""
    it: Iterator[str] = iter(answers)

    def prompt(_message: str) -> str:
        return next(it)

    return prompt


def _walk(
    plan: SyncPlan,
    answers: list[str],
    *,
    judge: StaticSyncJudge | None = None,
    translator: StaticSlideTranslator | None = None,
    cache: SyncWatermarkCache | None = None,
) -> tuple[PlanWalkResult, list[str]]:
    lines: list[str] = []
    options = WalkerOptions(prompt_fn=_scripted(answers), echo=lines.append)
    result = run_plan_walker(
        plan,
        judge=judge,
        translator=translator,
        watermark_cache=cache,
        options=options,
    )
    return result, lines


def _synthetic(de_path: Path, en_path: Path, *proposals: Proposal) -> SyncPlan:
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals.extend(proposals)
    return plan


def _slide_ids(path: Path) -> list[str]:
    return [
        c.metadata.slide_id for c in parse_cells(path.read_text("utf-8")) if c.metadata.slide_id
    ]


def _slide_order(path: Path) -> list[str]:
    return [
        c.metadata.slide_id
        for c in parse_cells(path.read_text("utf-8"))
        if c.metadata.is_slide_start and c.metadata.slide_id
    ]


# ---------------------------------------------------------------------------
# render_proposal
# ---------------------------------------------------------------------------


class TestRenderProposal:
    def test_edit_render_has_two_up(self):
        p = Proposal(kind="edit", role="slide", direction="de->en", slide_id="a")
        text = render_proposal(p, {("a", "slide"): "# ## A-de"}, {("a", "slide"): "# ## A-en"})
        assert "edit de->en a/slide" in text
        assert "--- DE (current) ---" in text
        assert "# ## A-de" in text
        assert "# ## A-en" in text

    def test_conflict_render_has_two_up_and_label(self):
        p = Proposal(kind="conflict", role="slide", direction=None, slide_id="a")
        text = render_proposal(p, {("a", "slide"): "# ## DE!"}, {("a", "slide"): "# ## EN!"})
        assert "CONFLICT a/slide" in text
        assert "# ## DE!" in text
        assert "# ## EN!" in text

    def test_remove_render_is_one_line(self):
        p = Proposal(kind="remove", role="slide", direction="de->en", slide_id="a")
        text = render_proposal(p, {}, {})
        assert "remove de->en a/slide" in text
        assert "--- DE" not in text  # structural kinds get no two-up

    def test_add_render_marks_translation_pending(self):
        p = Proposal(
            kind="add", role="slide", direction="de->en", slide_id=None, translation_pending=True
        )
        text = render_proposal(p, {}, {})
        assert "(id-less)/slide" in text
        assert "[translation pending]" in text


# ---------------------------------------------------------------------------
# Gated kinds — apply / skip
# ---------------------------------------------------------------------------


class TestGatedApplySkip:
    def test_apply_edit_writes_and_advances_watermark(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = _synthetic(
                de_path,
                en_path,
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"),
            )
            judge = _update_judge("# ## A-en-updated")
            result, _ = _walk(plan, ["a"], judge=judge, cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.apply_result.applied_edit == 1
        assert result.accepted == 1
        assert result.exit_code == 0
        assert result.apply_result.watermark_recorded is True
        assert recorded is True
        assert "# ## A-en-updated" in en_path.read_text("utf-8")

    def test_skip_edit_defers_and_holds_watermark(self, tmp_path: Path):
        # The core 2a safety property: skipping a proposal must NOT advance the
        # watermark, so the un-applied edit re-surfaces rather than vanishing.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = _synthetic(
                de_path,
                en_path,
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"),
            )
            judge = _update_judge("# ## SHOULD-NOT-BE-WRITTEN")
            result, _ = _walk(plan, ["s"], judge=judge, cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.apply_result.applied_edit == 0
        assert result.apply_result.deferred == 1
        assert result.skipped == 1
        assert result.exit_code == 1
        assert result.apply_result.watermark_recorded is False
        assert recorded is False
        assert "SHOULD-NOT-BE-WRITTEN" not in en_path.read_text("utf-8")

    def test_apply_remove_deletes_target(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        plan = _synthetic(
            de_path,
            en_path,
            Proposal(kind="remove", role="slide", direction="de->en", slide_id="b"),
        )
        result, _ = _walk(plan, ["a"])
        assert result.apply_result.applied_remove == 1
        assert _slide_ids(en_path) == ["a"]

    def test_skip_remove_keeps_target(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        plan = _synthetic(
            de_path,
            en_path,
            Proposal(kind="remove", role="slide", direction="de->en", slide_id="b"),
        )
        result, _ = _walk(plan, ["s"])
        assert result.apply_result.applied_remove == 0
        assert result.apply_result.deferred == 1
        assert _slide_ids(en_path) == ["a", "b"]

    def test_unknown_choice_reprompts(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
        )
        plan = _synthetic(
            de_path, en_path, Proposal(kind="edit", role="slide", direction="de->en", slide_id="a")
        )
        judge = _update_judge("# ## A-en-updated")
        result, lines = _walk(plan, ["x", "a"], judge=judge)
        assert result.apply_result.applied_edit == 1
        assert any("unknown choice" in line for line in lines)

    def test_mixed_accept_then_skip_holds_watermark(self, tmp_path: Path):
        # The decisive safety case: one edit accepted AND one skipped in the same
        # pass. A partial apply must NOT advance the watermark (deferred > 0), so
        # the skipped edit re-surfaces rather than being silently baselined. This
        # is the regression a naive "advance if anything applied" gate would hit.
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A-de") + _slide("de", "b", "# ## B-de"),
            _slide("en", "a", "# ## A-en") + _slide("en", "b", "# ## B-en"),
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = _synthetic(
                de_path,
                en_path,
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"),
                Proposal(kind="edit", role="slide", direction="de->en", slide_id="b"),
            )
            judge = _update_judge("# ## UPDATED")
            result, _ = _walk(plan, ["a", "s"], judge=judge, cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.apply_result.applied_edit == 1  # 'a' applied
        assert result.apply_result.deferred == 1  # 'b' skipped
        assert result.accepted == 1
        assert result.skipped == 1
        assert result.apply_result.watermark_recorded is False  # held despite a partial apply
        assert recorded is False
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Conflicts — de-wins / en-wins / skip
# ---------------------------------------------------------------------------


class TestConflict:
    def _conflict_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        return _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de-edited"), _slide("en", "a", "# ## A-en-edited")
        )

    def test_de_wins_updates_en(self, tmp_path: Path):
        de_path, en_path = self._conflict_pair(tmp_path)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = _synthetic(
                de_path,
                en_path,
                Proposal(kind="conflict", role="slide", direction=None, slide_id="a"),
            )
            judge = _update_judge("# ## A-reconciled-en")
            result, _ = _walk(plan, ["d"], judge=judge, cache=cache)
        finally:
            cache.close()

        assert result.conflicts_resolved == 1
        assert result.apply_result.applied_edit == 1
        assert result.apply_result.deferred == 0
        assert result.exit_code == 0
        assert result.apply_result.watermark_recorded is True
        assert "# ## A-reconciled-en" in en_path.read_text("utf-8")
        assert "# ## A-de-edited" in de_path.read_text("utf-8")  # winner untouched

    def test_en_wins_updates_de(self, tmp_path: Path):
        de_path, en_path = self._conflict_pair(tmp_path)
        plan = _synthetic(
            de_path, en_path, Proposal(kind="conflict", role="slide", direction=None, slide_id="a")
        )
        judge = _update_judge("# ## A-reconciled-de")
        result, _ = _walk(plan, ["e"], judge=judge)
        assert result.conflicts_resolved == 1
        assert "# ## A-reconciled-de" in de_path.read_text("utf-8")
        assert "# ## A-en-edited" in en_path.read_text("utf-8")  # winner untouched

    def test_skip_conflict_touches_nothing(self, tmp_path: Path):
        de_path, en_path = self._conflict_pair(tmp_path)
        before_de = de_path.read_text("utf-8")
        before_en = en_path.read_text("utf-8")
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            plan = _synthetic(
                de_path,
                en_path,
                Proposal(kind="conflict", role="slide", direction=None, slide_id="a"),
            )
            judge = _update_judge("# ## NOPE")
            result, _ = _walk(plan, ["s"], judge=judge, cache=cache)
            recorded = cache.has_pair(str(de_path), str(en_path))
        finally:
            cache.close()

        assert result.conflicts_resolved == 0
        assert result.apply_result.deferred == 1
        assert result.skipped == 1
        assert result.exit_code == 1
        assert result.apply_result.watermark_recorded is False
        assert recorded is False
        assert de_path.read_text("utf-8") == before_de
        assert en_path.read_text("utf-8") == before_en


# ---------------------------------------------------------------------------
# Quit
# ---------------------------------------------------------------------------


class TestQuit:
    def test_quit_defers_remaining_gated(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A-de") + _slide("de", "b", "# ## B-de"),
            _slide("en", "a", "# ## A-en") + _slide("en", "b", "# ## B-en"),
        )
        plan = _synthetic(
            de_path,
            en_path,
            Proposal(kind="edit", role="slide", direction="de->en", slide_id="a"),
            Proposal(kind="edit", role="slide", direction="de->en", slide_id="b"),
        )
        judge = _update_judge("# ## should-not-write")
        result, _ = _walk(plan, ["q"], judge=judge)

        assert result.apply_result.applied_edit == 0
        assert result.apply_result.deferred == 2
        assert result.unvisited == 2
        assert result.exit_code == 1
        assert "should-not-write" not in en_path.read_text("utf-8")


# ---------------------------------------------------------------------------
# Auto-applied kinds — add (end to end via the classifier)
# ---------------------------------------------------------------------------


class TestAutoAdd:
    def test_idless_add_is_auto_applied(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author appends a brand-new id-less slide on DE.
            de_path.write_text(
                _slide("de", "a", "# ## A")
                + '# %% [markdown] lang="de" tags=["slide"]\n# ## Neues Thema\n',
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("add") == 1
            translator = StaticSlideTranslator(mapping={"# ## Neues Thema": "# ## New Topic"})
            # No prompt is consumed for an auto-applied add.
            result, _ = _walk(plan, [], translator=translator, cache=cache)
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
        finally:
            cache.close()

        assert result.auto_applied == 1
        assert result.apply_result.applied_add == 1
        assert result.exit_code == 0
        assert "# ## New Topic" in en_path.read_text("utf-8")
        assert plan2.is_noop  # minted id + advanced watermark -> idempotent

    def test_add_without_translator_errors_exit_2(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "a", "# ## A")
                + '# %% [markdown] lang="de" tags=["slide"]\n# ## Neu\n',
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            result, _ = _walk(plan, [], translator=None, cache=cache)
        finally:
            cache.close()

        assert result.apply_result.has_errors
        assert result.exit_code == 2

    def test_idcarrying_add_is_auto_applied(self, tmp_path: Path):
        # An id-carrying add (a slide_id present on one deck only, unknown to the
        # baseline) is a brand-new slide the author wrote with an id already on
        # it. The walker auto-applies it: translate + insert the twin under the
        # SAME id, reviewed in the resulting git diff.
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B-de"),
            _slide("en", "a", "# ## A"),
        )
        # Cold start (no watermark, git fallback disabled) -> baseline=none, so
        # DE's lone slide "b" surfaces as an id-CARRYING add.
        plan = build_sync_plan(de_path, en_path, allow_git_fallback=False)
        add = next(p for p in plan.proposals if p.kind == "add")
        assert add.slide_id == "b"  # id-carrying, not id-less

        translator = StaticSlideTranslator(mapping={"# ## B-de": "# ## B-en"})  # fully mapped
        result, lines = _walk(plan, [], translator=translator)

        assert result.auto_applied == 1
        assert result.apply_result.applied_add == 1
        assert result.apply_result.deferred == 0
        assert any("will auto-apply" in line for line in lines)
        en_text = en_path.read_text("utf-8")
        assert "# ## B-en" in en_text  # translated counterpart inserted
        assert 'slide_id="b"' in en_text  # under the same id (no minting)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Move routing (skip via walker; apply end-to-end)
# ---------------------------------------------------------------------------


class TestMove:
    def test_apply_move_reorders_target(self, tmp_path: Path):
        de = _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B") + _slide("de", "c", "# ## C")
        en = _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B") + _slide("en", "c", "# ## C")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            de_path.write_text(
                _slide("de", "c", "# ## C")
                + _slide("de", "a", "# ## A")
                + _slide("de", "b", "# ## B"),
                encoding="utf-8",
            )
            plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
            assert plan.count("move") >= 1
            answers = ["a"] * plan.count("move")
            result, _ = _walk(plan, answers, cache=cache)
        finally:
            cache.close()

        assert result.apply_result.applied_move >= 1
        assert _slide_order(en_path) == ["c", "a", "b"]

    def test_skip_move_leaves_order(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        plan = _synthetic(
            de_path,
            en_path,
            Proposal(kind="move", role="slide", direction="de->en", slide_id="a"),
        )
        result, _ = _walk(plan, ["s"])
        assert result.apply_result.applied_move == 0
        assert result.apply_result.deferred == 1
        assert _slide_order(en_path) == ["a", "b"]  # untouched


# ---------------------------------------------------------------------------
# Exit codes & summary
# ---------------------------------------------------------------------------


class TestExitCodesAndSummary:
    def test_edit_without_judge_is_exit_2(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
        )
        plan = _synthetic(
            de_path, en_path, Proposal(kind="edit", role="slide", direction="de->en", slide_id="a")
        )
        result, _ = _walk(plan, ["a"], judge=None)  # accept, but no judge -> error
        assert result.apply_result.has_errors
        assert result.exit_code == 2

    def test_plan_error_is_exit_2(self, tmp_path: Path):
        # A structural plan issue (e.g. an unresolvable duplicate id) is exit 2
        # even when the walk applied nothing.
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
        plan.issues.append(PlanIssue(severity="error", slide_id="a", reason="duplicate"))
        result, _ = _walk(plan, [])
        assert result.exit_code == 2

    def test_noop_plan_is_exit_0(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        plan = _synthetic(de_path, en_path)  # no proposals
        result, _ = _walk(plan, [])
        assert result.exit_code == 0
        assert result.actions == []

    def test_summary_has_two_lines_with_counts(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
        )
        plan = _synthetic(
            de_path, en_path, Proposal(kind="edit", role="slide", direction="de->en", slide_id="a")
        )
        judge = _update_judge("# ## A-en-updated")
        result, _ = _walk(plan, ["a"], judge=judge)
        lines = result.summary()
        assert len(lines) == 2
        assert "1 accepted" in lines[0]  # decisions line
        assert "1 edit" in lines[1]  # outcomes line


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_empty_answer_defaults_to_skip(tmp_path: Path):
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "a", "# ## A-de"), _slide("en", "a", "# ## A-en")
    )
    plan = _synthetic(
        de_path, en_path, Proposal(kind="edit", role="slide", direction="de->en", slide_id="a")
    )
    judge = _update_judge("# ## nope")
    result, _ = _walk(plan, [""], judge=judge)
    assert result.apply_result.applied_edit == 0
    assert result.skipped == 1


def test_apply_result_type(tmp_path: Path):
    de_path, en_path = _write_pair(
        tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
    )
    plan = _synthetic(de_path, en_path)
    result, _ = _walk(plan, [])
    assert isinstance(result.apply_result, ApplyResult)
