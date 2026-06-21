"""Direct tests for the shared positional-anchor primitives (Issue #403).

These primitives back both ``voiceover_tools`` (extract/inline) and — going
forward — the ``clm slides sync`` narrative keying, so they get first-class
coverage independent of either consumer.
"""

from __future__ import annotations

from clm.slides.anchor_primitives import (
    TITLE_MACRO_ANCHOR,
    anchor_candidates,
    anchor_key,
    anchor_token,
    body_fingerprint,
    find_predecessor_index,
    split_anchor,
)
from clm.slides.raw_cells import split_cells


def _cells(text: str):
    _preamble, cells = split_cells(text, "#")
    return cells


class TestSplitAnchor:
    def test_parses_kind_value_occ(self):
        assert split_anchor("id:foo#2") == ("id", "foo", 2)
        assert split_anchor("fp:abc123#0") == ("fp", "abc123", 0)

    def test_legacy_token_without_occ_is_occurrence_zero(self):
        assert split_anchor("id:foo") == ("id", "foo", 0)

    def test_title_macro_token(self):
        kind, value, occ = split_anchor(TITLE_MACRO_ANCHOR)
        assert kind == "tm"
        assert occ == 0


class TestBodyFingerprint:
    def test_blank_line_invariant(self):
        a = _cells("# %% [markdown]\n# A\n\n\n# B\n")[0]
        b = _cells("# %% [markdown]\n# A\n# B\n")[0]
        assert body_fingerprint(a) == body_fingerprint(b)

    def test_distinguishes_bodies(self):
        a = _cells("# %% [markdown]\n# A\n")[0]
        b = _cells("# %% [markdown]\n# B\n")[0]
        assert body_fingerprint(a) != body_fingerprint(b)


class TestFindPredecessor:
    def test_skips_narrative_cells(self):
        # voiceover (idx 2) should anchor to the code cell (idx 1), not the
        # markdown slide (idx 0), walking back over no narrative here.
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# Intro\n'
            '# %% tags=["keep"] lang="en" slide_id="demo"\nprint(1)\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# narration\n'
        )
        cells = _cells(text)
        vo_idx = len(cells) - 1
        pred = find_predecessor_index(cells, vo_idx, "en")
        assert pred is not None
        assert cells[pred].metadata.slide_id == "demo"

    def test_walks_back_over_a_preceding_voiceover(self):
        # Two voiceovers in a row: the second's predecessor is the code cell,
        # walking back over the first voiceover.
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# Intro\n'
            '# %% tags=["keep"] lang="en" slide_id="demo"\nprint(1)\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# first\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# second\n'
        )
        cells = _cells(text)
        pred = find_predecessor_index(cells, len(cells) - 1, "en")
        assert pred is not None
        assert cells[pred].metadata.slide_id == "demo"


class TestAnchorToken:
    def test_id_anchor_for_slide_id_predecessor(self):
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# Intro\n'
            '# %% tags=["keep"] lang="en" slide_id="demo"\nprint(1)\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# narration\n'
        )
        cells = _cells(text)
        bounds = (0, len(cells))
        pred = find_predecessor_index(cells, len(cells) - 1, "en")
        assert anchor_token(cells, pred, bounds, "en") == "id:demo#0"

    def test_occurrence_ordinal_for_identical_id_less_cells(self):
        # Two byte-identical id-less code cells; a voiceover after the *second*
        # must resolve to occurrence #1, not #0.
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# Intro\n'
            '# %% tags=["keep"] lang="en"\nprint(result)\n'
            '# %% tags=["keep"] lang="en"\nprint(result)\n'
            '# %% [markdown] lang="en" tags=["voiceover"]\n# narration\n'
        )
        cells = _cells(text)
        bounds = (0, len(cells))
        pred = find_predecessor_index(cells, len(cells) - 1, "en")
        kind, value = anchor_key(cells[pred])
        assert kind == "fp"
        cands = anchor_candidates(cells, bounds, kind, value, "en")
        assert len(cands) == 2  # both identical code cells match the token
        assert anchor_token(cells, pred, bounds, "en") == f"fp:{value}#1"
