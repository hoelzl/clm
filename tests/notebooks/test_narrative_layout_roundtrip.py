"""Narrative writers keep the deck layout on rewrite (#1162).

Since #1158, ``clm harvest accept`` writes narrative bullets wrapped at 80
columns with a hanging continuation indent and one level of nested
children. The other two narrative writers -- the inline
:func:`~clm.notebooks.slide_writer.format_narrative_cell` (and
``update_narrative``) and the companion
:func:`~clm.slides.voiceover_tools._format_companion_cell_body` (and
``render_companion_update``) -- used to turn every non-blank line into its
own top-level bullet, flattening continuations and children.

The round trip: render a cell body the way ``accept`` does, strip the
comment tokens the way a reader hands the text to a writer, rewrite it,
and expect identical bytes.
"""

from __future__ import annotations

import pytest

from clm.core.slide_text.bullet_wrap import format_narrative_body, parse_narrative_text
from clm.notebooks.slide_writer import format_narrative_cell, update_narrative
from clm.slides.voiceover_tools import _format_companion_cell_body, render_companion_update
from clm.voiceover.harvest_accept import Bullet, _render_body

_LONG = (
    "Dieser Punkt ist absichtlich so lang, dass er über die Breite von achtzig "
    "Spalten hinausgeht und deshalb umbrochen werden muss, mit `inline code`."
)
_CHILD = (
    "Ein Unterpunkt, der ebenfalls lang genug ist, um auf mehrere Zeilen "
    "umbrochen zu werden, damit die Einrückung geprüft wird."
)

_BULLETS = [
    Bullet("Ein kurzer Punkt."),
    Bullet(_LONG, (_CHILD, "kurzes Kind")),
    Bullet("Noch ein kurzer Punkt.", ("einziges Kind",)),
]


def _accept_body(comment_token: str) -> str:
    """The body ``clm harvest accept`` writes for ``_BULLETS``."""
    return _render_body(_BULLETS, comment_token)


def _reader_text(body: str, comment_token: str) -> str:
    """Strip the comment token and the one space after it, keeping indent --
    the text a caller hands back to a writer."""
    out = []
    for line in body.split("\n"):
        rest = line[len(comment_token) :]
        out.append(rest[1:] if rest.startswith(" ") else rest)
    return "\n".join(out).strip()


@pytest.mark.parametrize("token", ["#", "//"])
class TestRoundTrip:
    def test_accept_body_is_wrapped_and_nested(self, token: str) -> None:
        body = _accept_body(token)
        assert f"\n{token}   - " in body  # a child bullet
        assert any(
            line.startswith(f"{token}   ") and not line.startswith(f"{token}   - ")
            for line in body.split("\n")
        )  # a continuation line

    def test_format_narrative_cell_round_trips(self, token: str) -> None:
        body = _accept_body(token)
        cell = format_narrative_cell(_reader_text(body, token), "de", comment_token=token)
        header = f'{token} %% [markdown] lang="de" tags=["voiceover"]'
        assert cell == header + "\n" + body

    def test_companion_body_round_trips(self, token: str) -> None:
        body = _accept_body(token)
        assert _format_companion_cell_body(_reader_text(body, token), token) == body.split("\n")

    def test_update_narrative_rewrite_is_byte_identical(self, token: str) -> None:
        body = _accept_body(token)
        deck = (
            f'{token} %% [markdown] lang="de" tags=["slide"]\n'
            f"{token} ## Folie\n"
            "\n"
            f'{token} %% [markdown] lang="de" tags=["voiceover"]\n'
            f"{body}\n"
        )
        updated = update_narrative(deck, {0: _reader_text(body, token)}, "de", comment_token=token)
        assert updated == deck

    def test_render_companion_update_is_byte_identical(self, token: str) -> None:
        body = _accept_body(token)
        companion = (
            f'{token} %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"\n{body}\n'
        )
        updated = render_companion_update(
            companion, {"intro": _reader_text(body, token)}, "de", comment_token=token
        )
        assert updated == companion


class TestParse:
    def test_continuation_and_children(self) -> None:
        text = "- eins\n  weiter\n  - kind\n    kind weiter\n- zwei"
        items = parse_narrative_text(text)
        assert [(i.text, i.children) for i in items] == [  # type: ignore[union-attr]
            ("eins weiter", ["kind kind weiter"]),
            ("zwei", []),
        ]

    def test_plain_lines_stay_one_bullet_each(self) -> None:
        """Text without layout keeps the pre-#1162 one-thought-per-line rule."""
        assert format_narrative_body("eins\nzwei\n- drei") == [
            "#",
            "# - eins",
            "# - zwei",
            "# - drei",
        ]

    def test_blank_and_marker_lines_verbatim(self) -> None:
        assert format_narrative_body("- eins\n\n**[Revisit]** später\n  eingerückt", "//") == [
            "//",
            "// - eins",
            "//",
            "// **[Revisit]** später",
            "// - eingerückt",
        ]

    def test_long_single_line_bullet_is_wrapped(self) -> None:
        body = format_narrative_body(f"- {_LONG}")
        assert len(body) > 2
        assert all(len(line) <= 80 for line in body)
        assert body[2].startswith("#   ")
