"""`clm slides sync --dry-run` must predict the real (writing) run (#216).

A ``--dry-run`` that reports proposals a writing run silently refuses — or that
exits "changes pending / clean" when the writing run errors and writes nothing —
is worse than useless: an author cannot trust the preview. These tests pin the
contract that *the plan an author previews is what a writing run actually does*.

They assert it the way the CLI itself decides outcomes: ``_plan_exit_code`` is
the dry-run exit code, ``_apply_exit_code`` is the writing-run exit code, and a
**non-failing** static translator/judge is injected so that any divergence is the
plan-vs-apply disagreement under test, never a translation/judge failure.

Fixed in the resolve-then-apply redesign (#216): when adds would flow in *both*
directions with no way to pair the halves — a freshly-split parallel deck (all
id-less), a per-half ``assign-ids`` (mismatched ids), or a half-id'd pair — the
classifier now emits ``refuse`` proposals at plan time instead of bidirectional
adds the apply engine would silently double (id-carrying) or defer with an error
(id-less). So the dry-run lists the refusal (exit 1, "changes pending") and a
writing run defers it (exit 1), writing nothing — the preview and the act agree.
The ``TestColdStartRefusalParity`` cases below pin that agreement.
"""

from __future__ import annotations

from pathlib import Path

from clm.cli.commands.slides.sync import _apply_exit_code, _plan_exit_code
from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import StaticSyncJudge, SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import ApplyResult, apply_plan
from clm.slides.sync_plan import SyncPlan, build_sync_plan, ordered_sync_cells
from clm.slides.sync_translate import StaticSlideTranslator

# Proposal kinds that a writing run is expected to apply automatically. A
# ``conflict`` is excluded: the plan labels it as a conflict and apply defers it
# by design, so a dry-run that shows "1 conflict" honestly predicts the deferral.
AUTO_APPLY_KINDS = ("add", "edit", "retag", "move", "remove", "rename")


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


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


def _forgiving_translator() -> StaticSlideTranslator:
    """A translator that succeeds for *any* body, so a deferral can only mean the
    plan/apply disagreement under test — never a missing translation."""
    return StaticSlideTranslator(default="# ## Translated\n#\n# - point")


def _forgiving_judge() -> StaticSyncJudge:
    return StaticSyncJudge(
        default_proposal=SyncProposal(verdict="update", proposed_text="# ## Translated\n# - point")
    )


def _assert_dry_run_predicts_apply(
    plan: SyncPlan, dry_exit: int, result: ApplyResult, apply_exit: int
) -> None:
    """The dry-run preview of ``plan`` must match what applying ``plan`` did.

    With a non-failing translator/judge:

    1. apply must not surface an error the dry-run plan did not already carry;
    2. every auto-applying proposal the plan promised must actually be applied;
    3. a writing-run *error* (exit 2) must have been foreseen by the dry-run
       (also exit 2) — a dry-run that looks clean/pending (exit 0/1) turning into
       an apply error is exactly the misleading-preview bug this guards against.
    """
    assert result.has_errors == plan.has_errors, (
        f"dry-run plan reported has_errors={plan.has_errors} but apply "
        f"has_errors={result.has_errors}: {result.errors}"
    )
    for kind in AUTO_APPLY_KINDS:
        applied = getattr(result, f"applied_{kind}")
        promised = plan.count(kind)
        assert applied == promised, (
            f"dry-run promised {promised} {kind}(s) but the writing run applied {applied}"
        )
    if apply_exit == 2:
        assert dry_exit == 2, (
            f"the writing run errored (exit 2) on a plan the dry-run reported as "
            f"exit {dry_exit} (not an error): {result.errors}"
        )


def _dry_then_apply(
    de_path: Path,
    en_path: Path,
    *,
    cache: SyncWatermarkCache | None,
    translator: StaticSlideTranslator | None,
    judge: StaticSyncJudge | None,
) -> tuple[SyncPlan, int, ApplyResult, int]:
    """Classify once (the dry-run preview), then apply that same plan (the writing
    run) — exactly the CLI's internal flow — and return both exit codes."""
    plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
    dry_exit = _plan_exit_code(plan)
    de_before = de_path.read_text(encoding="utf-8")
    en_before = en_path.read_text(encoding="utf-8")
    result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    apply_exit = _apply_exit_code(plan, result)
    # A writing run that *errors* must leave both decks byte-identical (#190 item
    # 1: an erroring pass writes neither deck) — so a misleading dry-run is never
    # also a corrupting one.
    if result.has_errors:
        assert de_path.read_text(encoding="utf-8") == de_before
        assert en_path.read_text(encoding="utf-8") == en_before
    return plan, dry_exit, result, apply_exit


# ---------------------------------------------------------------------------
# Parity holds: the dry-run preview matches the writing run
# ---------------------------------------------------------------------------


class TestDryRunMatchesApply:
    def test_noop_is_clean_in_both(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)  # current == baseline -> no-op
            plan, dry_exit, result, apply_exit = _dry_then_apply(
                de_path, en_path, cache=cache, translator=_forgiving_translator(), judge=None
            )
        finally:
            cache.close()
        assert dry_exit == 0  # nothing to do
        assert apply_exit == 0
        _assert_dry_run_predicts_apply(plan, dry_exit, result, apply_exit)

    def test_single_side_idless_add_is_delivered(self, tmp_path: Path):
        de = _slide("de", "a", "# ## A")
        en = _slide("en", "a", "# ## A")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author appends ONE new id-less slide on DE only.
            de_path.write_text(
                _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neues Thema"),
                encoding="utf-8",
            )
            plan, dry_exit, result, apply_exit = _dry_then_apply(
                de_path, en_path, cache=cache, translator=_forgiving_translator(), judge=None
            )
        finally:
            cache.close()
        assert plan.count("add") == 1
        assert dry_exit == 1  # one change pending
        assert apply_exit == 0  # ...and it applied cleanly
        assert result.applied_add == 1
        _assert_dry_run_predicts_apply(plan, dry_exit, result, apply_exit)

    def test_one_side_edit_is_delivered(self, tmp_path: Path):
        de = _slide("de", "intro", "# ## Einleitung")
        en = _slide("en", "intro", "# ## Introduction")
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # Author edits the DE deck; EN unchanged -> a one-directional edit.
            de_path.write_text(
                _slide("de", "intro", "# ## Einleitung\n# - Punkt eins"), encoding="utf-8"
            )
            plan, dry_exit, result, apply_exit = _dry_then_apply(
                de_path, en_path, cache=cache, translator=None, judge=_forgiving_judge()
            )
        finally:
            cache.close()
        assert plan.count("edit") == 1
        assert dry_exit == 1
        assert apply_exit == 0
        assert result.applied_edit == 1
        _assert_dry_run_predicts_apply(plan, dry_exit, result, apply_exit)


# ---------------------------------------------------------------------------
# Parity holds for a both-directions cold-start pair: the resolver refuses (#216)
# ---------------------------------------------------------------------------


class TestColdStartRefusalParity:
    def test_cold_start_parallel_idless_pair(self, tmp_path: Path):
        # A freshly-authored / freshly-split pair: structurally parallel, prose
        # differs by language, ZERO slide_ids, no watermark and (tmp dir) no git.
        de = (
            _slide_idless("de", "# ## Einleitung")
            + _slide_idless("de", "# ## Variablen")
            + _slide_idless("de", "# ## Schleifen")
        )
        en = (
            _slide_idless("en", "# ## Introduction")
            + _slide_idless("en", "# ## Variables")
            + _slide_idless("en", "# ## Loops")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        plan, dry_exit, result, apply_exit = _dry_then_apply(
            de_path, en_path, cache=None, translator=_forgiving_translator(), judge=None
        )
        # The resolver refuses both directions rather than promising 6 adds it
        # cannot apply: 3 de->en + 3 en->de become 6 refusals, nothing to add.
        assert plan.baseline_source == "none"
        assert plan.count("add") == 0
        assert plan.count("refuse") == 6
        assert dry_exit == 1  # "changes pending" (the refusal needs the author)
        assert apply_exit == 1  # ...deferred, not errored
        # Nothing written: both halves are byte-identical after the writing run.
        assert de_path.read_text(encoding="utf-8") == de
        assert en_path.read_text(encoding="utf-8") == en
        _assert_dry_run_predicts_apply(plan, dry_exit, result, apply_exit)

    def test_watermark_baseline_both_sides_idless(self, tmp_path: Path):
        de_path, en_path = _write_pair(
            tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A")
        )
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            # An id-less new slide added on BOTH halves (the isolated #216 case):
            # against a real baseline this still cannot be paired, so it refuses.
            de_after = _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neu")
            en_after = _slide("en", "a", "# ## A") + _slide_idless("en", "# ## New")
            de_path.write_text(de_after, encoding="utf-8")
            en_path.write_text(en_after, encoding="utf-8")
            plan, dry_exit, result, apply_exit = _dry_then_apply(
                de_path, en_path, cache=cache, translator=_forgiving_translator(), judge=None
            )
            assert plan.count("add") == 0
            assert plan.count("refuse") == 2
            assert dry_exit == 1
            assert apply_exit == 1
            assert result.watermark_recorded is False  # held over the refusal
            assert de_path.read_text(encoding="utf-8") == de_after
            assert en_path.read_text(encoding="utf-8") == en_after
            _assert_dry_run_predicts_apply(plan, dry_exit, result, apply_exit)
        finally:
            cache.close()
