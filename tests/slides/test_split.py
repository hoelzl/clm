"""Tests for :mod:`clm.slides.split` — bilingual ↔ split round-trip.

The round-trip property is the non-negotiable invariant (handover §2.4)::

    unify(*split(text)) == text                # byte-identical
    split(unify(de, en)) == (de, en)           # byte-identical

The suite combines:

- Direct unit cases on tiny synthetic decks (one slide pair, shared cell,
  voiceover, etc.) to make failure diagnosis fast.
- Hypothesis property tests on procedurally generated bilingual decks
  (sticking to the canonical "DE before EN in pairs" interleaving rule).
- Real-fixture round-trip on two PythonCourses ML AZAV decks
  (``slides_010_langchain_basics.py`` — large, mixed shapes;
  ``slides_015_langsmith_tracing.py`` — smaller, dense bullets). Skipped
  cleanly when the course repo is not present (the CLM repo CI does not
  vendor course content).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from clm.slides.split import (
    SplitError,
    UnifyError,
    split_in_file,
    split_text,
    unify_in_file,
    unify_texts,
)
from clm.slides.voiceover_tools import (
    companion_path,
    extract_voiceover,
    inline_voiceover,
)

# ---------------------------------------------------------------------------
# Helpers / building blocks
# ---------------------------------------------------------------------------


HEADER_PREAMBLE = (
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel DE", "Title EN") }}\n\n'
)


def _slide_pair(slug: str, de_title: str, en_title: str) -> str:
    """Build the canonical DE-then-EN slide pair with matching slide_id."""
    return (
        f'# %% [markdown] lang="de" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {de_title}\n#\n# - DE Bullet\n\n"
        f'# %% [markdown] lang="en" tags=["slide"] slide_id="{slug}"\n'
        f"#\n# ## {en_title}\n#\n# - EN Bullet\n\n"
    )


def _voiceover_pair(slug: str) -> str:
    return (
        f'# %% [markdown] lang="de" tags=["voiceover"] slide_id="{slug}"\n'
        f"#\n# Voiceover DE für {slug}\n\n"
        f'# %% [markdown] lang="en" tags=["voiceover"] slide_id="{slug}"\n'
        f"#\n# Voiceover EN for {slug}\n\n"
    )


def _shared_code(name: str = "x") -> str:
    return f'# %% tags=["keep"]\n{name} = 1\n\n'


# ---------------------------------------------------------------------------
# Direct unit cases — one shape per test for diagnosable failure
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_header_only(self):
        text = HEADER_PREAMBLE
        de, en = split_text(text)
        assert 'header_de("Titel DE")' in de
        assert "header_de" not in en
        assert 'header_en("Title EN")' in en
        assert "header_en" not in de
        # The bare ``import header`` line is rewritten alongside the macro
        # call so the split file imports only the macro it actually uses.
        assert "import header_de" in de
        assert "import header_en" in en

    def test_single_slide_pair(self):
        text = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction")
        de, en = split_text(text)
        assert "Einleitung" in de
        assert "Einleitung" not in en
        assert "Introduction" in en
        assert "Introduction" not in de
        assert unify_texts(de, en) == text

    def test_shared_code_appears_in_both(self):
        text = HEADER_PREAMBLE + _shared_code("x") + _slide_pair("a", "Eins", "One")
        de, en = split_text(text)
        assert "x = 1" in de
        assert "x = 1" in en
        assert unify_texts(de, en) == text

    def test_voiceover_routes_per_language(self):
        text = HEADER_PREAMBLE + _slide_pair("a", "Eins", "One") + _voiceover_pair("a")
        de, en = split_text(text)
        assert "Voiceover DE für a" in de
        assert "Voiceover EN for a" in en
        assert "Voiceover DE" not in en
        assert "Voiceover EN" not in de
        assert unify_texts(de, en) == text

    def test_already_split_raises(self):
        text = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"X\") }}\n"
        with pytest.raises(SplitError):
            split_text(text)

    def test_preamble_code_split_still_round_trips(self):
        # split must NOT rewrite preamble code (issue #253): it only warns at the
        # CLI/file layer. The byte-identical round trip must still hold.
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel DE", "Title EN") }}\n'
            "from typing import Iterable\n\n" + _slide_pair("a", "Eins", "One")
        )
        de, en = split_text(text)
        assert "from typing import Iterable" in de
        assert "from typing import Iterable" in en
        assert unify_texts(de, en) == text


class TestUnifyTexts:
    def test_round_trip_header_only(self):
        text = HEADER_PREAMBLE
        de, en = split_text(text)
        assert unify_texts(de, en) == text

    def test_round_trip_mixed(self):
        text = (
            HEADER_PREAMBLE
            + _voiceover_pair("intro")
            + _shared_code("api_key")
            + _slide_pair("what", "Was?", "What?")
            + _voiceover_pair("what")
        )
        de, en = split_text(text)
        assert unify_texts(de, en) == text

    def test_divergent_shared_cell_raises(self):
        text = HEADER_PREAMBLE + _shared_code("x") + _slide_pair("a", "Eins", "One")
        de, en = split_text(text)
        # Tamper with the shared code cell in the DE output only.
        tampered_de = de.replace("x = 1", "x = 2")
        with pytest.raises(UnifyError, match="shared cell"):
            unify_texts(tampered_de, en)

    def test_diverging_preamble_raises(self):
        text = HEADER_PREAMBLE + _slide_pair("a", "Eins", "One")
        de, en = split_text(text)
        with pytest.raises(UnifyError, match="preamble"):
            unify_texts("# leading\n" + de, en)


# ---------------------------------------------------------------------------
# Hypothesis property: round-trip on procedurally generated decks
# ---------------------------------------------------------------------------


@st.composite
def _bilingual_deck(draw):
    """Generate a bilingual deck following the canonical DE-then-EN rule.

    Canonical pattern (what the property tests cover):

    - Slide/voiceover cells always appear as **paired** DE-then-EN with a
      matching ``slide_id``.
    - Shared cells (``# %% tags=["keep"]`` style) have no ``lang`` and
      go to both outputs verbatim.

    Solo language-tagged cells without their language sibling are
    deliberately *not* generated here. After splitting, the relative
    order of a DE-only solo vs. an EN-only solo cannot be recovered from
    the two outputs alone (there is no information left to disambiguate
    "DE before EN" from "EN before DE" in the original). Real slide
    files in the course repo always pair language-tagged cells, so this
    canonical scope matches actual use. The ``unify`` algorithm still
    handles solos best-effort — see the direct unit tests for those
    shapes — but the round-trip property only holds for canonical
    decks.
    """
    n_blocks = draw(st.integers(min_value=0, max_value=6))
    parts: list[str] = [HEADER_PREAMBLE]

    for i in range(n_blocks):
        kind = draw(st.sampled_from(["slide_pair", "voiceover_pair", "shared_code"]))
        slug = f"s{i}"
        if kind == "slide_pair":
            parts.append(_slide_pair(slug, f"Titel {i}", f"Title {i}"))
        elif kind == "voiceover_pair":
            parts.append(_voiceover_pair(slug))
        else:  # shared_code
            parts.append(_shared_code(f"var_{i}"))

    return "".join(parts)


@given(text=_bilingual_deck())
@settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    max_examples=80,
)
def test_round_trip_property(text: str) -> None:
    de, en = split_text(text)
    assert unify_texts(de, en) == text


@given(text=_bilingual_deck())
@settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    max_examples=80,
)
def test_split_then_unify_outputs_match(text: str) -> None:
    """The other half of the round-trip: splitting twice yields the same pair."""
    de1, en1 = split_text(text)
    rt = unify_texts(de1, en1)
    de2, en2 = split_text(rt)
    assert de1 == de2
    assert en1 == en2


# ---------------------------------------------------------------------------
# Real fixtures from the PythonCourses repo (skipped when absent)
# ---------------------------------------------------------------------------


_FIXTURE_DIR = Path(
    "C:/Users/tc/Programming/Python/Courses/Own/PythonCourses/slides/"
    "module_550_ml_azav/topic_050_langchain_simple_chatbot"
)


@pytest.mark.parametrize(
    "fixture_name",
    ["slides_010_langchain_basics.py", "slides_015_langsmith_tracing.py"],
)
def test_real_fixture_round_trip(fixture_name: str) -> None:
    fixture_path = _FIXTURE_DIR / fixture_name
    if not fixture_path.is_file():
        pytest.skip(f"fixture not available: {fixture_path}")
    text = fixture_path.read_text(encoding="utf-8")
    de, en = split_text(text)
    assert unify_texts(de, en) == text


# ---------------------------------------------------------------------------
# File-based driver behaviour: refusal, --force, --dry-run
# ---------------------------------------------------------------------------


class TestSplitInFile:
    def test_writes_companions(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        result = split_in_file(source)
        assert result.wrote is True
        assert (tmp_path / "deck.de.py").is_file()
        assert (tmp_path / "deck.en.py").is_file()

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        result = split_in_file(source, dry_run=True)
        assert result.wrote is False
        assert not (tmp_path / "deck.de.py").exists()
        assert not (tmp_path / "deck.en.py").exists()

    def test_refuses_existing_without_force(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        (tmp_path / "deck.de.py").write_text("placeholder", encoding="utf-8")
        with pytest.raises(SplitError, match="refusing to overwrite"):
            split_in_file(source)

    def test_force_overwrites(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        (tmp_path / "deck.de.py").write_text("placeholder", encoding="utf-8")
        result = split_in_file(source, force=True)
        assert result.wrote is True
        assert (tmp_path / "deck.de.py").read_text(encoding="utf-8") != "placeholder"
        assert (tmp_path / "deck.de.py").read_text(encoding="utf-8") != ""

    def test_preamble_code_emits_warning(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("Titel DE", "Title EN") }}\n'
            "from typing import Iterable\n\n" + _slide_pair("a", "Eins", "One"),
            encoding="utf-8",
        )
        result = split_in_file(source)
        assert result.warnings
        assert any("#253" in w for w in result.warnings)

    def test_no_warning_for_clean_deck(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        result = split_in_file(source)
        assert result.warnings == []

    def test_missing_slide_id_emits_warning(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        idless_pair = (
            '# %% [markdown] lang="de" tags=["slide"]\n#\n# ## Eins\n\n'
            '# %% [markdown] lang="en" tags=["slide"]\n#\n# ## One\n\n'
        )
        source.write_text(HEADER_PREAMBLE + idless_pair, encoding="utf-8")
        result = split_in_file(source)
        assert any("#255" in w for w in result.warnings)
        assert any("assign-ids" in w for w in result.warnings)
        # The warning never blocks — the split is still written.
        assert result.wrote is True
        assert (tmp_path / "deck.de.py").is_file()

    def test_missing_slide_id_warning_in_dry_run(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        idless = '# %% [markdown] lang="de" tags=["subslide"]\n#\n# ## Eins\n\n'
        source.write_text(
            HEADER_PREAMBLE + _slide_pair("a", "Eins", "One") + idless, encoding="utf-8"
        )
        result = split_in_file(source, dry_run=True)
        assert result.wrote is False
        assert any("#255" in w for w in result.warnings)

    def test_no_warning_for_idless_code_cells(self, tmp_path: Path) -> None:
        # Bare lang-tagged code cells legitimately carry no slide_id (the
        # validator's rule covers slide/subslide cells only) — no warning.
        source = tmp_path / "deck.py"
        idless_code = '# %% lang="de"\nx = 1\n\n# %% lang="en"\nx = 1\n\n'
        source.write_text(
            HEADER_PREAMBLE + _slide_pair("a", "Eins", "One") + idless_code,
            encoding="utf-8",
        )
        result = split_in_file(source)
        assert result.warnings == []


class TestUnifyInFile:
    def test_writes_target(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        split_in_file(source)
        target = tmp_path / "unified.py"
        result = unify_in_file(tmp_path / "deck.de.py", tmp_path / "deck.en.py", target=target)
        assert result.wrote is True
        assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    def test_default_target_is_bare_basename(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        original = source.read_text(encoding="utf-8")
        split_in_file(source)
        # The original ``deck.py`` exists, so unifying back must refuse without --force.
        with pytest.raises(UnifyError, match="refusing to overwrite"):
            unify_in_file(tmp_path / "deck.de.py", tmp_path / "deck.en.py")
        # With --force, the bilingual file is rewritten byte-identically.
        result = unify_in_file(tmp_path / "deck.de.py", tmp_path / "deck.en.py", force=True)
        assert result.wrote is True
        assert source.read_text(encoding="utf-8") == original

    def test_basename_mismatch_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.de.py").write_text(HEADER_PREAMBLE, encoding="utf-8")
        (tmp_path / "b.en.py").write_text(HEADER_PREAMBLE, encoding="utf-8")
        with pytest.raises(UnifyError, match="basename"):
            unify_in_file(tmp_path / "a.de.py", tmp_path / "b.en.py")


# ---------------------------------------------------------------------------
# Voiceover companion split/unify in lockstep (hardening 2026-06)
#
# A slide file may have a sibling ``voiceover_*.py`` companion. Splitting the
# deck without the companion orphans the narration (the build no longer finds a
# companion next to either split half). ``split`` must split the companion too;
# ``unify`` must recombine it — both byte-identically, preserving
# ``for_slide`` / ``vo_anchor``.
# ---------------------------------------------------------------------------


def _deck_with_voiceover() -> str:
    """Bilingual deck with interleaved DE/EN voiceover, the extract input."""
    return (
        HEADER_PREAMBLE
        + _slide_pair("intro", "Einleitung", "Introduction")
        + _voiceover_pair("intro")
        + _slide_pair("setup", "Aufbau", "Setup")
        + _voiceover_pair("setup")
    )


class TestCompanionSplit:
    def test_splits_sibling_companion(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")  # -> voiceover_demo.py (bilingual)
        assert (tmp_path / "voiceover_demo.py").is_file()

        result = split_in_file(deck)

        de_comp = tmp_path / "voiceover_demo.de.py"
        en_comp = tmp_path / "voiceover_demo.en.py"
        assert de_comp.is_file()
        assert en_comp.is_file()
        assert result.de_companion == str(de_comp)
        assert result.en_companion == str(en_comp)
        assert result.source_companion == str(tmp_path / "voiceover_demo.py")

        de_text = de_comp.read_text(encoding="utf-8")
        en_text = en_comp.read_text(encoding="utf-8")
        # Each half carries only its own language's narration...
        assert 'lang="de"' in de_text and 'lang="en"' not in de_text
        assert 'lang="en"' in en_text and 'lang="de"' not in en_text
        # ...with the author-only positional attributes preserved verbatim.
        assert 'for_slide="intro"' in de_text and 'for_slide="setup"' in de_text
        assert 'for_slide="intro"' in en_text and 'for_slide="setup"' in en_text
        assert "vo_anchor=" in de_text and "vo_anchor=" in en_text

    def test_companion_round_trips_via_unify_texts(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        comp_before = (tmp_path / "voiceover_demo.py").read_text(encoding="utf-8")

        split_in_file(deck)

        de_text = (tmp_path / "voiceover_demo.de.py").read_text(encoding="utf-8")
        en_text = (tmp_path / "voiceover_demo.en.py").read_text(encoding="utf-8")
        assert unify_texts(de_text, en_text) == comp_before

    def test_no_companion_creates_no_voiceover_files(self, tmp_path: Path) -> None:
        # A deck with no sibling companion must not spawn empty voiceover files.
        deck = tmp_path / "slides_demo.py"
        deck.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        result = split_in_file(deck)
        assert result.de_companion is None
        assert result.en_companion is None
        assert list(tmp_path.glob("voiceover_*.py")) == []

    def test_refuses_when_companion_half_exists(self, tmp_path: Path) -> None:
        # The deck halves do not exist yet, but a companion half does: split must
        # still refuse without --force (atomic — nothing is written).
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        de_comp = tmp_path / "voiceover_demo.de.py"
        de_comp.write_text("placeholder", encoding="utf-8")
        with pytest.raises(SplitError, match="voiceover_demo.de.py"):
            split_in_file(deck)
        # Atomic: the deck halves were not written either.
        assert not (tmp_path / "slides_demo.de.py").exists()
        assert de_comp.read_text(encoding="utf-8") == "placeholder"

    def test_force_overwrites_companion_half(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        (tmp_path / "voiceover_demo.de.py").write_text("placeholder", encoding="utf-8")
        split_in_file(deck, force=True)
        assert (tmp_path / "voiceover_demo.de.py").read_text(encoding="utf-8") != "placeholder"

    def test_dry_run_writes_no_companion(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        result = split_in_file(deck, dry_run=True)
        assert result.de_companion == str(tmp_path / "voiceover_demo.de.py")
        assert not (tmp_path / "voiceover_demo.de.py").exists()
        assert not (tmp_path / "voiceover_demo.en.py").exists()

    def test_companion_halves_use_lf(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        split_in_file(deck)
        assert b"\r\n" not in (tmp_path / "voiceover_demo.de.py").read_bytes()
        assert b"\r\n" not in (tmp_path / "voiceover_demo.en.py").read_bytes()

    def test_write_failure_leaves_no_partial_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The common write failure (disk full) happens during the temp phase,
        # before any real target is replaced — so a failed split must leave NO
        # deck half, NO companion half, and no stray .tmp files. This is the
        # anti-orphan guarantee the companion seam exists to provide.
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck)

        real_write = Path.write_text
        calls = {"n": 0}

        def flaky(self: Path, *args: object, **kwargs: object) -> int:
            if self.name.endswith(".tmp"):
                calls["n"] += 1
                if calls["n"] == 3:  # the first companion half's temp
                    raise OSError("simulated disk full")
            return real_write(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "write_text", flaky)
        with pytest.raises(OSError, match="simulated disk full"):
            split_in_file(deck)

        for name in (
            "slides_demo.de.py",
            "slides_demo.en.py",
            "voiceover_demo.de.py",
            "voiceover_demo.en.py",
        ):
            assert not (tmp_path / name).exists()
        assert list(tmp_path.glob("*.tmp")) == []


class TestCompanionUnify:
    def test_recombines_companions(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        comp_before = (tmp_path / "voiceover_demo.py").read_text(encoding="utf-8")
        deck_extracted = deck.read_text(encoding="utf-8")
        split_in_file(deck)

        # Unify the deck halves back; the companions recombine in lockstep.
        target = tmp_path / "rebuilt.py"
        result = unify_in_file(
            tmp_path / "slides_demo.de.py",
            tmp_path / "slides_demo.en.py",
            target=target,
        )
        assert result.target_companion == str(tmp_path / "voiceover_rebuilt.py")
        assert target.read_text(encoding="utf-8") == deck_extracted
        assert (tmp_path / "voiceover_rebuilt.py").read_text(encoding="utf-8") == comp_before

    def test_one_companion_half_is_not_dropped(self, tmp_path: Path) -> None:
        # Degenerate state: only the DE companion half exists (the EN half was
        # deleted, or only DE narration was authored). Unify must NOT silently
        # orphan it — the missing half is treated as empty and the present
        # narration lands in the bilingual companion.
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        split_in_file(deck)
        (tmp_path / "voiceover_demo.en.py").unlink()  # drop the EN half

        result = unify_in_file(
            tmp_path / "slides_demo.de.py",
            tmp_path / "slides_demo.en.py",
            target=tmp_path / "rebuilt.py",
        )
        rebuilt_comp = tmp_path / "voiceover_rebuilt.py"
        assert result.target_companion == str(rebuilt_comp)
        text = rebuilt_comp.read_text(encoding="utf-8")
        assert "Voiceover DE für intro" in text
        assert 'lang="en"' not in text

    def test_no_companion_halves_no_target_companion(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        split_in_file(deck)
        result = unify_in_file(
            tmp_path / "slides_demo.de.py",
            tmp_path / "slides_demo.en.py",
            target=tmp_path / "rebuilt.py",
        )
        assert result.target_companion is None
        assert not (tmp_path / "voiceover_rebuilt.py").exists()

    def test_refuses_existing_companion_target(self, tmp_path: Path) -> None:
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")
        extract_voiceover(deck, layout="sibling")
        split_in_file(deck)
        # Aim unify at a fresh deck target (does NOT exist) whose companion
        # target DOES exist — proving the refusal is driven by the companion
        # alone, not the deck.
        target = tmp_path / "fresh.py"
        (tmp_path / "voiceover_fresh.py").write_text("placeholder", encoding="utf-8")
        with pytest.raises(UnifyError, match="voiceover_fresh.py"):
            unify_in_file(
                tmp_path / "slides_demo.de.py",
                tmp_path / "slides_demo.en.py",
                target=target,
                force=False,
            )
        assert not target.exists()  # atomic: deck target not written either

    def test_full_round_trip_extract_split_unify_inline(self, tmp_path: Path) -> None:
        # The complete cross-command seam: a bilingual deck goes
        # extract → split → unify → inline with no orphaned narration and no
        # data loss. The deck + companion reconstruct byte-identically through
        # split/unify; the final inline is a content check, not byte-identity —
        # bilingual extract↔inline positioning is a separate seam (the
        # single-language inline IS byte-identical: see the harness
        # ``extract-inline-round-trip`` row).
        deck = tmp_path / "slides_demo.py"
        deck.write_text(_deck_with_voiceover(), encoding="utf-8")

        extract_voiceover(deck, layout="sibling")
        deck_extracted = deck.read_text(encoding="utf-8")
        comp_before = (tmp_path / "voiceover_demo.py").read_text(encoding="utf-8")

        split_in_file(deck)
        # Recombine the split halves over the original deck/companion paths.
        unify_in_file(tmp_path / "slides_demo.de.py", tmp_path / "slides_demo.en.py", force=True)

        # Deck and companion are reconstructed byte-identically; the companion is
        # back next to the deck (never orphaned).
        assert deck.read_text(encoding="utf-8") == deck_extracted
        assert (tmp_path / "voiceover_demo.py").read_text(encoding="utf-8") == comp_before

        # Inline consumes the companion and restores every narration cell.
        inline_voiceover(deck)
        merged = deck.read_text(encoding="utf-8")
        assert "Voiceover DE für intro" in merged
        assert "Voiceover EN for setup" in merged
        assert "for_slide=" not in merged  # author-only attrs stripped on inline
        assert not (tmp_path / "voiceover_demo.py").exists()


class TestLineEndingsAreLF:
    """Split/unify outputs must use LF on disk on all platforms.

    Course repositories pin ``* text=auto eol=lf`` in ``.gitattributes``.
    ``Path.write_text`` without ``newline="\\n"`` translates every ``\\n``
    to ``os.linesep`` (``\\r\\n`` on Windows), producing spurious diffs and
    breaking byte-equivalence gates. Assert in binary mode so universal-
    newlines normalisation cannot mask the regression.
    """

    def test_split_writes_lf_only(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        split_in_file(source)
        de_bytes = (tmp_path / "deck.de.py").read_bytes()
        en_bytes = (tmp_path / "deck.en.py").read_bytes()
        assert b"\r\n" not in de_bytes
        assert b"\r\n" not in en_bytes

    def test_unify_writes_lf_only(self, tmp_path: Path) -> None:
        source = tmp_path / "deck.py"
        source.write_text(HEADER_PREAMBLE + _slide_pair("a", "Eins", "One"), encoding="utf-8")
        split_in_file(source)
        target = tmp_path / "unified.py"
        unify_in_file(tmp_path / "deck.de.py", tmp_path / "deck.en.py", target=target)
        assert b"\r\n" not in target.read_bytes()
