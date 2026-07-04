"""Companion-aware sync plumbing that survived the v3 cutover (issue #501).

The v2 plan engine is gone (#520 Phase 4), but three pieces of the separated
voiceover-companion contract remain load-bearing and are pinned here:

* **Projection** — :func:`clm.slides.sync_companion.project_pair` classifies a
  pair's voiceover representation (plain / separated / mixed / cross-language)
  and inlines a separated pair's companions in memory; mixed / cross-language /
  unplaceable cells **refuse**, never silently drop narration.
* **Verify** — :func:`clm.slides.sync_verify.verify_pair` runs over the inlined
  projection, so a symmetric separated pair verifies clean and a one-sided
  narration is drift, not corruption.
* **CLI resolution** — pointing a sync verb at a ``voiceover_*`` companion
  resolves its deck pair (``_resolve_single_path``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from clm.slides.sync_companion import Representation, project_pair
from clm.slides.sync_verify import verify_pair

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


# ---------------------------------------------------------------------------
# Pure projection: representation classification + in-memory inlining.
# ---------------------------------------------------------------------------


class TestProjection:
    def test_plain_pair_projects_untouched(self, tmp_path: Path) -> None:
        pair = _make_pair(tmp_path)
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.PLAIN
        assert proj.refusal is None

    def test_symmetric_separated_pair_projects_inlined(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.SEPARATED
        assert proj.refusal is None
        # The projected texts carry the narration the deck halves lack on disk.
        assert "Willkommen." in proj.de_text
        assert "Welcome." in proj.en_text
        assert "Willkommen." not in pair.de.read_text(encoding="utf-8")

    def test_subdir_companion_layout_projects_separated(self, tmp_path: Path) -> None:
        pair = _make_pair(
            tmp_path,
            de_comp=_companion("de", ("intro", "Willkommen.")),
            en_comp=_companion("en", ("intro", "Welcome.")),
            subdir=True,
        )
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.SEPARATED
        assert proj.refusal is None

    def test_one_sided_companion_is_separated_not_refused(self, tmp_path: Path) -> None:
        # A half with a companion, the other with NO voiceover, is classified
        # SEPARATED (legal): a one-sided narration is drift, not a broken layout.
        pair = _make_pair(tmp_path, de_comp=_companion("de", ("intro", "Willkommen.")))
        proj = project_pair(pair.de, pair.en, pair.de.read_text(), pair.en.read_text())
        assert proj.representation is Representation.SEPARATED
        assert proj.refusal is None


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
        assert proj.refusal is not None

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
        # Nothing was dropped: the companion file is untouched on disk.
        assert "ghost" in pair.de_comp.read_text(encoding="utf-8")


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
        # that *drift* is `report`'s job, not verify's; the projection must therefore
        # not manufacture a spurious verify error.
        pair = _make_pair(tmp_path, de_comp=_companion("de", ("intro", "Willkommen.")))
        result = verify_pair(pair.de, pair.en)
        assert [v for v in result.violations if v.severity == "error"] == []


# ---------------------------------------------------------------------------
# CLI: pointing a sync verb at a voiceover_* companion resolves its deck pair.
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
