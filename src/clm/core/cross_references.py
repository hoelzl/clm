"""Cross-references between notebooks (Issue #17).

Design-first scaffold. The only piece implemented here is the
decision-agnostic *extractor*: it pulls ``clm:`` references out of slide
markdown text. Interpreting a reference (topic-id vs notebook-stem vs an
explicit author-assigned id) and resolving it to a renamed, per-artifact
relative href is intentionally left unimplemented — those depend on the
open product decisions recorded in
``docs/claude/design/cross-references.md`` (Decisions 1, 2, 4, 5).

Nothing in the build pipeline calls this module yet; importing it has no
effect on build output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The cross-reference URI scheme (Decision 1, default option A). A reference
# is authored as a normal Markdown link whose href uses this scheme, e.g.
# ``[Functions workshop](clm:functions_workshop)``. Only the href is
# rewritten at build time; the link text is left untouched.
SCHEME = "clm:"

# Matches the href of a Markdown link whose target uses the ``clm:`` scheme.
# Captures the reference (everything after ``clm:`` up to the closing paren).
# Deliberately conservative: it only fires inside the ``](...)`` href slot,
# so ordinary links and image links (``![alt](path)`` — note the leading
# ``!`` is *not* excluded here because the reference still wouldn't use the
# ``clm:`` scheme for an image) are left alone.
_CROSS_REF_RE = re.compile(r"\]\(\s*clm:\s*(?P<ref>[^)\s]+)\s*\)")


@dataclass(frozen=True)
class ResolvedReference:
    """A cross-reference resolved to a concrete, per-artifact link target.

    ``href`` is the relative path from the *referring* output file to the
    *target* output artifact of the same ``(language, kind, format)``.
    """

    reference: str
    href: str


def extract_cross_references(text: str) -> list[str]:
    """Return every ``clm:`` reference found in *text*, in document order.

    Decision-agnostic: returns the raw reference strings (the part after
    ``clm:``) exactly as authored. Interpreting them — splitting an
    optional ``/notebook-stem`` disambiguator or ``#anchor`` — is the
    resolver's job and is deferred until the identifier scheme is chosen
    (Decision 2/3). Duplicates are preserved so callers can report every
    occurrence.

    Args:
        text: Markdown (or percent-format slide) source.

    Returns:
        List of reference strings, e.g. ``["functions_workshop", "intro"]``.
    """
    return [m.group("ref") for m in _CROSS_REF_RE.finditer(text)]


def has_cross_references(text: str) -> bool:
    """Return True if *text* contains at least one ``clm:`` reference."""
    return _CROSS_REF_RE.search(text) is not None


class CrossReferenceResolver(Protocol):
    """Resolves a ``clm:`` reference to a per-artifact relative href.

    A concrete implementation is built once per :class:`~clm.core.course.Course`
    after sections and notebook numbers are assigned, so it knows every
    output notebook's renamed filename. ``resolve`` returns ``None`` when
    the target is not part of the (possibly section-filtered) course or is
    not produced for the requested ``(language, kind, format)`` variant —
    the caller then applies the missing-target policy (Decision 4).

    Not implemented yet: the concrete resolver lands once Decisions 1, 2,
    4 and 5 are settled (see ``docs/claude/design/cross-references.md``).
    """

    def resolve(
        self,
        reference: str,
        *,
        from_output_file: Path,
        language: str,
        kind: str,
        format: str,
    ) -> ResolvedReference | None:
        """Resolve *reference* for one output artifact, or return ``None``."""
        ...
