"""Extract proposal text from slide cells that have no markdown heading.

Used by ``clm slides assign-ids`` to classify and propose slugs for the
"extractable" category (headingless but with some salient first line).

Three categories, matching §2.3 of ``handover-slide-format-redesign-clm.md``:

- ``HEADED``        — the cell has at least one ``##``-style heading. The
                      assign-ids algorithm handles this directly via
                      :func:`extract_heading`.
- ``EXTRACTABLE``   — no heading, but a first bullet, a prominent bold
                      line, or an ``<img alt="...">`` provides enough text
                      to suggest a slug. The tool refuses by default and
                      lists the proposal; ``--accept-content-derived``
                      bulk-accepts.
- ``NON_EXTRACTABLE`` — pure-image without alt, divider, empty cell, or
                        anything else that yields no usable text. Hard
                        refuse — the author has to write the id by hand.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

# Order matters: HEADED beats EXTRACTABLE beats NON_EXTRACTABLE.


class Category(enum.Enum):
    HEADED = "headed"
    EXTRACTABLE = "extractable"
    NON_EXTRACTABLE = "non_extractable"


@dataclass(frozen=True)
class Extraction:
    """What we managed to pull out of a cell.

    ``category`` is the classification. ``text`` is the raw extracted
    string (markdown formatting intact — slugification happens later via
    :func:`clm.slides.slug.slugify`). ``source`` identifies *which* extractor
    matched (``heading``/``bullet``/``bold``/``img_alt``) for diagnostics.
    """

    category: Category
    text: str = ""
    source: str = ""


# Markdown heading: ``# ## Title`` (Python comment prefix included).
_HEADING_RE = re.compile(r"^#\s+(#{1,6})\s+(?P<text>.+?)\s*$")

# Bullet: ``# - Text`` or ``# * Text`` (Python comment prefix included).
_BULLET_RE = re.compile(r"^#\s+[-*]\s+(?P<text>.+?)\s*$")

# Numbered list: ``# 1. Text``.
_NUMBERED_RE = re.compile(r"^#\s+\d+\.\s+(?P<text>.+?)\s*$")

# Bold *line*: a line that is essentially **bold text** with little else.
_BOLD_LINE_RE = re.compile(r"^#\s+\*\*([^*]+)\*\*\s*$")

# Image with alt text.
_IMG_ALT_RE = re.compile(r'<img[^>]*\balt="([^"]+)"', re.IGNORECASE)


def _iter_content_lines(content: str) -> list[str]:
    """Yield lines stripped of trailing whitespace, retaining the ``#`` prefix."""
    return [ln.rstrip() for ln in content.splitlines()]


def extract_heading(content: str) -> str | None:
    """Return the text of the first markdown heading in the cell, or None."""
    for line in _iter_content_lines(content):
        m = _HEADING_RE.match(line)
        if m:
            return m.group("text").strip()
    return None


def classify(content: str) -> Extraction:
    """Classify a cell's content and return whatever proposal text we can find.

    Precedence within the EXTRACTABLE category: first bullet/numbered item,
    then prominent bold line, then first ``<img alt="...">``. Whichever
    appears first in source order wins among items of the same precedence.
    """
    lines = _iter_content_lines(content)

    # HEADED takes absolute priority.
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            return Extraction(Category.HEADED, m.group("text").strip(), "heading")

    # Otherwise scan once for any extractable signal. We collect the first
    # match of each kind so we can apply the bullet > bold > img precedence
    # explicitly rather than depending on source order alone.
    first_bullet: str | None = None
    first_bold: str | None = None
    first_img_alt: str | None = None

    for line in lines:
        if first_bullet is None:
            m = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
            if m:
                first_bullet = m.group("text").strip()
        if first_bold is None:
            m = _BOLD_LINE_RE.match(line)
            if m:
                first_bold = m.group(1).strip()
        if first_img_alt is None:
            m = _IMG_ALT_RE.search(line)
            if m:
                first_img_alt = m.group(1).strip()

    if first_bullet:
        return Extraction(Category.EXTRACTABLE, first_bullet, "bullet")
    if first_bold:
        return Extraction(Category.EXTRACTABLE, first_bold, "bold")
    if first_img_alt:
        return Extraction(Category.EXTRACTABLE, first_img_alt, "img_alt")

    return Extraction(Category.NON_EXTRACTABLE)


def cell_text_for_llm(content: str, *, max_chars: int = 1500) -> str:
    """Return a cleaned, plain-text rendering of the cell for LLM input.

    Strips the ``# `` comment prefix, drops blank lines, and caps the
    total length so we don't blow up the prompt with huge cells.
    """
    parts: list[str] = []
    used = 0
    for line in _iter_content_lines(content):
        if not line.strip():
            continue
        # Drop the Python comment prefix on markdown lines.
        if line.startswith("# "):
            stripped = line[2:]
        elif line.startswith("#"):
            stripped = line[1:]
        else:
            stripped = line
        if not stripped.strip():
            continue
        parts.append(stripped)
        used += len(stripped) + 1
        if used >= max_chars:
            parts.append("...")
            break
    return "\n".join(parts)
