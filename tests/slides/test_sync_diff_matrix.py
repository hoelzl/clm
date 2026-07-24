"""The §7.4 closure tests for :mod:`clm.slides.sync_diff` (#520 Phase 2).

Three structural guarantees, each preventing a distinct regression class:

1. **The transition matrix walk** — langness {shared, localized} ×
   id {present, absent} × layout {inline, companion} × progress {complete,
   in-progress-de, in-progress-en} — asserts every combination maps to
   exactly one registered row of the §7.2/§7.3 tables (or to the §3.4
   normalize refusal for the states v3 defines away). A new class axis or
   row must extend this enumeration to land.
2. **Field coverage (§6.3)** — every serialized field of the member record
   is either compared by the differ or explicitly annotated cosmetic. A new
   field on :class:`Member`/:class:`SideCell` fails here until annotated.
3. **Hypothesis noise-floor properties** — canonical bundles diff clean
   against their own snapshot; any single one-sided mutation is propagated
   or alerted, never silently clean, with a hard per-mutation item ceiling
   (the anti-W10 contract).
"""

from __future__ import annotations

import pytest
from attrs import evolve, fields
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from clm.slides.bilingual_doc import Member, SideCell
from clm.slides.doc_lenses import parse_bundle
from clm.slides.sync_diff import (
    COMPARED_MEMBER_FIELDS,
    COMPARED_SIDECELL_FIELDS,
    COSMETIC_MEMBER_FIELDS,
    COSMETIC_SIDECELL_FIELDS,
    FRAMED_ACTIONS,
    MECHANICAL_ACTIONS,
    baseline_from_deck,
    diff_outcome,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 1. The §7.4 transition-matrix walk
# ---------------------------------------------------------------------------

# The member under test always lives in group s0 and is called "cell-x"
# when id'd. ``progress`` describes how far the langness transition got:
# complete (both halves), in_progress_de / in_progress_en (one half only).

_MATRIX: list[tuple[str, str, str, str, str]] = []
for _layout in ("inline", "companion"):
    for _progress in ("complete", "in_progress_de", "in_progress_en"):
        # fork rows (base shared)
        _MATRIX.append(
            (
                "shared",
                "present",
                _layout,
                _progress,
                "record_fork" if _progress == "complete" else "fork_pending_twin",
            )
        )
        _MATRIX.append(
            (
                "shared",
                "absent",
                _layout,
                _progress,
                "record_fork" if _progress == "complete" else "fork_pending_twin",
            )
        )
        # unify rows (base localized, ids always present after §3.4)
        _MATRIX.append(
            (
                "localized",
                "present",
                _layout,
                _progress,
                "record_unify" if _progress == "complete" else "unify_pending_twin",
            )
        )
        # localized + id-absent cannot arise: the §3.4 precondition refuses
        # the *baseline* before any diff runs (normalize owns cold pairing).
        _MATRIX.append(("localized", "absent", _layout, _progress, "normalize_refusal"))


def _member_cell(
    layout: str,
    *,
    lang: str | None,
    slide_id: str | None,
    body: str,
) -> str:
    attrs_ = ""
    if lang is not None:
        attrs_ += f' lang="{lang}"'
    if layout == "companion":
        attrs_ += ' tags=["notes"] for_slide="s0"'
    if slide_id is not None:
        attrs_ += f' slide_id="{slide_id}"'
    return f"# %% [markdown]{attrs_}\n# {body}\n\n"


def _bundle(
    layout: str,
    de_cell: str | None,
    en_cell: str | None,
) -> tuple[str, str, str | None, str | None]:
    de_deck = [HEADER_DE, _slide("s0", "de", "Titel")]
    en_deck = [HEADER_EN, _slide("s0", "en", "Title")]
    de_comp = en_comp = None
    if layout == "inline":
        if de_cell:
            de_deck.append(de_cell)
        if en_cell:
            en_deck.append(en_cell)
    else:
        de_comp = _build(de_cell) if de_cell else ""
        en_comp = _build(en_cell) if en_cell else ""
    return _build(*de_deck), _build(*en_deck), de_comp, en_comp


@pytest.mark.parametrize(
    ("langness", "id_state", "layout", "progress", "expected"),
    _MATRIX,
    ids=[f"{c[0]}-{c[1]}-{c[2]}-{c[3]}" for c in _MATRIX],
)
def test_transition_matrix_maps_to_exactly_one_row(
    langness: str, id_state: str, layout: str, progress: str, expected: str
):
    base_id = "cell-x" if id_state == "present" else None

    if langness == "shared":
        # Base: one attr-less cell, byte-identical on both sides.
        base_de = _member_cell(layout, lang=None, slide_id=base_id, body="shared body")
        base_en = _member_cell(layout, lang=None, slide_id=base_id, body="shared body")
        # Fork: mark with lang attrs; an id-less member gets an id minted
        # at fork time (§7.3 — localized members must be id'd).
        forked_de = _member_cell(layout, lang="de", slide_id="cell-x", body="shared body")
        forked_en = _member_cell(layout, lang="en", slide_id="cell-x", body="shared body")
        cur_de = forked_de if progress in ("complete", "in_progress_de") else base_de
        cur_en = forked_en if progress in ("complete", "in_progress_en") else base_en
    else:
        base_de = _member_cell(layout, lang="de", slide_id=base_id, body="DE body")
        base_en = _member_cell(layout, lang="en", slide_id=base_id, body="EN body")
        # Unify: drop the lang attrs (the id STAYS, P3); the shared body is
        # chosen at completion.
        unified = _member_cell(layout, lang=None, slide_id=base_id, body="unified body")
        cur_de = unified if progress in ("complete", "in_progress_de") else base_de
        cur_en = unified if progress in ("complete", "in_progress_en") else base_en

    base_outcome = parse_bundle(*_bundle(layout, base_de, base_en))
    if expected == "normalize_refusal":
        assert base_outcome.refusal is not None
        codes = {r.code for r in base_outcome.refusal.reasons}
        assert codes <= {"idless_localized", "idless_narrative"}
        assert codes
        return
    assert base_outcome.deck is not None, (
        base_outcome.refusal.render() if base_outcome.refusal else "?"
    )
    base = baseline_from_deck(base_outcome.deck)

    diff = diff_outcome(parse_bundle(*_bundle(layout, cur_de, cur_en)), base)
    assert diff.refusal is None, diff.refusal.render() if diff.refusal else "?"
    assert len(diff.items) == 1, [(i.outcome, i.action, i.key, i.detail) for i in diff.items]
    item = diff.items[0]
    assert item.action == expected
    assert item.action in MECHANICAL_ACTIONS | FRAMED_ACTIONS
    if progress == "in_progress_de":
        assert item.direction == "de_to_en"
    elif progress == "in_progress_en":
        assert item.direction == "en_to_de"


def test_action_registries_are_disjoint_and_closed():
    assert not (MECHANICAL_ACTIONS & FRAMED_ACTIONS)
    matrix_rows = {row for *_, row in _MATRIX} - {"normalize_refusal"}
    assert matrix_rows <= MECHANICAL_ACTIONS | FRAMED_ACTIONS
    # The tag-parity aspect rows (#615) never appear in the transition
    # matrix (they are orthogonal to langness transitions), so the walk
    # above cannot police their registration — assert it explicitly.
    tag_axis_rows = {row for *_, rows in _TAG_AXIS for row in rows}
    assert tag_axis_rows <= MECHANICAL_ACTIONS | FRAMED_ACTIONS
    assert "mirror_tags" in MECHANICAL_ACTIONS
    assert "conflict_tags" in FRAMED_ACTIONS


# ---------------------------------------------------------------------------
# 1b. The tag-divergence axis (#615)
# ---------------------------------------------------------------------------

# A focused walk over the tag-parity aspect rows: shape {localized-id'd,
# complete-fork} × move {one-sided, divergent, baseline-carried}. Unlike the
# §7.4 matrix walk this asserts the expected *set* of rows (the fork cases
# legitimately co-emit with record_fork). Bodies stay at base throughout —
# the aspect row must fully account for the tag delta.

_TAG_AXIS: list[tuple[str, str, frozenset[str]]] = [
    ("localized", "one_sided", frozenset({"mirror_tags"})),
    ("localized", "divergent", frozenset({"conflict_tags"})),
    ("localized", "baseline_carried", frozenset({"conflict_tags"})),
    ("fork", "one_sided", frozenset({"mirror_tags", "record_fork"})),
    ("fork", "divergent", frozenset({"conflict_tags", "record_fork"})),
    ("fork", "baseline_carried", frozenset({"conflict_tags", "record_fork"})),
]


def _tag_cell(lang: str | None, tags: tuple[str, ...], body: str) -> str:
    tag_attr = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    lang_attr = f' lang="{lang}"' if lang is not None else ""
    return f'# %% [markdown]{lang_attr} tags={tag_attr} slide_id="cell-x"\n# {body}\n\n'


@pytest.mark.parametrize(
    ("shape", "move", "expected_actions"),
    _TAG_AXIS,
    ids=[f"{c[0]}-{c[1]}" for c in _TAG_AXIS],
)
def test_tag_divergence_axis_maps_to_expected_row_set(
    shape: str, move: str, expected_actions: frozenset[str]
):
    if shape == "localized":
        base_de = _tag_cell("de", ("notes",), "DE body")
        base_en_tags = ("alt",) if move == "baseline_carried" else ("notes",)
        base_en = _tag_cell("en", base_en_tags, "EN body")
        cur_de = base_de if move == "baseline_carried" else _tag_cell("de", ("alt",), "DE body")
        cur_en = _tag_cell("en", ("voiceover",), "EN body") if move == "divergent" else base_en
    else:
        base_de = base_en = _tag_cell(None, ("notes",), "shared body")
        en_tags = ("notes",) if move == "one_sided" else ("voiceover",)
        cur_de = _tag_cell("de", ("alt",), "shared body")
        cur_en = _tag_cell("en", en_tags, "shared body")

    base_outcome = parse_bundle(*_bundle("inline", base_de, base_en))
    assert base_outcome.deck is not None, (
        base_outcome.refusal.render() if base_outcome.refusal else "?"
    )
    base = baseline_from_deck(base_outcome.deck)
    if shape == "fork" and move == "baseline_carried":
        # A real shared base always records one tag tuple on both sides, so
        # the unattributable state is the pre-tag-recording ledger shape:
        # entries whose tag fields predate recording (``None`` = moved).
        key = next(k for k in base.members if "cell-x" in k)
        base.members[key] = evolve(base.members[key], de_tags=None, en_tags=None)

    diff = diff_outcome(parse_bundle(*_bundle("inline", cur_de, cur_en)), base)
    assert diff.refusal is None, diff.refusal.render() if diff.refusal else "?"
    rows = [(i.outcome, i.action, i.key, i.direction, i.detail) for i in diff.items]
    assert {i.action for i in diff.items} == set(expected_actions), rows
    assert len(diff.items) == len(expected_actions), rows

    by_action = {i.action: i for i in diff.items}
    if "mirror_tags" in by_action:
        item = by_action["mirror_tags"]
        assert item.outcome == "mechanical"
        assert item.direction == "de_to_en"  # the DE half is the mover throughout
        assert item.side == "de"
    if "conflict_tags" in by_action:
        item = by_action["conflict_tags"]
        assert item.outcome == "conflict"
        assert item.side is None  # the answer names the side, not the row
        if shape == "fork":
            assert item.direction == "both"  # fork-time divergence is never baseline-blessed
        else:
            assert item.direction == ("none" if move == "baseline_carried" else "both")
    assert all(i.action in MECHANICAL_ACTIONS | FRAMED_ACTIONS for i in diff.items)


# ---------------------------------------------------------------------------
# 2. Field coverage (§6.3)
# ---------------------------------------------------------------------------


def test_every_member_field_is_compared_or_annotated_cosmetic():
    names = {f.name for f in fields(Member)}
    assert names == COMPARED_MEMBER_FIELDS | COSMETIC_MEMBER_FIELDS
    assert not COMPARED_MEMBER_FIELDS & COSMETIC_MEMBER_FIELDS


def test_every_sidecell_field_is_compared_or_annotated_cosmetic():
    names = {f.name for f in fields(SideCell)}
    assert names == COMPARED_SIDECELL_FIELDS | COSMETIC_SIDECELL_FIELDS
    assert not COMPARED_SIDECELL_FIELDS & COSMETIC_SIDECELL_FIELDS


# ---------------------------------------------------------------------------
# 3. Hypothesis noise-floor properties
# ---------------------------------------------------------------------------


def _localized(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" slide_id="{slug}"\n# {text}\n\n'


def _shared_code(name: str, value: int = 1) -> str:
    return f'# %% tags=["keep"]\n{name} = {value}\n\n'


def _inline_vo(slug: str, lang: str, text: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{slug}"\n# {text}\n\n'


def _companion_cell(slug: str, lang: str, owner: str, text: str) -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["notes"] for_slide="{owner}" '
        f'slide_id="{slug}"\n#\n# - {text}\n\n'
    )


def _strip_final_blank(text: str) -> str:
    return text[:-1] if text.endswith("\n\n") else text


@st.composite
def _normalized_bundle(draw) -> tuple[str, str, str | None, str | None]:
    """A canonical §3.4 bundle (the test_doc_lenses.py generator, kept in
    lockstep — canonical decks must diff clean against their snapshot)."""
    n_groups = draw(st.integers(min_value=1, max_value=4))
    de_parts, en_parts = [HEADER_DE], [HEADER_EN]
    comp_de: list[str] = []
    comp_en: list[str] = []
    with_companion = draw(st.booleans())
    member_kinds = ["localized", "shared", "code"] + ([] if with_companion else ["inline_vo"])
    for g in range(n_groups):
        slug = f"s{g}"
        de_parts.append(_slide(slug, "de", f"Titel {g}"))
        en_parts.append(_slide(slug, "en", f"Title {g}"))
        n_members = draw(st.integers(min_value=0, max_value=3))
        for m in range(n_members):
            kind = draw(st.sampled_from(member_kinds))
            mslug = f"{slug}-m{m}"
            if kind == "localized":
                de_parts.append(_localized(mslug, "de", f"DE {mslug}"))
                en_parts.append(_localized(mslug, "en", f"EN {mslug}"))
            elif kind == "shared":
                de_parts.append(_shared_code(f"var_{g}_{m}"))
                en_parts.append(_shared_code(f"var_{g}_{m}"))
            elif kind == "inline_vo":
                de_parts.append(_inline_vo(mslug, "de", f"VO DE {mslug}"))
                en_parts.append(_inline_vo(mslug, "en", f"VO EN {mslug}"))
            else:
                de_parts.append(f'# %% lang="de" slide_id="{mslug}"\nprint("de")\n\n')
                en_parts.append(f'# %% lang="en" slide_id="{mslug}"\nprint("en")\n\n')
        if with_companion and draw(st.booleans()):
            comp_de.append(_companion_cell(f"{slug}-vo", "de", slug, f"Text {slug}"))
            comp_en.append(_companion_cell(f"{slug}-vo", "en", slug, f"Text {slug}"))
    de = _strip_final_blank("".join(de_parts))
    en = _strip_final_blank("".join(en_parts))
    de_c = _strip_final_blank("".join(comp_de)) if comp_de else None
    en_c = _strip_final_blank("".join(comp_en)) if comp_en else None
    return de, en, de_c, en_c


@given(bundle=_normalized_bundle())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=60)
def test_canonical_bundle_diffs_clean_against_its_snapshot(
    bundle: tuple[str, str, str | None, str | None],
) -> None:
    outcome = parse_bundle(*bundle)
    assert outcome.deck is not None
    base = baseline_from_deck(outcome.deck)
    diff = diff_outcome(parse_bundle(*bundle), base)
    assert diff.is_clean, [(i.outcome, i.action, i.key, i.detail) for i in diff.items]


@given(bundle=_normalized_bundle(), data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=100)
def test_single_mutation_is_propagated_or_alerted_never_silent(
    bundle: tuple[str, str, str | None, str | None], data: st.DataObject
) -> None:
    """The cardinal invariant (the v3 analogue of the corpus mutation
    oracle's ``_falsely_consistent``): whatever single edit an author makes
    on one half, the diff either refuses (framed) or reports it — and the
    report stays within a hard noise ceiling (the anti-W10 property)."""
    de, en, de_c, en_c = bundle
    base_outcome = parse_bundle(de, en, de_c, en_c)
    assert base_outcome.deck is not None
    base = baseline_from_deck(base_outcome.deck)

    side = data.draw(st.sampled_from(["de", "en"]))
    menu = ["edit_shared", "edit_localized", "add_cell", "strip_id", "retag"]
    if (de_c if side == "de" else en_c) is not None:
        menu += ["edit_companion", "drop_companion_cell"]
    mutation = data.draw(st.sampled_from(menu))

    def mutate(text: str) -> str:
        if mutation == "edit_shared" and " = 1" in text:
            return text.replace(" = 1", " = 2", 1)
        if mutation == "edit_localized" and "# DE s" in text:
            return text.replace("# DE s", "# DE edit s", 1)
        if mutation == "edit_localized" and "# EN s" in text:
            return text.replace("# EN s", "# EN edit s", 1)
        if mutation == "add_cell":
            return text + "\n" + _strip_final_blank(_localized(f"extra-{side}", side, "added"))
        if mutation == "strip_id":
            import re

            return re.sub(r'(\[markdown\] lang="[a-z]+") slide_id="[^"]*"', r"\1", text, count=1)
        if mutation == "retag" and 'tags=["keep"]' in text:
            return text.replace('tags=["keep"]', 'tags=["keep", "extra"]', 1)
        return text

    def mutate_companion(text: str) -> str:
        if mutation == "edit_companion":
            return text.replace("# - Text", "# - Edited text", 1)
        if mutation == "drop_companion_cell":
            cells = text.split("\n# %%")
            if len(cells) > 1:
                cells.pop(len(cells) - 1)
                return "\n# %%".join(cells)
        return text

    new_de, new_en, new_de_c, new_en_c = de, en, de_c, en_c
    if mutation in ("edit_companion", "drop_companion_cell"):
        if side == "de" and de_c is not None:
            new_de_c = mutate_companion(de_c)
        elif en_c is not None:
            new_en_c = mutate_companion(en_c)
    elif side == "de":
        new_de = mutate(de)
    else:
        new_en = mutate(en)

    changed = (new_de, new_en, new_de_c, new_en_c) != (de, en, de_c, en_c)
    outcome = parse_bundle(new_de, new_en, new_de_c, new_en_c)
    if outcome.refusal is not None:
        assert outcome.refusal.reasons  # framed, enumerated
        return
    assert outcome.deck is not None
    diff = diff_outcome(outcome, base)
    if not changed:
        assert diff.is_clean
        return
    # Propagated-or-alerted: a real change can never diff clean.
    assert not diff.is_clean, mutation
    # The noise ceiling: one authoring action, at most three items (a
    # mutation may legitimately split into a content row plus an order row).
    assert len(diff.items) <= 3, [(i.outcome, i.action, i.key, i.detail) for i in diff.items]
    # Determinism: the same input diffs identically.
    again = diff_outcome(parse_bundle(new_de, new_en, new_de_c, new_en_c), base)
    assert [(i.key, i.action) for i in again.items] == [(i.key, i.action) for i in diff.items]
