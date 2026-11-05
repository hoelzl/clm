"""Wrap one narrative bullet to the deck style (#1101).

Narrative cells (``voiceover``/``notes``) write their bullets as comment
lines — ``# - text`` (Python) or ``// - text`` (C-family) — wrapped at
:data:`NARRATIVE_WIDTH` columns with a hanging indent that aligns the
continuation under the bullet text (``#   ``/``//   ``; a child bullet's
continuation aligns under *its* text, ``#     ``).

The wrap is markdown-safe:

* it breaks only at spaces, never inside an inline-code span (a backtick
  span may contain spaces) — so a URL or any other space-free run is never
  split;
* a word longer than the width stays whole on its own line;
* it never starts a continuation line with a token markdown would read as a
  new block (a list marker such as ``-`` or ``1.``, a heading ``#``, a
  blockquote ``>``, a setext/thematic-break run such as ``---``, a code
  fence, or an HTML tag) — such a token stays on the preceding line;
* a bullet that already fits is returned byte-for-byte unchanged.
"""

from __future__ import annotations

import re
from functools import cache

__all__ = ["NARRATIVE_WIDTH", "wrap_bullet"]

#: The column width the decks wrap narrative bullets at.
NARRATIVE_WIDTH = 80

# Tokens that open a markdown block when they start a line: list markers,
# headings, blockquotes, setext underlines / thematic breaks, table pipes.
_BLOCK_MARKER = re.compile(r"[-+*=_#>|~]+|\d{1,9}[.)]")
# A blockquote opens on ``>`` even without a following space (``>5``).
_BLOCK_PREFIXES = ("```", "~~~", "<", ">")


@cache
def _closing_run(ticks: int) -> re.Pattern[str]:
    """A backtick run of exactly ``ticks`` backticks (CommonMark code span)."""
    return re.compile(rf"(?<!`)`{{{ticks}}}(?!`)")


def _escaped(text: str, pos: int) -> bool:
    """Whether ``text[pos]`` is backslash-escaped (odd backslash run before it)."""
    backslashes = 0
    while pos - backslashes - 1 >= 0 and text[pos - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _atoms(text: str) -> list[tuple[str, str]]:
    """Split ``text`` at space runs outside code spans.

    Returns ``(separator, atom)`` pairs, ``separator`` being the space run
    that preceded the atom (``""`` for the first)."""
    atoms: list[tuple[str, str]] = []
    n = len(text)
    i = 0
    while i < n:
        j = i
        while j < n and text[j] == " ":
            j += 1
        if j >= n:
            break
        k = j
        while k < n and text[k] != " ":
            if text[k] != "`" or _escaped(text, k):
                # An escaped backtick (``\```) cannot open a code span.
                k += 1
                continue
            run_end = k
            while run_end < n and text[run_end] == "`":
                run_end += 1
            close = _closing_run(run_end - k).search(text, run_end)
            # An unmatched opening run is literal backticks, not a span.
            k = run_end if close is None else close.end()
        atoms.append((text[i:j], text[j:k]))
        i = k
    return atoms


def _opens_block(atom: str) -> bool:
    return bool(_BLOCK_MARKER.fullmatch(atom)) or atom.startswith(_BLOCK_PREFIXES)


def _glue_block_markers(atoms: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Fuse every block-opening atom onto its predecessor so that no line
    break can ever land in front of it."""
    glued: list[tuple[str, str]] = []
    for sep, atom in atoms:
        if glued and _opens_block(atom):
            prev_sep, prev = glued[-1]
            glued[-1] = (prev_sep, prev + sep + atom)
        else:
            glued.append((sep, atom))
    return glued


def wrap_bullet(
    text: str, first_prefix: str, cont_prefix: str, width: int = NARRATIVE_WIDTH
) -> list[str]:
    """Render ``text`` as lines: the first prefixed ``first_prefix``, each
    continuation ``cont_prefix``, every line at most ``width`` columns unless
    a single unbreakable atom is longer.

    ``text`` is one bullet's single-line content (no newline)."""
    if len(first_prefix) + len(text) <= width:
        return [first_prefix + text]
    atoms = _glue_block_markers(_atoms(text))
    if not atoms:
        return [first_prefix + text]
    lines: list[str] = []
    current = first_prefix + atoms[0][1]
    for sep, atom in atoms[1:]:
        if len(current) + len(sep) + len(atom) <= width:
            current += sep + atom
        else:
            lines.append(current)
            current = cont_prefix + atom
    lines.append(current)
    return lines
