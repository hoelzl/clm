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
# Line-ending preservation (issue #132)
# ---------------------------------------------------------------------------


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
