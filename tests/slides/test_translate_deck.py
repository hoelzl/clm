"""Tests for :mod:`clm.slides.translate_deck` — full-deck translation engine.

The engine synthesizes the other-language split half of a single-language deck
(Issue #232, Phase 1). The load-bearing correctness invariant is that the
generated ``(de, en)`` pair round-trips through the canonical split/unify
machinery — ``split(unify(de, en)) == (de, en)`` — i.e. the new half pairs
cleanly, keeps shared cells byte-identical and carries matching slide_ids in
order. On top of that we check the *content* (the right language survives in
each half) and pin exact byte output on decks whose structure is naturally
trailing-symmetric (header-only or shared-cell-terminated).

Note: split itself produces trailing-blank-*asymmetric* halves — the cell that
ends the bilingual source carries an extra EOF blank that lands on only one
side. So a generated half (which mirrors the *source* half's spacing) does not
in general byte-match the other half of an arbitrary bilingual deck; it is its
own self-consistent, round-tripping file. That is correct: when bootstrapping
there is no pre-existing other half to match.

Everything is offline: the engine depends only on the ``SlideTranslator``
protocol, driven here by ``StaticSlideTranslator``.
"""

from __future__ import annotations

import pytest

from clm.slides.raw_cells import split_cells
from clm.slides.split import split_text, unify_texts
from clm.slides.sync_translate import StaticSlideTranslator, TranslationError
from clm.slides.translate_deck import (
    TranslateDeckError,
    translate_deck_text,
)

# ---------------------------------------------------------------------------
# Fixtures / building blocks (mirroring tests/slides/test_split.py)
# ---------------------------------------------------------------------------


HEADER_PREAMBLE = (
    '# j2 from \'macros.j2\' import header\n# {{ header("Titel DE", "Title EN") }}\n\n'
)
TITLES = {"Titel DE": "Title EN"}


def _slide_pair(slug: str, de_title: str, en_title: str) -> str:
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


def _localized_code_pair(slug: str, de: str, en: str) -> str:
    return f'# %% lang="de" slide_id="{slug}"\n{de}\n\n# %% lang="en" slide_id="{slug}"\n{en}\n\n'


def _idless_code_pair(de: str, en: str) -> str:
    """A localized code pair with NO slide_id (the role_of-is-None case)."""
    return f'# %% lang="de"\n{de}\n\n# %% lang="en"\n{en}\n\n'


def _shared_code(name: str = "x") -> str:
    return f'# %% tags=["keep"]\n{name} = 1\n\n'


def _localized_bodies(text: str) -> list[str]:
    """The ``rstrip``-ed bodies of the lang-tagged cells, in order."""
    _, cells = split_cells(text)
    return [c.body.rstrip("\n") for c in cells if c.metadata.lang is not None]


def _mirror_translator(de_text: str, en_text: str) -> StaticSlideTranslator:
    """A translator that turns each DE localized body/title into its EN twin.

    Built straight from the canonical split halves, so feeding it the DE half
    regenerates the EN content exactly.
    """
    mapping = dict(zip(_localized_bodies(de_text), _localized_bodies(en_text)))
    mapping.update(TITLES)
    return StaticSlideTranslator(mapping=mapping)


def _reverse_translator(de_text: str, en_text: str) -> StaticSlideTranslator:
    """The EN -> DE mirror (for testing the reverse direction)."""
    mapping = dict(zip(_localized_bodies(en_text), _localized_bodies(de_text)))
    mapping["Title EN"] = "Titel DE"
    return StaticSlideTranslator(mapping=mapping)


class _RoleRecorder:
    """A translator that records each ``(source_body, role)`` and echoes the body.

    Echoing the source body unchanged keeps the generated pair structurally valid
    (it round-trips), so the engine runs to completion and we can inspect which
    ``role`` each cell — especially the header title — was translated under.
    """

    prompt_version = "rec"
    prog_lang = "python"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append((source_body, role))
        return source_body


def _split(text: str) -> tuple[str, str]:
    de, en = split_text(text)
    # Sanity: the fixture itself round-trips, so any later inequality is the
    # engine's doing, not a malformed fixture.
    assert unify_texts(de, en) == text
    return de, en


def _assert_valid_pair(de_text: str, en_text: str) -> None:
    """The generated pair must round-trip like any real split pair."""
    assert split_text(unify_texts(de_text, en_text)) == (de_text, en_text)


# ---------------------------------------------------------------------------
# Happy paths — a valid, correctly-translated other half
# ---------------------------------------------------------------------------


class TestTranslateDeckText:
    def test_single_slide_pair(self):
        text = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction")
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert "Introduction" in result.target_text
        assert "Einleitung" not in result.target_text
        assert 'header_en("Title EN")' in result.target_text
        assert "header_de" not in result.target_text
        assert result.translated_count == 1

    def test_reverse_direction_en_to_de(self):
        text = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction")
        de, en = _split(text)
        result = translate_deck_text(
            en, source_lang="en", target_lang="de", translator=_reverse_translator(de, en)
        )
        # Generated half is the DE side; it must pair with the EN source.
        _assert_valid_pair(result.target_text, en)
        assert "Einleitung" in result.target_text
        assert "Introduction" not in result.target_text
        assert "header_en" not in result.target_text

    def test_shared_code_is_copied_not_translated(self):
        text = HEADER_PREAMBLE + _shared_code("x") + _slide_pair("a", "Eins", "One")
        de, en = _split(text)
        # The mirror translator has NO mapping for "x = 1"; if the engine tried
        # to translate the shared cell, StaticSlideTranslator would raise.
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert "x = 1" in result.target_text
        assert result.copied_count == 1  # the shared code cell

    def test_localized_code_pair_with_id(self):
        text = HEADER_PREAMBLE + _localized_code_pair("c1", 'print("Hallo")', 'print("Hello")')
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert 'print("Hello")' in result.target_text
        assert 'print("Hallo")' not in result.target_text
        assert result.translated_count == 1

    def test_idless_localized_code_is_translated(self):
        # role_of() is None for a localized id-less code cell, but it carries
        # lang= and so MUST be translated — the gate is lang, not role_of.
        text = HEADER_PREAMBLE + _idless_code_pair('greet("Welt")', 'greet("World")')
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert 'greet("World")' in result.target_text
        assert 'greet("Welt")' not in result.target_text
        assert result.translated_count == 1

    def test_voiceover_cells_are_translated(self):
        text = HEADER_PREAMBLE + _slide_pair("a", "Eins", "One") + _voiceover_pair("a")
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert "Voiceover EN for a" in result.target_text
        assert "Voiceover DE" not in result.target_text

    def test_mixed_multi_shape_deck(self):
        text = (
            HEADER_PREAMBLE
            + _shared_code("setup")
            + _slide_pair("a", "Eins", "One")
            + _localized_code_pair("c1", 'print("Hallo")', 'print("Hello")')
            + _idless_code_pair("y = 2  # zwei", "y = 2  # two")
            + _slide_pair("b", "Zwei", "Two")
            + _voiceover_pair("b")
        )
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert result.translated_count == 5  # 2 slides + 2 code + 1 voiceover
        assert result.copied_count == 1  # shared setup code
        assert result.header_translated is True
        assert "Einleitung" not in result.target_text  # nothing German left in slides

    def test_deck_without_header_macro(self):
        # A plain single-language deck that never adopted the bilingual header
        # convention still translates; there is simply no header to rewrite.
        text = _slide_pair("only", "Allein", "Alone")
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        _assert_valid_pair(de, result.target_text)
        assert result.header_translated is False
        assert "Alone" in result.target_text

    def test_header_title_uses_title_role(self):
        # The header title must be translated via the dedicated "title" role, not
        # the markdown prose role — otherwise the model adds a stray "# " prefix
        # and leaves the title untranslated. See sync_translate._TITLE_SYSTEM_PROMPT.
        text = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction")
        de, _en = _split(text)
        rec = _RoleRecorder()
        translate_deck_text(de, source_lang="de", target_lang="en", translator=rec)
        roles_by_body = dict(rec.calls)
        assert roles_by_body["Titel DE"] == "title"
        # A slide body still goes through a prose role, never "title".
        assert all(role != "title" for body, role in rec.calls if body != "Titel DE")


# ---------------------------------------------------------------------------
# Exact byte output — on trailing-symmetric decks the generated half equals
# what split would have produced for the other side.
# ---------------------------------------------------------------------------


class TestByteExactOutput:
    def test_header_only(self):
        de, en = _split(HEADER_PREAMBLE)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        assert result.target_text == en
        assert result.header_translated is True

    def test_empty_title_is_not_translated(self):
        text = '# j2 from \'macros.j2\' import header\n# {{ header("", "") }}\n\n'
        de, en = _split(text)
        # An empty title must not be sent to the translator (no mapping for "").
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=StaticSlideTranslator()
        )
        assert result.target_text == en

    def test_slide_then_shared_terminator(self):
        # Ending on a shared cell makes the two halves trailing-symmetric, so
        # the generated EN half byte-matches split's EN half exactly.
        text = (
            HEADER_PREAMBLE
            + _slide_pair("intro", "Einleitung", "Introduction")
            + _shared_code("end")
        )
        de, en = _split(text)
        result = translate_deck_text(
            de, source_lang="de", target_lang="en", translator=_mirror_translator(de, en)
        )
        assert result.target_text == en

    def test_reverse_byte_exact(self):
        text = (
            HEADER_PREAMBLE
            + _slide_pair("intro", "Einleitung", "Introduction")
            + _shared_code("end")
        )
        de, en = _split(text)
        result = translate_deck_text(
            en, source_lang="en", target_lang="de", translator=_reverse_translator(de, en)
        )
        assert result.target_text == de


# ---------------------------------------------------------------------------
# Error paths — never write a malformed or incomplete half
# ---------------------------------------------------------------------------


class TestErrors:
    def test_unsupported_language(self):
        with pytest.raises(TranslateDeckError, match="unsupported language"):
            translate_deck_text(
                HEADER_PREAMBLE,
                source_lang="de",
                target_lang="fr",
                translator=StaticSlideTranslator(default="x"),
            )

    def test_same_language(self):
        with pytest.raises(TranslateDeckError, match="both"):
            translate_deck_text(
                HEADER_PREAMBLE,
                source_lang="de",
                target_lang="de",
                translator=StaticSlideTranslator(default="x"),
            )

    def test_translation_failure_surfaces_cell(self):
        text = HEADER_PREAMBLE + _slide_pair("intro", "Einleitung", "Introduction")
        de, _ = _split(text)
        # A translator that can do the title but not the slide body must fail
        # loudly, naming the offending slide, and never half-write.
        translator = StaticSlideTranslator(mapping=TITLES)
        with pytest.raises(TranslateDeckError, match="intro"):
            translate_deck_text(de, source_lang="de", target_lang="en", translator=translator)

    def test_translation_failure_is_raised_not_swallowed(self):
        text = _slide_pair("only", "Allein", "Alone")
        de, _ = _split(text)
        with pytest.raises(TranslateDeckError):
            translate_deck_text(
                de, source_lang="de", target_lang="en", translator=StaticSlideTranslator()
            )

    def test_round_trip_guard_rejects_structural_corruption(self):
        # A translation that injects a *no-lang* cell boundary appears in only
        # one half, so the pair no longer aligns; the split/unify validity guard
        # must catch it rather than write a corrupt deck. (A guard on validity,
        # not on translation fidelity — an injected valid lang="en" cell would
        # round-trip and is out of scope.)
        text = _slide_pair("only", "Allein", "Alone")
        de, _ = _split(text)
        rogue = StaticSlideTranslator(default="# %%\n_INJECTED_ = 1")
        with pytest.raises(TranslateDeckError, match="split"):
            translate_deck_text(de, source_lang="de", target_lang="en", translator=rogue)
