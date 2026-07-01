"""Companion-aware ``clm slides sync`` (issue #501).

``clm slides sync`` was structurally blind to **separated voiceover companion
files** (``voiceover_*.de.py`` / ``voiceover_*.en.py``): editing one companion was
never propagated to the other language and no ``sync`` subcommand reported it. The
feature inlines each half's companion in memory (design
``sync-separated-voiceover-companions.md``) so the existing plan engine sees the
narration like any other cell, and extracts the reconciled projection back into the
companions on write-back. These tests pin the whole contract, grouped by phase:

* **Read** (Phase 1) — a standing separated pair reports **0 changes** (the §5.3
  keystone: the baseline is projected identically), a drifted companion surfaces as
  ``add …/voiceover [translation pending]``, mixed / cross-language / unplaceable
  cells **refuse**, a legacy watermark **demotes**, and a ``voiceover_*`` argument
  resolves to its deck pair.
* **Apply** (Phase 2) — a narration added / edited / removed on one side is
  reconciled into the other companion in one atomic ≤4-file write; an in-sync pair
  writes nothing; a one-sided companion is created at the twin's layout; notes stay
  inline; the deck stays voiceover-free on disk.
* **Bless + ledger** (Phase 3) — ``bless`` records a ``separated`` watermark and the
  consistency ledger fingerprints the projection, so a confirmed narration is
  suppressed while a later drift still surfaces.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_companion import Representation, project_pair
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_report import build_report
from clm.slides.sync_translate import StaticSlideTranslator
from clm.slides.sync_verify import verify_pair
from clm.slides.voiceover_tools import resolve_companion

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **_GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Deck / companion builders — a two-slide split pair whose voiceover lives in a
# separated companion (no inline voiceover in the deck itself).
# ---------------------------------------------------------------------------


def _deck(lang: str) -> str:
    title = {"de": "Einführung", "en": "Introduction"}[lang]
    end = {"de": "Ende", "en": "The End"}[lang]
    return (
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="intro"\n'
        f"#\n# ## {title}\n"
        "\n"
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="ende"\n'
        f"#\n# ## {end}\n"
    )


def _companion(lang: str, *cells: tuple[str, str]) -> str:
    """A companion file of ``(for_slide, narration)`` voiceover cells."""
    out = []
    for for_slide, narration in cells:
        out.append(
            f'# %% [markdown] lang="{lang}" tags=["voiceover"] for_slide="{for_slide}"\n'
            f"#\n# - {narration}\n"
        )
    return "\n".join(out)


def _inline_vo_deck(lang: str) -> str:
    """A deck carrying its intro voiceover *inline* (the mixed / cross-lang probe)."""
    title = {"de": "Einführung", "en": "Introduction"}[lang]
    end = {"de": "Ende", "en": "The End"}[lang]
    inline = {"de": "Inline-Erzählung.", "en": "Inline narration."}[lang]
    return (
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="intro"\n'
        f"#\n# ## {title}\n"
        "\n"
        f'# %% [markdown] lang="{lang}" tags=["voiceover"]\n'
        f"#\n# - {inline}\n"
        "\n"
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="ende"\n'
        f"#\n# ## {end}\n"
    )


class _Pair:
    """A committed split pair in a throwaway git repo, with optional companions."""

    def __init__(self, folder: Path, repo: Path):
        self.folder = folder
        self.repo = repo
        self.de = folder / "slides_x.de.py"
        self.en = folder / "slides_x.en.py"
        self.de_comp = folder / "voiceover_x.de.py"
        self.en_comp = folder / "voiceover_x.en.py"

    def commit(self, msg: str = "c") -> None:
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg)


def _make_pair(
    tmp_path: Path,
    *,
    de_text: str | None = None,
    en_text: str | None = None,
    de_comp: str | None = None,
    en_comp: str | None = None,
    subdir: bool = False,
    commit: bool = True,
) -> _Pair:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    folder = repo / "t"
    folder.mkdir(parents=True, exist_ok=True)
    pair = _Pair(folder, repo)
    pair.de.write_text(de_text if de_text is not None else _deck("de"), encoding="utf-8")
    pair.en.write_text(en_text if en_text is not None else _deck("en"), encoding="utf-8")
    comp_dir = folder / "voiceover" if subdir else folder
    if subdir and (de_comp is not None or en_comp is not None):
        comp_dir.mkdir(exist_ok=True)
        pair.de_comp = comp_dir / "voiceover_x.de.py"
        pair.en_comp = comp_dir / "voiceover_x.en.py"
    if de_comp is not None:
        pair.de_comp.write_text(de_comp, encoding="utf-8")
    if en_comp is not None:
        pair.en_comp.write_text(en_comp, encoding="utf-8")
    if commit:
        pair.commit("init")
    return pair


def _proposals(plan) -> list[tuple[str, str | None, str]]:
    return [(p.kind, p.direction, p.role) for p in plan.proposals]


# ---------------------------------------------------------------------------
# Keystone (§5.3): a standing separated pair reports zero spurious changes.
# ---------------------------------------------------------------------------


class TestSpuriousAddRegression:
    def test_in_sync_separated_pair_is_a_noop(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.proposals == []
        assert not plan.has_errors
        assert plan.companion_aware is True
        assert plan.projected_de_text is not None

    def test_in_sync_against_explicit_baseline_ref(self, tmp_path: Path) -> None:
        # The projection must apply to the explicit `--baseline <ref>` source too.
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        plan = build_sync_plan(pair.de, pair.en, baseline_ref="HEAD")
        assert plan.proposals == []
        assert not plan.has_errors

    def test_in_sync_with_subdir_companion_layout(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            subdir=True,
        )
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.proposals == []
        assert plan.companion_aware is True


# ---------------------------------------------------------------------------
# Headline fix: a drifted companion surfaces as an add [translation pending].
# ---------------------------------------------------------------------------


class TestCompanionDriftSurfaces:
    def test_new_de_narration_reports_add_to_en(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        # Author adds a NEW narration on the DE side only (working tree, uncommitted).
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke fürs Zuschauen.")),
            encoding="utf-8",
        )
        plan = build_sync_plan(pair.de, pair.en)
        assert ("add", "de->en", "voiceover") in _proposals(plan)

    def test_report_excerpt_quotes_the_projected_narration(self, tmp_path: Path) -> None:
        # §5.7 read purity: the excerpt must come from the in-memory inlined
        # projection, never the voiceover-free working tree (which lacks the line).
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke fürs Zuschauen.")),
            encoding="utf-8",
        )
        plan = build_sync_plan(pair.de, pair.en)
        report = build_report(plan, with_excerpts=True)
        excerpts = " ".join(
            item.source_excerpt or "" for item in (*report.assisted, *report.mechanical)
        )
        assert "Danke fürs Zuschauen." in excerpts
        # The raw DE deck on disk never contained that narration line.
        assert "Danke fürs Zuschauen." not in pair.de.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# One-sided companion — propagate (parity with inline voiceover, the core ask).
# ---------------------------------------------------------------------------


class TestOneSidedCompanion:
    def test_de_only_companion_proposes_add_to_en(self, tmp_path: Path) -> None:
        # A half with a companion, the other with NO voiceover, is classified
        # SEPARATED (legal) and — exactly like a one-sided *inline* voiceover —
        # proposes `add de->en [translation pending]`. (Suppressing a *standing*
        # one-sided asymmetry is a separate engine-wide refinement; the current
        # engine propagates for inline too, and Phase 1 keeps companion == inline.)
        pair = _make_pair(tmp_path, de_comp=_companion("de", ("intro", "Willkommen.")))
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.SEPARATED
        plan = build_sync_plan(pair.de, pair.en)
        assert ("add", "de->en", "voiceover") in _proposals(plan)


# ---------------------------------------------------------------------------
# Representation invariants: mixed / cross-language / notes-not-mixed.
# ---------------------------------------------------------------------------


class TestRepresentationRefusals:
    def test_mixed_deck_refuses(self, tmp_path: Path) -> None:
        # DE keeps voiceover BOTH inline and in a companion — a partial split.
        pair = _make_pair(
            tmp_path,
            de_text=_inline_vo_deck("de"),
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            commit=False,
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.MIXED
        assert proj.refusal is not None
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.has_errors
        assert plan.companion_aware is True
        assert plan.proposals == []

    def test_cross_language_asymmetry_refuses(self, tmp_path: Path) -> None:
        # DE separated (companion), EN carries inline voiceover — inconsistent.
        pair = _make_pair(
            tmp_path,
            en_text=_inline_vo_deck("en"),
            de_comp=_companion("de", ("intro", "Willkommen.")),
            commit=False,
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.CROSS_LANGUAGE
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.has_errors

    def test_inline_notes_beside_voiceover_companion_is_not_mixed(self, tmp_path: Path) -> None:
        # The sanctioned steady state (post-#387): notes inline, voiceover in the
        # companion. The mixed predicate is voiceover-ONLY, so this is SEPARATED.
        de_with_notes = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "#\n# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["notes"]\n'
            "#\n# - Eine Sprechernotiz.\n"
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="ende"\n'
            "#\n# ## Ende\n"
        )
        en_with_notes = de_with_notes.replace('lang="de"', 'lang="en"').replace(
            "Eine Sprechernotiz.", "A speaker note."
        )
        pair = _make_pair(
            tmp_path,
            de_text=de_with_notes,
            en_text=en_with_notes,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.SEPARATED
        assert proj.refusal is None


# ---------------------------------------------------------------------------
# Total transform: an unplaceable companion cell refuses (never a silent drop).
# ---------------------------------------------------------------------------


class TestTotalTransform:
    def test_orphaned_companion_cell_refuses(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("ghost", "Für eine gelöschte Folie.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.refusal is not None
        assert "ghost" in proj.refusal
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.has_errors
        assert plan.proposals == []
        # Nothing was dropped: the companion file is untouched on disk.
        assert "ghost" in pair.de_comp.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Representation marker: a legacy voiceover-free watermark demotes to git-HEAD.
# ---------------------------------------------------------------------------


class TestRepresentationMarker:
    def test_get_set_representation_roundtrip(self, tmp_path: Path) -> None:
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        assert cache.get_representation("a", "b") is None  # no marker → legacy plain
        cache.set_representation("a", "b", "separated")
        assert cache.get_representation("a", "b") == "separated"

    def test_legacy_watermark_on_separated_deck_demotes_to_git_head(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        # A legacy (voiceover-free, no representation marker) watermark for the pair.
        cache.put_deck(
            de_path=str(pair.de),
            en_path=str(pair.en),
            lang="de",
            cells=[(0, "intro", "slide", "deadbeef", None)],
        )
        assert cache.has_pair(str(pair.de), str(pair.en))
        plan = build_sync_plan(pair.de, pair.en, watermark_cache=cache)
        # The marker mismatch (stored plain vs current separated) demotes to the
        # projected git-HEAD baseline instead of the voiceover-free watermark.
        assert plan.baseline_source == "git-head"

    def test_plain_pair_still_uses_its_watermark(self, tmp_path: Path) -> None:
        # A plain pair with a legacy watermark matches (plain == plain) — no demotion.
        pair = _make_pair(tmp_path)
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        cache.put_deck(
            de_path=str(pair.de),
            en_path=str(pair.en),
            lang="de",
            cells=[(0, "intro", "slide", "deadbeef", None)],
        )
        plan = build_sync_plan(pair.de, pair.en, watermark_cache=cache)
        assert plan.baseline_source == "watermark"


# ---------------------------------------------------------------------------
# Apply on an in-sync separated pair is a clean no-op; plain pairs are untouched.
# (The full write-back is exercised in TestCompanionApplyWriteback below.)
# ---------------------------------------------------------------------------


class TestApplyGuardAndPlainParity:
    def test_apply_in_sync_separated_pair_writes_nothing(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        before = {p: p.read_text(encoding="utf-8") for p in (pair.de, pair.en, pair.de_comp)}
        plan = build_sync_plan(pair.de, pair.en)
        result = apply_plan(plan, judge=None)
        assert not result.has_errors
        # An in-sync pair changes no files (the empty-plan short-circuit).
        for path, text in before.items():
            assert path.read_text(encoding="utf-8") == text

    def test_plain_pair_is_not_companion_aware(self, tmp_path: Path) -> None:
        pair = _make_pair(tmp_path)
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.companion_aware is False
        assert plan.projected_de_text is None


# ---------------------------------------------------------------------------
# verify_pair over the inlined projection.
# ---------------------------------------------------------------------------


class TestVerifyProjection:
    def test_symmetric_separated_pair_verifies_clean(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        result = verify_pair(pair.de, pair.en)
        assert [v for v in result.violations if v.severity == "error"] == []

    def test_one_sided_companion_is_not_a_structural_error(self, tmp_path: Path) -> None:
        # A one-sided narration is legal bilingual content (a DE-only localized cell),
        # not a structural corruption — `unify_texts` degrades gracefully. Surfacing
        # that *drift* is `report`'s job (the `add de->en` proposal), not verify's; the
        # projection must therefore not manufacture a spurious verify error.
        pair = _make_pair(tmp_path, de_comp=_companion("de", ("intro", "Willkommen.")))
        result = verify_pair(pair.de, pair.en)
        assert [v for v in result.violations if v.severity == "error"] == []


# ---------------------------------------------------------------------------
# CLI: pointing `sync` at a voiceover_* companion resolves its deck pair.
# ---------------------------------------------------------------------------


class TestCompanionArgResolution:
    def test_sibling_companion_resolves_to_deck_pair(self, tmp_path: Path) -> None:
        from clm.cli.commands.slides.sync import _resolve_single_path

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        de, en = _resolve_single_path(pair.de_comp, None)
        assert (de, en) == (pair.de, pair.en)

    def test_en_companion_resolves_ordered_de_first(self, tmp_path: Path) -> None:
        from clm.cli.commands.slides.sync import _resolve_single_path

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        de, en = _resolve_single_path(pair.en_comp, None)
        assert (de, en) == (pair.de, pair.en)

    def test_subdir_companion_resolves_to_deck_one_level_up(self, tmp_path: Path) -> None:
        from clm.cli.commands.slides.sync import _resolve_single_path

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            subdir=True,
        )
        de, en = _resolve_single_path(pair.de_comp, None)
        assert (de, en) == (pair.de, pair.en)

    def test_orphan_companion_errors_clearly(self, tmp_path: Path) -> None:
        import click

        from clm.cli.commands.slides.sync import _resolve_single_path

        # A companion with no deck beside it.
        folder = tmp_path / "t"
        folder.mkdir(parents=True)
        orphan = folder / "voiceover_missing.de.py"
        orphan.write_text(_companion("de", ("intro", "x")), encoding="utf-8")
        with pytest.raises(click.UsageError, match="voiceover companion"):
            _resolve_single_path(orphan, None)


# ---------------------------------------------------------------------------
# Phase 2 — APPLY write-back: reconcile a separated pair into ≤4 files.
# ---------------------------------------------------------------------------


def _errors(result) -> list[str]:
    return list(result.errors)


class TestCompanionApplyWriteback:
    def test_new_narration_translated_into_the_other_companion(self, tmp_path: Path) -> None:
        # The headline fix: a narration added on one side is translated and written
        # into the OTHER language's companion; a second report is clean.
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            encoding="utf-8",
        )
        translator = StaticSlideTranslator(mapping={"#\n# - Danke.": "#\n# - Thanks."})
        plan = build_sync_plan(pair.de, pair.en)
        result = apply_plan(plan, judge=None, translator=translator)
        assert _errors(result) == []
        assert result.applied_add == 1
        assert "Thanks." in pair.en_comp.read_text(encoding="utf-8")
        # Deck stays voiceover-free on disk (the wholly-sidecar invariant).
        assert "voiceover" not in pair.en.read_text(encoding="utf-8")
        # A second report against the reconciled tree is clean.
        assert build_sync_plan(pair.de, pair.en).proposals == []

    def test_deterministic_remove_propagates_without_a_model(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            en_comp=_companion("en", ("intro", "Welcome."), ("ende", "Thanks.")),
        )
        # Author removes the ende narration on DE only.
        pair.de_comp.write_text(_companion("de", ("intro", "Willkommen.")), encoding="utf-8")
        plan = build_sync_plan(pair.de, pair.en)
        result = apply_plan(plan, judge=None)  # no translator needed for a remove
        assert _errors(result) == []
        assert result.applied_remove == 1
        assert "Thanks." not in pair.en_comp.read_text(encoding="utf-8")
        assert build_sync_plan(pair.de, pair.en).proposals == []

    def test_one_sided_companion_created_at_twin_layout(self, tmp_path: Path) -> None:
        pair = _make_pair(tmp_path, de_comp=_companion("de", ("intro", "Willkommen.")))
        assert resolve_companion(pair.en) is None
        translator = StaticSlideTranslator(mapping={"#\n# - Willkommen.": "#\n# - Welcome."})
        plan = build_sync_plan(pair.de, pair.en)
        result = apply_plan(plan, judge=None, translator=translator)
        assert _errors(result) == []
        created = resolve_companion(pair.en)
        assert created is not None
        # Pinned to the DE twin's (sibling) layout, not relocated into a subdir.
        assert created.parent == pair.en.parent
        assert "Welcome." in created.read_text(encoding="utf-8")
        assert build_sync_plan(pair.de, pair.en).proposals == []

    def test_subdir_layout_is_preserved_on_write_back(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            subdir=True,
        )
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            encoding="utf-8",
        )
        translator = StaticSlideTranslator(mapping={"#\n# - Danke.": "#\n# - Thanks."})
        plan = build_sync_plan(pair.de, pair.en)
        apply_plan(plan, judge=None, translator=translator)
        created = resolve_companion(pair.en)
        assert created is not None
        assert created.parent.name == "voiceover"  # stayed in the subdir layout

    def test_clean_apply_records_separated_watermark_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            en_comp=_companion("en", ("intro", "Welcome."), ("ende", "Thanks.")),
        )
        pair.de_comp.write_text(_companion("de", ("intro", "Willkommen.")), encoding="utf-8")
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        plan = build_sync_plan(pair.de, pair.en, watermark_cache=cache)
        result = apply_plan(plan, judge=None, watermark_cache=cache)
        assert result.watermark_recorded
        assert cache.get_representation(str(pair.de), str(pair.en)) == "separated"
        # The recorded separated watermark is used next run (not demoted) and is clean.
        plan2 = build_sync_plan(pair.de, pair.en, watermark_cache=cache)
        assert plan2.baseline_source == "watermark"
        assert plan2.proposals == []

    def test_mixed_pair_still_writes_nothing(self, tmp_path: Path) -> None:
        # A refused (mixed) pair must never be written by apply.
        de_inline = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n#\n# ## X\n\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n#\n# - inline\n\n'
            '# %% [markdown] lang="de" tags=["slide"] slide_id="ende"\n#\n# ## Y\n'
        )
        pair = _make_pair(
            tmp_path,
            de_text=de_inline,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            commit=False,
        )
        before = {p: p.read_text(encoding="utf-8") for p in (pair.de, pair.en, pair.de_comp)}
        plan = build_sync_plan(pair.de, pair.en)
        assert plan.has_errors  # the refusal rides on the plan's blocking issue
        result = apply_plan(plan, judge=None)
        # The plan's blocking issue holds the flush — apply writes nothing.
        assert result.flushed is False
        for path, text in before.items():
            assert path.read_text(encoding="utf-8") == text

    def test_inline_notes_stay_in_the_deck_on_write_back(self, tmp_path: Path) -> None:
        # Voiceover-only extract: a note inline in the deck stays there; only the
        # voiceover round-trips to the companion (the post-#387 steady state).
        de_deck = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n#\n# ## Einführung\n'
            "\n"
            '# %% [markdown] lang="de" tags=["notes"]\n#\n# - Eine Sprechernotiz.\n'
            "\n"
            '# %% [markdown] lang="de" tags=["slide"] slide_id="ende"\n#\n# ## Ende\n'
        )
        en_deck = de_deck.replace('lang="de"', 'lang="en"').replace(
            "Eine Sprechernotiz.", "A speaker note."
        )
        pair = _make_pair(
            tmp_path,
            de_text=de_deck,
            en_text=en_deck,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            encoding="utf-8",
        )
        translator = StaticSlideTranslator(mapping={"#\n# - Danke.": "#\n# - Thanks."})
        plan = build_sync_plan(pair.de, pair.en)
        apply_plan(plan, judge=None, translator=translator)
        de_after = pair.de.read_text(encoding="utf-8")
        assert 'tags=["notes"]' in de_after  # the note is still inline in the deck
        assert "Eine Sprechernotiz." in de_after
        assert "notes" not in pair.de_comp.read_text(encoding="utf-8")

    def test_crash_during_write_back_leaves_files_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        pair.de_comp.write_text(
            _companion("de", ("intro", "Willkommen."), ("ende", "Danke.")),
            encoding="utf-8",
        )
        before = {
            p: p.read_text(encoding="utf-8") for p in (pair.de, pair.en, pair.de_comp, pair.en_comp)
        }

        def _boom(_writes: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("clm.slides.sync_apply.atomic_write_all", _boom)
        translator = StaticSlideTranslator(mapping={"#\n# - Danke.": "#\n# - Thanks."})
        plan = build_sync_plan(pair.de, pair.en)
        with pytest.raises(OSError, match="disk full"):
            apply_plan(plan, judge=None, translator=translator)
        # The whole batch failed before any os.replace — every target is untouched.
        for path, text in before.items():
            assert path.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# Phase 3 — bless + consistency-ledger parity over the projection.
# ---------------------------------------------------------------------------


class TestCompanionBlessAndLedger:
    def test_bless_records_a_separated_watermark(self, tmp_path: Path) -> None:
        from clm.slides.sync_apply import record_baseline

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        # Hand-reconcile a fresh state and bless it as the baseline.
        pair.de_comp.write_text(_companion("de", ("intro", "Hallo.")), encoding="utf-8")
        pair.en_comp.write_text(_companion("en", ("intro", "Hi.")), encoding="utf-8")
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        record_baseline(cache, pair.de, pair.en)
        assert cache.get_representation(str(pair.de), str(pair.en)) == "separated"
        # The blessed (separated) watermark is trusted next run — not demoted — and clean.
        plan = build_sync_plan(pair.de, pair.en, watermark_cache=cache)
        assert plan.baseline_source == "watermark"
        assert plan.proposals == []

    def test_bless_plain_pair_records_plain_representation(self, tmp_path: Path) -> None:
        from clm.slides.sync_apply import record_baseline

        pair = _make_pair(tmp_path)
        cache = SyncWatermarkCache(tmp_path / "wm.db")
        record_baseline(cache, pair.de, pair.en)
        assert cache.get_representation(str(pair.de), str(pair.en)) == "plain"

    def test_ledger_suppresses_a_separated_voiceover(self, tmp_path: Path) -> None:
        from clm.slides import sync_ledger

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        # A one-sided voiceover edit would normally propose an edit against git HEAD.
        pair.de_comp.write_text(
            _companion("de", ("intro", "Hallo und willkommen.")), encoding="utf-8"
        )
        assert any(p.role == "voiceover" for p in build_sync_plan(pair.de, pair.en).proposals)
        # Record the current state as trusted-in-sync (the voiceover lives in the
        # companion, so this only works because the ledger fingerprints the projection).
        rec = sync_ledger.record_pair(pair.de, pair.en, confirmed_by="test")
        assert not rec.refused
        ledger = sync_ledger.load(sync_ledger.ledger_path_for(pair.de))
        plan = build_sync_plan(pair.de, pair.en, ledger=ledger)
        assert [p for p in plan.proposals if p.role == "voiceover"] == []
        assert plan.ledger_skipped >= 1

    def test_ledger_does_not_suppress_a_drifted_voiceover(self, tmp_path: Path) -> None:
        from clm.slides import sync_ledger

        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        sync_ledger.record_pair(pair.de, pair.en, confirmed_by="test")
        # Drift AFTER recording: the ledger must not mask it.
        pair.de_comp.write_text(_companion("de", ("intro", "Etwas anderes.")), encoding="utf-8")
        ledger = sync_ledger.load(sync_ledger.ledger_path_for(pair.de))
        plan = build_sync_plan(pair.de, pair.en, ledger=ledger)
        assert any(p.role == "voiceover" for p in plan.proposals)
