"""Slug derivation for slide_id values.

Slide IDs are EN-derived, lowercase-kebab, ASCII-only. They survive title
edits once assigned: the slug algorithm is *only* used when generating a
fresh ID. See ``handover-slide-format-redesign-clm.md`` §2.3 for the design
rationale.

Public API:

- :func:`slugify` — turn a free-form title into a slug
- :func:`strip_preserve_marker` — drop the leading ``!`` from a preserve-marked id
- :func:`is_preserved` — whether a slide_id carries the preserve marker
- :func:`is_valid_slug` — check that a slug matches the canonical format
- :func:`resolve_collision` — append ``-2``/``-3``/... to disambiguate
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

PRESERVE_MARKER = "!"
MAX_SLUG_LENGTH = 30

# Stop words dropped only when the result would otherwise exceed the cap.
# Kept deliberately short — the goal is "remove filler", not "rewrite the
# title in telegraphese".
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "but",
        "with",
        "by",
        "as",
    }
)

# German character transliteration (applied before ASCII fold so we get
# the conventional spelling, not "o"/"a"/"u"/"s").
_GERMAN_MAP = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
}

# Markdown formatting we want to strip before tokenizing.
_MARKDOWN_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MARKDOWN_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MARKDOWN_CODE = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_TAG = re.compile(r"<[^>]+>")


def strip_preserve_marker(slide_id: str) -> str:
    """Return the bare id with any leading ``!`` removed."""
    if slide_id.startswith(PRESERVE_MARKER):
        return slide_id[len(PRESERVE_MARKER) :]
    return slide_id


def is_preserved(slide_id: str) -> bool:
    """Whether ``slide_id`` carries the preserve marker."""
    return slide_id.startswith(PRESERVE_MARKER)


def _strip_markdown(text: str) -> str:
    text = _MARKDOWN_BOLD.sub(r"\1", text)
    text = _MARKDOWN_ITALIC.sub(r"\1", text)
    text = _MARKDOWN_CODE.sub(r"\1", text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    return text


def _transliterate(text: str) -> str:
    for src, dst in _GERMAN_MAP.items():
        text = text.replace(src, dst)
    # Fold any remaining non-ASCII (accents, smart quotes, etc.) to ASCII.
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    # Split on any run of non-alphanumeric characters.
    return [tok for tok in re.split(r"[^a-z0-9]+", text) if tok]


def _truncate_to_cap(tokens: list[str], cap: int) -> list[str]:
    """Trim from the right at a word boundary to stay within ``cap`` chars."""
    if not tokens:
        return tokens
    out: list[str] = []
    used = 0
    for tok in tokens:
        # +1 for the joining hyphen when appending after the first token.
        extra = len(tok) + (1 if out else 0)
        if used + extra > cap:
            break
        out.append(tok)
        used += extra
    if not out:
        # First token alone exceeds the cap — hard-truncate it. Better than
        # returning an empty slug.
        out = [tokens[0][:cap]]
    return out


def slugify(text: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Derive a kebab-case ASCII slug from free-form title text.

    Steps:

    1. Strip common markdown formatting (``**bold**``, ``*italic*``,
       ``` `code` ```, ``[link](url)``, HTML tags).
    2. Transliterate German umlauts/ß to their conventional digraphs, then
       NFKD-fold remaining non-ASCII characters.
    3. Lowercase and tokenize on non-alphanumeric runs.
    4. If the joined result exceeds ``max_length``, drop stop-words and try
       again. Then trim at a word boundary to fit the cap.

    Returns an empty string when no usable token can be extracted; callers
    should treat that as a refusal.
    """
    cleaned = _strip_markdown(text)
    cleaned = _transliterate(cleaned)
    tokens = _tokenize(cleaned)
    if not tokens:
        return ""

    joined = "-".join(tokens)
    if len(joined) <= max_length:
        return joined

    # Drop stop-words and try again (preserve first token even if it's a
    # stop-word — a slug "of-x" is more useful than just "x").
    pruned = [tokens[0]] + [t for t in tokens[1:] if t not in _STOP_WORDS]
    pruned = _dedupe_first(pruned, tokens[0])
    joined = "-".join(pruned)
    if len(joined) <= max_length:
        return joined

    return "-".join(_truncate_to_cap(pruned, max_length))


def _dedupe_first(tokens: list[str], head: str) -> list[str]:
    """Avoid duplicating the head token if it survives the stop-word filter."""
    if len(tokens) >= 2 and tokens[0] == tokens[1] == head:
        return [tokens[0]] + tokens[2:]
    return tokens


def is_valid_slug(slide_id: str, *, max_length: int = MAX_SLUG_LENGTH) -> bool:
    """Check that ``slide_id`` matches the canonical format.

    A valid slide_id is ``[!]?[a-z0-9]+(-[a-z0-9]+)*`` and the bare form
    fits within ``max_length`` characters. The optional leading ``!`` is
    the preserve marker; it does not count toward the length cap.
    """
    bare = strip_preserve_marker(slide_id)
    if not bare:
        return False
    if len(bare) > max_length:
        return False
    return bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", bare))


def resolve_collision(base: str, used: Iterable[str], *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Append ``-2``/``-3``/... until the slug is unique within ``used``.

    Comparisons are done on the bare (marker-stripped) form so that
    ``!intro`` and ``intro`` correctly count as a collision. The suffixed
    result always fits within ``max_length`` — the base is trimmed at a
    word boundary if needed so that minted ids never fail
    :func:`is_valid_slug` (issue #233).
    """
    used_bare = {strip_preserve_marker(s) for s in used}
    if base not in used_bare:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        head = "-".join(_truncate_to_cap(base.split("-"), max_length - len(suffix)))
        candidate = f"{head}{suffix}"
        if candidate not in used_bare:
            return candidate
        n += 1
