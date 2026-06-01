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
