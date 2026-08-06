"""Header ``clm:`` directive markers that the build itself must read.

A deck can carry per-file directives as comments in its header (the lines
before the first ``<token> %%`` cell marker). Most directives are authoring
concerns and live with their checks in ``clm.slides.validator``; this module
holds the scanner plus the directives the *build* consumes (Phase 8 S1,
#802) — currently only ``clm: no-compile``, which CMake export reads.
"""

import re

# Header marker for decks whose code export legitimately cannot compile
# outside the kernel (e.g. xeus-specific includes, deliberate error
# demonstrations). The CMake generation (#333) marks such decks
# EXCLUDE_FROM_ALL: still buildable explicitly, skipped by "build all" and
# by the CI compile check.
NO_COMPILE_MARKER = "clm: no-compile"
_NO_COMPILE_MARKER_RE = re.compile(r"^(?:#|//)\s*clm:\s*no-compile\s*$")


def has_header_marker(text: str, comment_token: str, marker_re: re.Pattern[str]) -> bool:
    """Whether the deck's file header contains a ``clm:`` directive comment.

    Scans only the file header — lines before the first ``<token> %%`` cell
    marker — so the directive is a per-file declaration, not cell content.
    """
    cell_marker = f"{comment_token} %%"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(cell_marker):
            break
        if marker_re.match(stripped):
            return True
    return False


def has_no_compile_marker(text: str, comment_token: str = "#") -> bool:
    """Whether the deck opts out of the default code-export build (#333)."""
    return has_header_marker(text, comment_token, _NO_COMPILE_MARKER_RE)
