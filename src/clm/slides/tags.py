"""Canonical tag definitions for CLM slide files.

This module is the single source of truth for all recognized cell tags.
Both the notebook worker (``jupyter_utils.py``) and the slide tooling
(``clm.slides.validator``, ``clm.slides.normalizer``, etc.) import from here.

Tag Semantics
-------------

===========  ====================================================================
Tag          Purpose
===========  ====================================================================
slide        Starts a new slide (sets slideshow metadata)
subslide     Starts a subslide within a slide
keep         Code cell contents retained in all output variants
start        Starter code for live coding (kept in code-along, deleted in others)
completed    Full solution following a ``start`` cell (deleted in code-along)
alt          Discussion/alternative content for completed variant only
answer       Solution text (cleared in code-along, shown in completed/speaker)
notes        Brief speaker hints (speaker output only)
voiceover    Text to read aloud (speaker output only)
workshop     Marks the heading cell of a workshop section (structural metadata)
private      Cell visible only in private documents
del          Cell deleted from all outputs
nodataurl    Prevents data-URL inlining for images
===========  ====================================================================
"""

from __future__ import annotations

# --- Slide structure tags ---
# Tags that interact with slideshow metadata (slide boundaries, narrative).
# NOTE: This matches the original _SLIDE_TAGS in jupyter_utils.py, which
# includes notes/voiceover because they participate in slide-tag conflict
# detection (a cell shouldn't have both "slide" and "notes").
SLIDE_TAGS: frozenset[str] = frozenset({"slide", "subslide", "notes", "voiceover"})

# --- Visibility tags ---
# Tags that prevent a cell from appearing in public output.
PRIVATE_TAGS: frozenset[str] = frozenset({"notes", "voiceover", "private"})

# --- Content-control tags (code cells) ---
CODE_CONTENT_TAGS: frozenset[str] = frozenset({"keep", "start", "completed"})

# --- Structural metadata tags ---
# Tags that carry structural meaning but don't affect output processing.
STRUCTURAL_TAGS: frozenset[str] = frozenset({"workshop"})

# --- Per-cell-type valid tag sets ---
# These are the complete sets used by get_invalid_code_tags / get_invalid_markdown_tags.

EXPECTED_GENERIC_TAGS: frozenset[str] = frozenset(
    SLIDE_TAGS | PRIVATE_TAGS | STRUCTURAL_TAGS | frozenset({"alt", "completed", "del"})
)

EXPECTED_CODE_TAGS: frozenset[str] = frozenset(CODE_CONTENT_TAGS | EXPECTED_GENERIC_TAGS)

EXPECTED_MARKDOWN_TAGS: frozenset[str] = frozenset(
    frozenset({"notes", "voiceover", "answer", "nodataurl"}) | EXPECTED_GENERIC_TAGS
)

# --- Convenience: all recognized tags ---
ALL_VALID_TAGS: frozenset[str] = frozenset(EXPECTED_CODE_TAGS | EXPECTED_MARKDOWN_TAGS)
