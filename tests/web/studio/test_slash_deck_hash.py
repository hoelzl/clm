"""Issue #458: Studio renders a ``//``-deck cell's hash with the real comment token.

``_cell_views`` must parse with the deck's comment token so the render hash equals the
write-guard hash (``hash_cell`` over ``metadata.comment_token``). Token-less parsing
would leave the ``// `` prefix on each prose line, so the hashed (reflow-normalized)
text — and thus the optimistic-concurrency check — would diverge for a ``//`` deck.

(Studio's ``open_deck`` is ``.py``-only today, so the divergence is latent rather than
live; this proves the render path threads the token regardless, future-proofing Studio
for ``//`` decks and exercising the fix at the unit level.)
"""

from __future__ import annotations

from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_writeback import hash_cell

from .conftest import Course  # noqa: F401  (imported for the `service`/`course` fixtures)

_SLASH_CELL = (
    '// %% [markdown] lang="de" tags=["slide"] slide_id="x"\n'
    "// Dies ist ein langer Absatz der\n"
    "// über mehrere Zeilen umgebrochen ist und genug Text hat.\n"
)


_SLASH_CELL_REWRAPPED = (
    '// %% [markdown] lang="de" tags=["slide"] slide_id="x"\n'
    "// Dies ist ein langer Absatz\n"
    "// der über mehrere Zeilen umgebrochen ist und genug Text hat.\n"
)


def test_cell_views_render_hash_uses_the_deck_comment_token(service) -> None:
    views = service._cell_views(_SLASH_CELL, None, "//")
    slide = next(v for v in views if v.role == "slide")

    parsed_slash = parse_cells(_SLASH_CELL, "//")[0]
    assert slide.content_hash == hash_cell(parsed_slash.metadata, parsed_slash.content)

    # Reflow-insensitivity reaches the Studio render hash: the same // prose re-wrapped
    # renders the SAME content_hash — it would NOT if the token were not threaded
    # (the "// " prefix would stay embedded in the hashed prose and move on a re-wrap).
    rewrapped = next(
        v for v in service._cell_views(_SLASH_CELL_REWRAPPED, None, "//") if v.role == "slide"
    )
    assert rewrapped.content_hash == slide.content_hash
