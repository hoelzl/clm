"""The sequence core's text layer (``clm.slides.doc_text``, design note §5).

Each row on a small deck: the text of a localized cell edited on one half or
both, a cell with no recorded text, the owner attributes merged 3-way, and
the file preambles. The last tests render a plan
(``clm.slides.doc_render``).
"""

from __future__ import annotations

from attrs import evolve

from clm.slides import doc_core
from clm.slides.bilingual_doc import SideCell
from clm.slides.doc_lenses import parse_bundle, read_file
from clm.slides.doc_render import render
from clm.slides.doc_sequence import canonical, deck_sequence
from clm.slides.doc_text import (
    TextPlan,
    TextRow,
    plan_text,
    recorded_by_half,
    text_key,
    text_of,
)

TOKEN = "#"

DE = """# Preamble: "macros"

# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# # Einführung

# %%
x = 1

# %% [markdown] lang="de" tags=["voiceover"] slide_id="vo" for_slide="intro"
# Hallo

# %% [markdown] lang="de" tags=["subslide"] slide_id="more"
# Mehr
"""
EN = (
    DE.replace('lang="de"', 'lang="en"')
    .replace("# # Einführung", "# # Introduction")
    .replace("# Hallo", "# Hello")
    .replace("# Mehr", "# More")
)


def _plan(
    de: str,
    en: str,
    base: tuple[str, str] = (DE, EN),
    answers: dict[str, str] | None = None,
) -> TextPlan:
    return _run(de, en, base, answers, rendered=False)[0]


def _run(
    de: str,
    en: str,
    base: tuple[str, str] = (DE, EN),
    answers: dict[str, str] | None = None,
    texts: dict[str, str] | None = None,
    *,
    rendered: bool = True,
) -> tuple[TextPlan, dict[tuple[str, str], str | None] | None]:
    """The core's merge of ``de``/``en`` against ``base``, then the text
    layer with ``answers`` (row kind -> answer; a kind missing is held)."""
    sequence = deck_sequence(parse_bundle(*base).deck, content=canonical)
    assert not isinstance(sequence, str), sequence
    inputs: dict[str, list[SideCell]] = {}
    recorded_cells: dict[str, list[SideCell]] = {}
    preambles: dict[tuple[str, str], tuple[str, ...] | None] = {}
    base_preambles: dict[tuple[str, str], tuple[str, ...] | None] = {}
    for lang, now, then in (("de", de, base[0]), ("en", en, base[1])):
        preambles[(lang, "deck")], inputs[lang] = read_file(now, lang, "deck", TOKEN)
        base_preambles[(lang, "deck")], recorded_cells[lang] = read_file(then, lang, "deck", TOKEN)
        preambles[(lang, "companion")] = base_preambles[(lang, "companion")] = None
    out = doc_core.merge(
        doc_core.base_of(sequence),
        doc_core.cells_of(inputs["de"]),
        doc_core.cells_of(inputs["en"]),
        lambda q: {"translate_new": "body"}.get(q.kind),
    )
    assert not out.opened, [q.kind for q in out.opened]
    given = answers or {}
    plan = plan_text(
        out.halves,
        recorded_by_half(recorded_cells),
        preambles,
        base_preambles,
        lambda row: given.get(row.kind),
    )
    if plan.held or not rendered:
        return plan, None

    def translate(source: SideCell, lang: str) -> str:
        key = text_key(source)
        assert texts is not None and key is not None
        return texts[key]

    return plan, render(out.halves, preambles, inputs, translate, TOKEN, plan)


def _kinds(rows: list[TextRow]) -> list[tuple[str, str, str | None]]:
    return [(r.kind, r.handle, r.side) for r in rows]


def test_an_unchanged_deck_has_no_rows() -> None:
    plan = _plan(DE, EN)
    assert (plan.asked, plan.mechanical, plan.held, plan.retext, plan.preambles) == (
        [],
        [],
        [],
        {},
        {},
    )
    assert plan.owners == {
        (lang, sid): owners
        for lang in ("de", "en")
        for sid, owners in (
            ("intro", (None, None)),
            ("vo", ("intro", None)),
            ("more", (None, None)),
        )
    }


def test_an_edit_on_one_half_asks_translate_edit_and_body_retexts_the_twin() -> None:
    de = DE.replace("# Mehr", "# Noch mehr")
    plan = _plan(de, EN, answers={"translate_edit": "body"})
    assert [(r.kind, r.handle, r.side, a) for r, a in plan.asked] == [
        ("translate_edit", "id:more", "de", "body")
    ]
    assert plan.held == []
    source = plan.retext[("en", "more")]
    assert source.lines[1] == "# Noch mehr"


def test_keep_twin_leaves_the_twin_alone() -> None:
    plan = _plan(DE.replace("# Mehr", "# Noch mehr"), EN, answers={"translate_edit": "keep_twin"})
    assert plan.retext == {} and plan.held == []


def test_an_unanswered_question_holds_the_deck() -> None:
    plan = _plan(DE.replace("# Mehr", "# Noch mehr"), EN)
    assert _kinds(plan.held) == [("translate_edit", "id:more", "de")]


def test_re_spacing_a_localized_cell_is_an_edit_of_its_text() -> None:
    de = DE.replace("# Hallo\n", "# Hallo\n\n")
    plan = _plan(de, EN, answers={"translate_edit": "body"})
    assert _kinds([r for r, _ in plan.asked]) == [("translate_edit", "id:vo", "de")]


def test_edits_on_both_halves_ask_verify_translation() -> None:
    plan = _plan(
        DE.replace("# Mehr", "# Noch mehr"),
        EN.replace("# More", "# Even more"),
        answers={"verify_translation": "confirm"},
    )
    assert _kinds([r for r, _ in plan.asked]) == [("verify_translation", "id:more", None)]
    assert plan.retext == {} and plan.held == []


def test_a_tags_only_change_is_not_a_text_row() -> None:
    de = DE.replace('tags=["subslide"] slide_id="more"', 'tags=["slide"] slide_id="more"')
    plan = _plan(de, EN)
    assert plan.asked == [] and plan.held == []


def test_an_owner_changed_on_one_half_is_taken() -> None:
    de = DE.replace('slide_id="vo" for_slide="intro"', 'slide_id="vo" for_slide="more"')
    plan = _plan(de, EN)
    assert plan.asked == [] and plan.held == []
    assert plan.owners[("de", "vo")] == plan.owners[("en", "vo")] == ("more", None)


def test_an_owner_changed_differently_on_both_halves_is_conflict_owner() -> None:
    de = DE.replace('slide_id="vo" for_slide="intro"', 'slide_id="vo" for_slide="more"')
    en = EN.replace('slide_id="vo" for_slide="intro"', 'slide_id="vo"')
    assert _kinds(_plan(de, en).held) == [("conflict_owner", "id:vo", None)]
    plan = _plan(de, en, answers={"conflict_owner": "en"})
    assert plan.held == [] and plan.owners[("de", "vo")] == plan.owners[("en", "vo")] == (
        None,
        None,
    )


def test_an_owner_naming_no_id_is_broken_owner() -> None:
    de = DE.replace('slide_id="vo" for_slide="intro"', 'slide_id="vo" for_slide="gone"')
    plan = _plan(de, EN)
    assert _kinds(plan.held) == [("broken_owner", "id:vo", None)]


def test_a_cell_with_no_recorded_text_asks_verify_cold() -> None:
    # both halves added the same localized cell since base
    anchor = '# %% [markdown] lang="{}" tags=["subslide"]'
    new = '# %% [markdown] lang="{0}" slide_id="extra"\n#\n# {1}\n\n'
    de = DE.replace(anchor.format("de"), new.format("de", "Neu") + anchor.format("de"))
    en = EN.replace(anchor.format("en"), new.format("en", "New") + anchor.format("en"))
    plan = _plan(de, en, answers={"verify_cold": "confirm"})
    assert _kinds([r for r, _ in plan.asked]) == [("verify_cold", "id:extra", None)]


def test_a_preamble_changed_on_one_half_propagates() -> None:
    de = DE.replace('Preamble: "macros"', 'Preamble: "other"')
    plan = _plan(de, EN)
    assert _kinds(plan.mechanical) == [("propagate_preamble", "preamble:deck", "de")]
    assert plan.preambles[("en", "deck")][0] == '# Preamble: "other"'


def test_preambles_changed_differently_ask_conflict_preamble() -> None:
    de = DE.replace('Preamble: "macros"', 'Preamble: "other"')
    en = EN.replace('Preamble: "macros"', 'Preamble: "third"')
    assert _kinds(_plan(de, en).held) == [("conflict_preamble", "preamble:deck", None)]
    plan = _plan(de, en, answers={"conflict_preamble": "de"})
    assert list(plan.preambles) == [("en", "deck")]
    assert plan.preambles[("en", "deck")][0] == '# Preamble: "other"'


def test_text_of_ignores_id_tags_and_owners_but_not_the_separator() -> None:
    _, cells = read_file(DE, "de", "deck", TOKEN)
    vo = cells[2]
    assert vo.slide_id == "vo"
    moved = evolve(vo, lines=('# %% [markdown] lang="de" tags=["notes"]', *vo.lines[1:]))
    assert text_of(moved) == text_of(vo)
    spaced = evolve(vo, lines=(*vo.lines, ""))
    assert text_of(spaced) != text_of(vo)


def test_rendered_a_translate_edit_body_rewrites_the_twin_with_the_edited_cell_s_separator() -> (
    None
):
    de = DE.replace("# Hallo\n", "# Hallo, Welt\n\n")
    _, files = _run(de, EN, answers={"translate_edit": "body"}, texts={"vo": "#\n# Hello, world\n"})
    assert files is not None
    assert files[("de", "deck")] == de
    assert files[("en", "deck")] == EN.replace("# Hello\n", "#\n# Hello, world\n\n")


def test_rendered_a_translate_edit_body_keeps_the_twin_s_separator_when_the_edit_kept_its_own() -> (
    None
):
    de = DE.replace("# Hallo\n", "# Hallo, Welt\n")
    en = EN.replace("# Hello\n", "# Hello\n\n")
    base = (DE, en)
    _, files = _run(
        de, en, base=base, answers={"translate_edit": "body"}, texts={"vo": "#\n# Hello, world\n"}
    )
    assert files is not None
    assert files[("en", "deck")] == en.replace("# Hello\n", "#\n# Hello, world\n")


def test_an_owner_the_halves_recorded_differently_is_each_half_s_own() -> None:
    de_base = DE.replace('for_slide="intro"', 'for_slide="intro" vo_anchor="fp:aaaa#0"')
    en_base = EN.replace('for_slide="intro"', 'for_slide="intro" vo_anchor="fp:bbbb#0"')
    de = de_base.replace("fp:aaaa#0", "fp:cccc#0")
    plan, files = _run(de, en_base, base=(de_base, en_base))
    assert plan.asked == [] and plan.held == []
    assert plan.owners[("de", "vo")] == ("intro", "fp:cccc#0")
    assert plan.owners[("en", "vo")] == ("intro", "fp:bbbb#0")
    assert files is not None and files[("en", "deck")] == en_base


def test_a_fingerprint_anchor_replaced_by_an_id_anchor_on_one_half_reaches_the_other() -> None:
    de_base = DE.replace('for_slide="intro"', 'for_slide="intro" vo_anchor="fp:aaaa#0"')
    en_base = EN.replace('for_slide="intro"', 'for_slide="intro" vo_anchor="fp:bbbb#0"')
    de = de_base.replace("fp:aaaa#0", "id:intro#0")
    _, files = _run(de, en_base, base=(de_base, en_base))
    assert files is not None
    assert files[("en", "deck")] == en_base.replace("fp:bbbb#0", "id:intro#0")


def test_rendered_keep_twin_leaves_the_twin_s_bytes() -> None:
    de = DE.replace("# Hallo\n", "# Hallo, Welt\n\n")
    _, files = _run(de, EN, answers={"translate_edit": "keep_twin"})
    assert files is not None and files[("en", "deck")] == EN


def test_rendered_an_owner_changed_on_one_half_reaches_the_other() -> None:
    de = DE.replace('slide_id="vo" for_slide="intro"', 'slide_id="vo" for_slide="more"')
    _, files = _run(de, EN)
    assert files is not None
    assert files[("en", "deck")] == EN.replace(
        'slide_id="vo" for_slide="intro"', 'slide_id="vo" for_slide="more"'
    )


def test_rendered_an_owner_gained_on_one_half_is_appended_on_the_other() -> None:
    de = DE.replace('slide_id="more"', 'slide_id="more" vo_anchor="id:intro#0"')
    _, files = _run(de, EN)
    assert files is not None
    assert files[("en", "deck")] == EN.replace(
        'slide_id="more"', 'slide_id="more" vo_anchor="id:intro#0"'
    )


def test_rendered_a_preamble_changed_on_one_half_is_copied() -> None:
    de = DE.replace('Preamble: "macros"', 'Preamble: "other"')
    _, files = _run(de, EN)
    assert files is not None
    assert files[("en", "deck")] == EN.replace('Preamble: "macros"', 'Preamble: "other"')


def test_rendered_the_title_macro_is_known_by_the_reserved_id() -> None:
    def titled(deck: str, lang: str, title: str) -> str:
        macro = f"# j2 from 'macros.j2' import header_{lang}\n# {{{{ header_{lang}(\"{title}\") }}}}\n\n"
        return deck.replace('# Preamble: "macros"\n\n', macro)

    de_base, en_base = titled(DE, "de", "Alt"), titled(EN, "en", "Old")
    de = titled(DE, "de", "Neu")
    plan, files = _run(
        de,
        en_base,
        base=(de_base, en_base),
        answers={"translate_edit": "body"},
        texts={"title": '# {{ header_en("New") }}'},
    )
    assert _kinds([r for r, _ in plan.asked]) == [("translate_edit", "id:title", "de")]
    assert files is not None and files[("en", "deck")] == titled(EN, "en", "New")
