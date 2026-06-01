"""Tests for the Issue #190 §4 content-anchor chokepoint (Phase 1a).

``construct_of`` / ``anchor_of`` derive a cell's content-anchor identity
(``hand slide_id > AST construct slug > sha256``) without ever writing to the
file. ``ordered_sync_cells`` now populates ``CurrentCell.construct`` so the
widened watermark can store it.
"""

from __future__ import annotations

from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import (
    MEMBERSHIP_ROLES,
    _baseline_from_watermark,
    _shared_anchor_map,
    align_anchored,
    ordered_sync_cells,
    watermark_rows,
)
from clm.slides.sync_writeback import anchor_of, cell_content_hash, construct_of


def _meta(text: str):
    cell = parse_cells(text)[0]
    return cell.metadata, cell.content


class TestConstructOf:
    def test_class_beats_function(self):
        meta, body = _meta('# %% lang="de"\nclass Widget:\n    def go(self): ...\n')
        assert construct_of(meta, body) == "class-widget"

    def test_function(self):
        meta, body = _meta('# %% lang="de"\ndef my_fun():\n    print(1)\n')
        assert construct_of(meta, body) == "function-my-fun"

    def test_import(self):
        meta, body = _meta('# %% tags=["keep"]\nimport time\n')
        assert construct_of(meta, body) == "import-time"

    def test_markdown_is_none(self):
        meta, body = _meta('# %% [markdown] lang="de"\n# Hello\n')
        assert construct_of(meta, body) is None

    def test_unparsable_code_is_none(self):
        meta, body = _meta('# %% lang="de"\n%matplotlib inline\n!ls\n')
        assert construct_of(meta, body) is None

    def test_j2_is_none(self):
        meta, body = _meta("# j2 from 'macros.j2' import header_de\n# {{ header_de(\"X\") }}\n")
        assert construct_of(meta, body) is None


class TestAnchorOf:
    def test_hand_id_wins_over_construct(self):
        meta, body = _meta('# %% lang="de" slide_id="my-id"\ndef my_fun(): ...\n')
        assert anchor_of(meta, body) == "id:my-id"

    def test_construct_when_idless(self):
        meta, body = _meta('# %% tags=["keep"]\nimport os\n')
        assert anchor_of(meta, body) == "construct:import-os"

    def test_hash_fallback_for_unnameable(self):
        meta, body = _meta('# %% lang="de"\n%timeit work()\n')
        assert anchor_of(meta, body) == f"hash:{cell_content_hash(body)}"

    def test_hand_id_markdown(self):
        meta, body = _meta('# %% [markdown] lang="de" slide_id="intro"\n# Hi\n')
        assert anchor_of(meta, body) == "id:intro"

    def test_id_and_construct_slugs_cannot_collide(self):
        # A hand id literally "import-time" must not collide with the construct
        # slug of `import time` (the prefixes keep the namespaces disjoint).
        id_meta, id_body = _meta('# %% lang="de" slide_id="import-time"\nx = 1\n')
        con_meta, con_body = _meta('# %% tags=["keep"]\nimport time\n')
        assert anchor_of(id_meta, id_body) != anchor_of(con_meta, con_body)


class TestOrderedSyncCellsPopulatesConstruct:
    def test_localized_code_cell_carries_construct(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## Titel\n'
            '# %% lang="de" tags=["keep"] slide_id="demo"\ndef greet():\n    pass\n'
        )
        cells = ordered_sync_cells(parse_cells(text), "de")
        by_role = {c.role: c for c in cells}
        assert by_role["slide"].construct is None  # markdown -> no construct
        assert by_role["code"].construct == "function-greet"


class TestWatermarkRows:
    def test_partitions_and_synthetic_roles(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## Titel\n'  # legacy de
            '# %% tags=["keep"]\nimport time\n'  # neutral -> shared, synthetic
            '# %% lang="de"\ndef greet():\n    pass\n'  # localized id-less -> de, synthetic
            '# %% lang="de" tags=["keep"] slide_id="demo"\nx = 1\n'  # localized id'd -> de, legacy
        )
        rows = watermark_rows(parse_cells(text))

        de = rows["de"]
        shared = rows["shared"]
        assert rows["en"] == []

        # de partition: the slide (legacy), the id-less localized code (synthetic),
        # and the id'd localized code (legacy CODE_ROLE), in file order.
        assert [(r[1], r[2]) for r in de] == [
            ("s", "slide"),
            (None, "localized-code"),
            ("demo", "code"),
        ]
        # The id-less localized code carries its construct anchor.
        assert de[1][4] == "function-greet"
        # shared partition: the neutral code, recorded once with a synthetic role.
        assert [(r[1], r[2], r[4]) for r in shared] == [(None, "neutral-code", "import-time")]

    def test_legacy_subset_matches_ordered_sync_cells(self):
        # The classifier filters MEMBERSHIP_ROLES, so the legacy subset of the de
        # partition must reproduce ordered_sync_cells's (slide_id, role) order
        # exactly — this is the no-behavior-change guarantee.
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# ## T\n'
            '# %% tags=["keep"]\nimport os\n'
            '# %% lang="de"\nprint("hi")\n'
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="s"\n# VO\n'
        )
        cells = parse_cells(text)
        legacy = [(r[1], r[2]) for r in watermark_rows(cells)["de"] if r[2] not in MEMBERSHIP_ROLES]
        expected = [(c.slide_id, c.role) for c in ordered_sync_cells(cells, "de")]
        assert legacy == expected

    def test_baseline_reindexes_legacy_positions_past_membership_rows(self):
        # _baseline_from_watermark must re-index the real-role survivors into the
        # legacy-only position space (0,1,...), NOT pass the membership-inflated
        # stored positions through — else _resolve_duplicates' abs(pos - base.pos)
        # picks the wrong copy as "original" (Issue #190 review finding 2).
        rows = [
            (0, None, "localized-code", "h0", "x"),  # membership row, dropped
            (1, "dup", "slide", "h1", None),  # legacy: stored pos 1 -> re-indexed 0
            (2, "dup", "voiceover", "h2", None),  # legacy: stored pos 2 -> re-indexed 1
        ]
        base = _baseline_from_watermark(rows)
        assert [(b.position, b.slide_id, b.role) for b in base] == [
            (0, "dup", "slide"),
            (1, "dup", "voiceover"),
        ]


class TestAlignAnchored:
    """The Issue #190 item-2 direction detector (Phase 3a)."""

    _BASE = '# %% tags=["keep"]\nimport time\n'

    def test_halves_agree_is_noop_even_with_empty_baseline(self):
        # The robustness gate: if both halves agree on every neutral cell (unify
        # holds), there is nothing to propagate regardless of the baseline — incl.
        # an empty baseline from a pre-Phase-1b watermark.
        de = '# %% [markdown] lang="de" tags=["slide"] slide_id="s"\n# T\n' + self._BASE
        en = '# %% [markdown] lang="en" tags=["slide"] slide_id="s"\n# T\n' + self._BASE
        a = align_anchored(parse_cells(de), parse_cells(en), {})
        assert a.direction is None
        assert not a.diverged

    def test_de_drift_gives_de_to_en(self):
        baseline = _shared_anchor_map(parse_cells(self._BASE))
        de = '# %% tags=["keep"]\nimport time\nx = 1\n'  # DE edited the shared cell
        en = self._BASE  # EN unchanged
        a = align_anchored(parse_cells(de), parse_cells(en), baseline)
        assert a.direction == "de->en"
        assert not a.diverged

    def test_en_drift_gives_en_to_de(self):
        baseline = _shared_anchor_map(parse_cells(self._BASE))
        de = self._BASE
        en = '# %% tags=["keep"]\nimport time\nx = 1\n'
        a = align_anchored(parse_cells(de), parse_cells(en), baseline)
        assert a.direction == "en->de"
        assert not a.diverged

    def test_both_sides_drift_diverges(self):
        baseline = _shared_anchor_map(parse_cells(self._BASE))
        de = '# %% tags=["keep"]\nimport time\nx = 1\n'  # DE: -> construct x
        en = '# %% tags=["keep"]\nimport time\ny = 2\n'  # EN: -> construct y (differs)
        a = align_anchored(parse_cells(de), parse_cells(en), baseline)
        assert a.diverged
        assert a.direction is None
