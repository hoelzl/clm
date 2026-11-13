"""The sequence core's renderer (``clm.slides.doc_render``, design note §6).

The laws of §6.3 on small decks: rendering an unchanged deck gives its bytes
back (identity), and after a pass with nothing held both halves are in sync
again (agreement). Plus each source of bytes of §6.1: the own cell adjusted
(id, tags), the other half's shared cell, and a twin with the translator's
text. ``scripts/sync_shadow.py`` checks the same against real history.
"""

from __future__ import annotations

from clm.slides import doc_core
from clm.slides.bilingual_doc import SideCell
from clm.slides.doc_lenses import parse_bundle, read_file
from clm.slides.doc_render import render
from clm.slides.doc_sequence import canonical, deck_sequence

TOKEN = "#"

DE = """# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# # Einführung

# %%
x = 1

# %% tags=["keep"]
y = 2

# %% [markdown] lang="de" tags=["subslide"] slide_id="more"
# Mehr
"""
EN = (
    DE.replace('lang="de"', 'lang="en"')
    .replace("# # Einführung", "# # Introduction")
    .replace("# Mehr", "# More")
)


def _sync(
    de: str, en: str, base: tuple[str, str] = (DE, EN), texts: dict[str, str] | None = None
) -> dict[tuple[str, str], str | None]:
    """One pass over ``de``/``en`` with ``base`` as the recorded pair; every
    question answered as the shadow's judge answers it; the result rendered.
    ``texts`` maps an id to the translator's body."""
    sequence = deck_sequence(parse_bundle(*base).deck, content=canonical)
    assert not isinstance(sequence, str), sequence
    preambles: dict[tuple[str, str], tuple[str, ...] | None] = {}
    inputs: dict[str, list[SideCell]] = {}
    for lang, text in (("de", de), ("en", en)):
        preamble, cells = read_file(text, lang, "deck", TOKEN)
        preambles[(lang, "deck")] = preamble
        preambles[(lang, "companion")] = None
        inputs[lang] = cells
    out = doc_core.merge(
        doc_core.base_of(sequence),
        doc_core.cells_of(inputs["de"]),
        doc_core.cells_of(inputs["en"]),
        lambda q: {"translate_new": "body", "fork": "body", "unify": "accept"}.get(q.kind),
    )
    assert not out.opened, [q.kind for q in out.opened]

    def translate(source: SideCell, lang: str) -> str:
        assert texts is not None and source.slide_id is not None
        return texts[source.slide_id]

    return render(out.halves, preambles, inputs, translate, TOKEN)


def _in_sync(files: dict[tuple[str, str], str | None]) -> bool:
    de, en = files[("de", "deck")], files[("en", "deck")]
    assert de is not None and en is not None
    return not isinstance(deck_sequence(parse_bundle(de, en).deck), str)


def test_identity_an_unchanged_deck_renders_to_its_own_bytes() -> None:
    files = _sync(DE, EN)
    assert files == {
        ("de", "deck"): DE,
        ("en", "deck"): EN,
        ("de", "companion"): None,
        ("en", "companion"): None,
    }


def test_a_shared_edit_on_one_half_is_the_other_half_s_cell() -> None:
    files = _sync(DE.replace("x = 1", "x = 10"), EN)
    assert files[("de", "deck")] == DE.replace("x = 1", "x = 10")
    assert files[("en", "deck")] == EN.replace("x = 1", "x = 10")
    assert _in_sync(files)


def test_a_moved_shared_cell_keeps_its_bytes() -> None:
    de = DE.replace(
        '# %%\nx = 1\n\n# %% tags=["keep"]\ny = 2\n', '# %% tags=["keep"]\ny = 2\n\n# %%\nx = 1\n'
    )
    assert de != DE
    files = _sync(de, EN)
    assert files[("de", "deck")] == de
    assert files[("en", "deck")] == EN.replace(
        '# %%\nx = 1\n\n# %% tags=["keep"]\ny = 2\n', '# %% tags=["keep"]\ny = 2\n\n# %%\nx = 1\n'
    )


def test_tags_changed_on_one_half_reach_the_other() -> None:
    de = DE.replace('# %% tags=["keep"]\ny = 2', '# %% tags=["keep", "alt"]\ny = 2')
    files = _sync(de, EN)
    assert files[("en", "deck")] == EN.replace(
        '# %% tags=["keep"]\ny = 2', '# %% tags=["keep", "alt"]\ny = 2'
    )
    assert _in_sync(files)


def test_an_id_gained_on_one_half_is_stamped_on_the_other() -> None:
    de = DE.replace("# %%\nx = 1", '# %% slide_id="x-one"\nx = 1')
    files = _sync(de, EN)
    assert files[("en", "deck")] == EN.replace("# %%\nx = 1", '# %% slide_id="x-one"\nx = 1')
    assert _in_sync(files)


def test_a_localized_cell_new_on_one_half_gets_a_twin_with_the_translator_s_text() -> None:
    new_de = '# %% [markdown] lang="de" slide_id="note"\n#\n# Hinweis\n\n'
    new_en = '# %% [markdown] lang="en" slide_id="note"\n#\n# Note\n\n'
    anchor = '# %% [markdown] lang="{}" tags=["subslide"]'
    de = DE.replace(anchor.format("de"), new_de + anchor.format("de"))
    files = _sync(de, EN, texts={"note": "#\n# Note"})
    assert files[("de", "deck")] == de
    assert files[("en", "deck")] == EN.replace(anchor.format("en"), new_en + anchor.format("en"))
    assert _in_sync(files)
