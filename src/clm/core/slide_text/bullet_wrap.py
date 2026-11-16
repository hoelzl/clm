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

:func:`render_narrative_bullets` renders a whole bullet list (one nesting
level) in that style, and :func:`format_narrative_body` parses narrative
text that may already carry the deck layout -- wrapped continuations and
indented child bullets -- back into that list before rendering it, so every
narrative-cell writer (``clm harvest accept``, ``update_narrative``, the
companion writers) agrees on one model of a bullet (#1162).
:func:`narrative_prompt_text` renders the same model as *logical* bullets --
one line per bullet, children indented, continuations unwrapped -- for the
narration the LLM prompts carry (#1280).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import cache

__all__ = [
    "NARRATIVE_WIDTH",
    "NarrativeBullet",
    "format_narrative_body",
    "narrative_prompt_text",
    "parse_narrative_text",
    "render_narrative_bullets",
    "wrap_bullet",
]

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


# ---------------------------------------------------------------------------
# Whole narrative bodies (#1162)
# ---------------------------------------------------------------------------


@dataclass
class NarrativeBullet:
    """One top-level narrative bullet and its (one nesting level) children."""

    text: str
    children: list[str] = field(default_factory=list)


def render_narrative_bullets(
    bullets: Iterable[tuple[str, Sequence[str]]], comment_token: str
) -> list[str]:
    """Render ``(text, children)`` bullets as wrapped comment lines.

    A top-level bullet renders as ``<tok> - text`` with its continuation
    under the text (``<tok>   ``); a child as ``<tok>   - text`` with its
    continuation under *its* text (``<tok>     ``)."""
    top, top_cont = f"{comment_token} - ", f"{comment_token}   "
    child, child_cont = f"{comment_token}   - ", f"{comment_token}     "
    lines: list[str] = []
    for text, children in bullets:
        lines.extend(wrap_bullet(text, top, top_cont))
        for child_text in children:
            lines.extend(wrap_bullet(child_text, child, child_cont))
    return lines


def _is_bullet(stripped: str) -> bool:
    return stripped.startswith("- ")


def parse_narrative_text(text: str) -> list[NarrativeBullet | str]:
    """Parse narrative text (comment tokens already stripped) into bullets.

    Items are :class:`NarrativeBullet` or a verbatim line (``""`` for a
    blank line, or a ``**[`` marker line). The layout rules:

    * an unindented line opens a new top-level bullet, whether or not it
      starts with ``- `` (plain text keeps meaning "one thought per line");
    * an indented ``- `` line under a bullet is a child of that bullet;
    * any other indented line under a bullet continues the most recent
      bullet or child (a wrapped line), joined with one space;
    * a blank line or a ``**[`` line ends the current bullet, so an
      indented line after it opens a new bullet.
    """
    items: list[NarrativeBullet | str] = []
    current: NarrativeBullet | None = None
    for raw in text.strip().split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            items.append("")
            current = None
            continue
        if stripped.startswith("**["):
            items.append(stripped)
            current = None
            continue
        if line[0] in " \t" and current is not None:
            if _is_bullet(stripped):
                current.children.append(stripped[2:])
            elif current.children:
                current.children[-1] += " " + stripped
            else:
                current.text += " " + stripped
            continue
        current = NarrativeBullet(stripped[2:] if _is_bullet(stripped) else stripped)
        items.append(current)
    return items


def format_narrative_body(text: str, comment_token: str = "#") -> list[str]:
    """Narrative text -> the cell body lines in the deck style.

    The body opens with a bare ``comment_token`` line (the blank comment
    line after the header); bullets are wrapped via
    :func:`render_narrative_bullets`, blank lines become a bare
    ``comment_token`` and ``**[`` marker lines are kept verbatim. Text that
    already carries the deck layout round-trips unchanged."""
    body = [comment_token]
    for item in parse_narrative_text(text):
        if isinstance(item, NarrativeBullet):
            body.extend(render_narrative_bullets([(item.text, item.children)], comment_token))
        elif item:
            body.append(f"{comment_token} {item}")
        else:
            body.append(comment_token)
    return body


def narrative_prompt_text(text: str) -> str:
    """Narrative text -> logical bullets, the form narration takes in an LLM prompt.

    ``text`` is narrative text with the comment tokens stripped but the
    indentation kept (the input :func:`parse_narrative_text` reads). The
    result has one line per bullet -- ``- text`` for a top-level bullet,
    ``  - text`` for a child -- with wrapped continuations joined onto
    their bullet, so the model sees the nesting but none of the
    80-column layout breaks; the writers re-wrap its answer. Markdown is
    kept verbatim, ``**[`` marker lines are kept, and runs of blank lines
    collapse to one (#1280).
    """
    lines: list[str] = []
    for item in parse_narrative_text(text):
        if isinstance(item, NarrativeBullet):
            lines.append(f"- {item.text}")
            lines.extend(f"  - {child}" for child in item.children)
        elif item or (lines and lines[-1]):
            lines.append(item)
    return "\n".join(lines).strip()
