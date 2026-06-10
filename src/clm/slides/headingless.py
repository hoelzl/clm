"""Extract proposal text from slide cells that have no markdown heading.

Used by ``clm slides assign-ids`` to classify and propose slugs for the
"extractable" category (headingless but with some salient first line).

Three categories, matching §2.3 of ``handover-slide-format-redesign-clm.md``:

- ``HEADED``        — the cell has at least one ``##``-style heading. The
                      assign-ids algorithm handles this directly via
                      :func:`extract_heading`.
- ``EXTRACTABLE``   — no heading, but a first bullet, a prominent bold
                      line, an ``<img alt="...">``, or an image filename
                      (``<img src="...">`` without alt, #233) provides
                      enough text to suggest a slug. The tool refuses by
                      default and lists the proposal;
                      ``--accept-content-derived`` bulk-accepts.
- ``NON_EXTRACTABLE`` — divider, empty cell, or anything else that yields
                        no usable text. Hard refuse — the author has to
                        write the id by hand.
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
    matched (``heading``/``bullet``/``bold``/``img_alt``/``img_src``/``prose``)
    for diagnostics.
    """

    category: Category
    text: str = ""
    source: str = ""


# Each matcher anchors on the line-comment prefix of either family — ``#``
# (python/rust) or ``//`` (cpp/csharp/java/typescript) — since a deck is
# single-language the alternation never cross-matches.
#
# Markdown heading: ``# ## Title`` / ``// ## Title`` (comment prefix included).
_HEADING_RE = re.compile(r"^(?:#|//)\s+(#{1,6})\s+(?P<text>.+?)\s*$")

# Bullet: ``# - Text`` or ``# * Text`` (comment prefix included).
_BULLET_RE = re.compile(r"^(?:#|//)\s+[-*]\s+(?P<text>.+?)\s*$")

# Numbered list: ``# 1. Text``.
_NUMBERED_RE = re.compile(r"^(?:#|//)\s+\d+\.\s+(?P<text>.+?)\s*$")

# Bold *line*: a line that is essentially **bold text** with little else.
_BOLD_LINE_RE = re.compile(r"^(?:#|//)\s+\*\*([^*]+)\*\*\s*$")

# Image with alt text.
_IMG_ALT_RE = re.compile(r'<img[^>]*\balt="([^"]+)"', re.IGNORECASE)

# Image src attribute — fallback when no alt text exists (#233). Matched
# per line without requiring a closing ``>``, so a multi-line ``<img``
# tag (src on the first line, style attributes on later lines) is still
# recognized instead of leaking tag fragments into prose extraction.
_IMG_SRC_RE = re.compile(r'<img[^>]*\bsrc="([^"]+)"', re.IGNORECASE)

# HTML tag (used to drop naked <img> noise before prose extraction).
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Unterminated opening tag fragment at end of line — the first line of a
# multi-line ``<img src="..."`` tag whose attributes continue on later
# lines (#233). Requires a tag-name character after ``<`` so legit prose
# like ``a < b`` is untouched.
_UNCLOSED_TAG_RE = re.compile(r"<[A-Za-z!/][^>]*$")

# Inline markdown formatting to unwrap before treating a line as prose.
_BOLD_INLINE_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_INLINE_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_LINK_INLINE_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Terminal punctuation stripped from the end of a prose line.
_TRAILING_PUNCT_RE = re.compile(r"[:.?!]+\s*$")

# Used to reject lines that contain no word characters after cleanup.
_WORD_RE = re.compile(r"\w")


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


def _extract_first_prose_line(lines: list[str]) -> str | None:
    """Return the first non-empty jupytext-markdown prose line, or None.

    Only lines that look like jupytext markdown content (``# something``)
    are considered — bare code lines never qualify as prose, which keeps
    code cells routed to the AST extractor instead of accidentally
    matching here on `import`/`def` lines. Lines that reduce to empty
    text after stripping HTML, inline markdown formatting, and trailing
    terminal punctuation are skipped; lines with no word characters at
    all are rejected. A tag opened on one line and closed on a later one
    (a multi-line ``<img …`` with per-line attributes, #233) is consumed
    across lines so its attribute soup never reads as prose.
    """
    in_tag = False
    for line in lines:
        # Skip blank markdown lines and any non-markdown line (e.g. raw
        # code statements in a code cell). Recognize either comment family.
        if line.startswith("# "):
            content = line[2:]
        elif line.startswith("// "):
            content = line[3:]
        else:
            continue
        if in_tag:
            # Inside a multi-line tag: consume up to its ``>`` (or the
            # whole line when the tag is still open).
            if ">" not in content:
                continue
            content = content.split(">", 1)[1]
            in_tag = False
        content = _HTML_TAG_RE.sub("", content)
        m = _UNCLOSED_TAG_RE.search(content)
        if m is not None:
            in_tag = True
            content = content[: m.start()]
        content = _BOLD_INLINE_RE.sub(r"\1", content)
        content = _ITALIC_INLINE_RE.sub(r"\1", content)
        content = _CODE_INLINE_RE.sub(r"\1", content)
        content = _LINK_INLINE_RE.sub(r"\1", content)
        content = _TRAILING_PUNCT_RE.sub("", content).strip()
        if not content:
            continue
        if not _WORD_RE.search(content):
            continue
        return content
    return None


def classify(content: str) -> Extraction:
    """Classify a cell's content and return whatever proposal text we can find.

    Precedence within the EXTRACTABLE category: first bullet/numbered item,
    then prominent bold line, then first ``<img alt="...">``, then the
    first non-empty prose line, then — last, #233 — the filename stem of
    the first ``<img src="...">`` without alt text. Whichever appears
    first in source order wins among items of the same precedence.
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
    first_img_src: str | None = None

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
        if first_img_src is None:
            m = _IMG_SRC_RE.search(line)
            if m:
                first_img_src = m.group(1).strip()

    if first_bullet:
        return Extraction(Category.EXTRACTABLE, first_bullet, "bullet")
    if first_bold:
        return Extraction(Category.EXTRACTABLE, first_bold, "bold")
    if first_img_alt:
        return Extraction(Category.EXTRACTABLE, first_img_alt, "img_alt")

    prose = _extract_first_prose_line(lines)
    if prose:
        return Extraction(Category.EXTRACTABLE, prose, "prose")

    if first_img_src:
        # Image-only cell with no alt text: derive from the image
        # filename stem (#233) — ``img/robots-playing-checkers.png`` ->
        # ``img robots-playing-checkers``. Real prose (a caption) still
        # wins above; this only rescues the previously hard-refused case.
        stem = _img_src_stem(first_img_src)
        if stem:
            return Extraction(Category.EXTRACTABLE, f"img {stem}", "img_src")

    return Extraction(Category.NON_EXTRACTABLE)


def _img_src_stem(src: str) -> str:
    """Filename stem of an image src (path/URL noise and extension dropped)."""
    name = src.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    # Drop a URL query/fragment, then the extension.
    name = name.split("?", 1)[0].split("#", 1)[0]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


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
        # Drop the comment prefix on markdown lines (either comment family).
        if line.startswith("# "):
            stripped = line[2:]
        elif line.startswith("// "):
            stripped = line[3:]
        elif line.startswith("#"):
            stripped = line[1:]
        elif line.startswith("//"):
            stripped = line[2:]
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
