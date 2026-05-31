"""Cross-references between notebooks (Issue #17).

Authors link from one notebook to another with a custom Markdown link
scheme::

    See the [Functions workshop](clm:functions_workshop) for exercises.

``clm:<reference>`` survives jupytext / nbconvert untouched (it is a valid
URI) and is regex-detectable. Only the **href** is rewritten at build time;
the link text is left as authored.

This module provides three layers:

* :func:`extract_cross_references` / :func:`has_cross_references` — a pure,
  decision-agnostic scanner that pulls the raw reference strings out of
  slide markdown.
* :class:`CrossReferenceResolver` — built once per
  :class:`~clm.core.course.Course` after sections and notebook numbers are
  assigned. It maps a *topic id* to the renamed, same-variant relative href
  for a given ``(language, kind, format)`` output artifact. Resolution is
  filesystem-free: both the referring and target artifacts live under the
  same ``slides/<format>/<kind>/`` root, so only the section-directory names
  and renamed filenames are needed.
* :func:`rewrite_cross_references` — the mechanical worker-side string
  rewrite. It needs no knowledge of other notebooks' output names; the
  resolved href map is computed at payload-construction time and carried
  across the worker boundary.

Identifier scheme (v1, locked): the path-derived **topic id** — the same
identifier ``<topic>id</topic>`` references in a spec, produced by
:func:`clm.infrastructure.utils.path_utils.simplify_ordered_name`. An
optional ``/notebook-stem`` disambiguator selects one deck inside a
multi-notebook directory topic; ``#anchor`` sub-section targets are out of
scope for v1 and are stripped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import quote

from clm.core.utils.text_utils import sanitize_file_name
from clm.infrastructure.utils.path_utils import (
    ext_for,
    simplify_ordered_name,
)

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.course_files.notebook_file import NotebookFile

logger = logging.getLogger(__name__)

# The cross-reference URI scheme (locked Decision 1, option A). A reference
# is authored as a normal Markdown link whose href uses this scheme, e.g.
# ``[Functions workshop](clm:functions_workshop)``. Only the href is
# rewritten at build time; the link text is left untouched.
SCHEME = "clm:"

# Matches the href of a Markdown link whose target uses the ``clm:`` scheme.
# Captures the link text and the reference (everything after ``clm:`` up to
# the closing paren). Deliberately conservative: it only fires inside the
# ``[text](...)`` link slot, so ordinary links and image links are left
# alone (an image link never uses the ``clm:`` scheme).
_CROSS_REF_RE = re.compile(r"\[(?P<text>[^\]]*)\]\(\s*clm:\s*(?P<ref>[^)\s]+)\s*\)")


@dataclass(frozen=True)
class ResolvedReference:
    """A cross-reference resolved to a concrete, per-artifact link target.

    ``href`` is the relative path (POSIX style) from the *referring* output
    file to the *target* output artifact of the same
    ``(language, kind, format)``. ``ambiguous`` is True when the reference
    pointed at a directory topic containing several slide notebooks and no
    ``/notebook-stem`` disambiguator was given — resolution then falls back
    to a deterministic target and the build emits a warning.
    """

    reference: str
    href: str
    ambiguous: bool = False


def extract_cross_references(text: str) -> list[str]:
    """Return every ``clm:`` reference found in *text*, in document order.

    Returns the raw reference strings (the part after ``clm:``) exactly as
    authored, including any ``/notebook-stem`` disambiguator or ``#anchor``
    — splitting those is the resolver's job. Duplicates are preserved so
    callers can report every occurrence.

    Args:
        text: Markdown (or percent-format slide) source.

    Returns:
        List of reference strings, e.g. ``["functions_workshop", "intro"]``.
    """
    return [m.group("ref") for m in _CROSS_REF_RE.finditer(text)]


def has_cross_references(text: str) -> bool:
    """Return True if *text* contains at least one ``clm:`` reference."""
    return _CROSS_REF_RE.search(text) is not None


def split_reference(reference: str) -> tuple[str, str | None]:
    """Split a raw reference into ``(topic_id, notebook_stem)``.

    ``#anchor`` sub-section targets are out of scope for v1 and are
    stripped here (the anchor part is discarded). A ``/notebook-stem``
    disambiguator, when present, selects one deck inside a multi-notebook
    directory topic.

    Examples::

        "intro"               -> ("intro", None)
        "intro/slides_basics" -> ("intro", "slides_basics")
        "intro#heading"       -> ("intro", None)
        "intro/deck#heading"  -> ("intro", "deck")
    """
    # Drop a v1-out-of-scope anchor first.
    ref = reference.split("#", 1)[0]
    if "/" in ref:
        topic_id, stem = ref.split("/", 1)
        return topic_id, (stem or None)
    return ref, None


@dataclass
class _NotebookEntry:
    """One resolvable target notebook, indexed by topic id."""

    notebook: NotebookFile
    # ``simplify_ordered_name`` of the slide-file stem, used as the
    # optional ``/notebook-stem`` disambiguator for multi-notebook topics.
    stem_id: str


def rewrite_cross_references(text: str, hrefs: dict[str, str]) -> str:
    """Rewrite every ``[text](clm:<ref>)`` link in *text* using *hrefs*.

    *hrefs* maps a raw reference string (exactly as authored, the part after
    ``clm:``) to its resolved relative href for the current output artifact.

    * A reference present in *hrefs* with a non-empty href becomes a normal
      Markdown link ``[text](<href>)``.
    * A reference mapped to the empty string is *dropped*: the link text is
      kept but the link itself is removed (rendered as plain text). This is
      the ``code`` output rule (locked Decision 4) and the warn-and-drop
      missing-target policy.
    * A reference absent from *hrefs* is left verbatim (deferred formats such
      as ``jupyterlite`` populate no href and rely on this; see Decision 4).

    The rewrite is purely mechanical and needs no knowledge of other
    notebooks — the href map is computed at payload-construction time.
    """
    if not hrefs:
        return text

    def _replace(match: re.Match[str]) -> str:
        ref = match.group("ref")
        link_text = match.group("text")
        if ref not in hrefs:
            return match.group(0)
        href = hrefs[ref]
        if not href:
            # Drop the link, keep the text.
            return link_text
        return f"[{link_text}]({href})"

    return _CROSS_REF_RE.sub(_replace, text)


class CrossReferenceResolver:
    """Resolve a ``clm:`` reference to a per-artifact relative href.

    Built once per :class:`~clm.core.course.Course` after sections and
    notebook numbers are assigned, so it knows every output notebook's
    renamed filename. Resolution is filesystem-free and per
    ``(language, kind, format)``.
    """

    def __init__(self, course: Course) -> None:
        from clm.core.course_files.notebook_file import NotebookFile

        self._course = course
        # topic id -> list of target notebooks (one per slide file).
        self._index: dict[str, list[_NotebookEntry]] = {}
        for section in course.sections:
            for topic in section.topics:
                for file in topic.files:
                    if not isinstance(file, NotebookFile):
                        continue
                    stem_id = simplify_ordered_name(file.path.stem) or file.path.stem
                    self._index.setdefault(topic.id, []).append(
                        _NotebookEntry(notebook=file, stem_id=stem_id)
                    )

    @property
    def topic_ids(self) -> set[str]:
        """Topic ids that have at least one slide notebook in the course."""
        return set(self._index)

    def _select(
        self, topic_id: str, notebook_stem: str | None
    ) -> tuple[NotebookFile | None, bool, int]:
        """Pick the target notebook for *topic_id*.

        Returns ``(notebook, ambiguous, candidate_count)``. ``notebook`` is
        None when the topic id is unknown. ``ambiguous`` is True when the
        topic has several notebooks and no disambiguator selected one (we
        then fall back to the deterministic first entry).
        """
        entries = self._index.get(topic_id)
        if not entries:
            return None, False, 0

        if notebook_stem is not None:
            for entry in entries:
                if entry.stem_id == notebook_stem or entry.notebook.path.stem == notebook_stem:
                    return entry.notebook, False, len(entries)
            # Disambiguator given but no match — treat as unresolved.
            return None, False, len(entries)

        if len(entries) == 1:
            return entries[0].notebook, False, 1

        # Multiple notebooks, no disambiguator: deterministic fallback to
        # the lowest section slot then path name.
        ordered = sorted(
            entries,
            key=lambda e: (e.notebook.number_in_section, e.notebook.path.name),
        )
        return ordered[0].notebook, True, len(entries)

    def resolve(
        self,
        reference: str,
        *,
        from_notebook: NotebookFile,
        language: str,
        kind: str,
        format: str,
    ) -> ResolvedReference | None:
        """Resolve *reference* for one output artifact, or return ``None``.

        Returns ``None`` when the target topic is not part of the (possibly
        section-filtered) course or when the reference carried a
        disambiguator that matched no notebook — the caller then applies the
        missing-target policy (locked Decision 4).

        The relative href is computed from the section-directory names and
        renamed filenames alone: the referring and target artifacts always
        share the same ``slides/<format>/<kind>/`` parent for a given
        ``(language, kind, format)``, so no filesystem access is needed.
        """
        topic_id, notebook_stem = split_reference(reference)
        target, ambiguous, _count = self._select(topic_id, notebook_stem)
        if target is None:
            return None

        ext = ext_for(format, target.prog_lang)
        target_section_dir = sanitize_file_name(target.section.name[language])
        target_file_name = target.file_name(language, ext)
        from_section_dir = sanitize_file_name(from_notebook.section.name[language])

        # Both files live under the same ``slides/<format>/<kind>/`` root, so
        # the relative path is purely a function of the two section dirs.
        from_path = PurePosixPath(from_section_dir)
        target_path = PurePosixPath(target_section_dir) / target_file_name
        href = _encode_href(_relative_posix(target_path, from_path))
        return ResolvedReference(reference=reference, href=href, ambiguous=ambiguous)

    def build_href_map(
        self,
        references: list[str],
        *,
        from_notebook: NotebookFile,
        language: str,
        kind: str,
        format: str,
        fail_on_missing: bool,
    ) -> tuple[dict[str, str], list[_XRefIssue]]:
        """Resolve every reference for one artifact into a href map.

        Returns ``(hrefs, issues)``. ``hrefs`` is consumed by
        :func:`rewrite_cross_references`:

        * present reference -> resolved relative href (normal link).
        * present reference -> ``""`` when the target is missing and
          ``fail_on_missing`` is False (warn-and-drop) **or** when the
          output ``format`` is ``code`` (links are always dropped there).
        * a missing reference under ``fail_on_missing`` is **omitted** from
          the map (left verbatim) and reported as an error issue, so the
          build can fail without rewriting the link to something misleading.

        ``jupyterlite`` is deferred: references are omitted (left verbatim)
        and a single info issue is recorded.
        """
        hrefs: dict[str, str] = {}
        issues: list[_XRefIssue] = []

        drop_all = format == "code"
        defer_all = format == "jupyterlite"

        if defer_all and references:
            issues.append(
                _XRefIssue(
                    severity="info",
                    reference=references[0],
                    message=(
                        "jupyterlite cross-reference targets are deferred until "
                        "the JupyterLite site builder ships; link text is left "
                        "verbatim."
                    ),
                )
            )
            return hrefs, issues

        for reference in references:
            resolved = self.resolve(
                reference,
                from_notebook=from_notebook,
                language=language,
                kind=kind,
                format=format,
            )
            if resolved is None:
                if fail_on_missing:
                    issues.append(
                        _XRefIssue(
                            severity="error",
                            reference=reference,
                            message=(
                                f"Cross-reference target '{reference}' is not "
                                f"included in the course (language={language}, "
                                f"kind={kind}, format={format})."
                            ),
                        )
                    )
                    # Leave verbatim so the failing build does not silently
                    # rewrite the link.
                    continue
                issues.append(
                    _XRefIssue(
                        severity="warning",
                        reference=reference,
                        message=(
                            f"Cross-reference target '{reference}' is not "
                            f"included in this build; dropping the link."
                        ),
                    )
                )
                hrefs[reference] = ""
                continue

            if resolved.ambiguous:
                issues.append(
                    _XRefIssue(
                        severity="warning",
                        reference=reference,
                        message=(
                            f"Cross-reference '{reference}' points at a topic "
                            f"with multiple slide notebooks; resolved to "
                            f"'{resolved.href}'. Add a '/notebook-stem' "
                            f"disambiguator to be explicit."
                        ),
                    )
                )

            hrefs[reference] = "" if drop_all else resolved.href

        return hrefs, issues


@dataclass
class _XRefIssue:
    """A cross-reference issue raised while building a href map."""

    severity: str  # "error", "warning", "info"
    reference: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CrossReferenceFinding:
    """A build-time cross-reference validation finding (Issue #17).

    Variant-agnostic: it reports whether a referenced topic is *included*
    in the (possibly section-filtered) course and whether it is ambiguous,
    independent of language / kind / format. ``severity`` for a missing
    target depends on the ``fail_on_missing`` policy chosen by the caller.
    """

    severity: str  # "error", "warning"
    type: str  # "cross_reference_target_missing" | "cross_reference_ambiguous"
    reference: str
    message: str
    source_file: str


def validate_cross_references(
    course: Course, *, fail_on_missing: bool
) -> list[CrossReferenceFinding]:
    """Scan every notebook in *course* for ``clm:`` references and validate them.

    A reference whose topic id is not part of the (section-filtered) course is
    reported as ``cross_reference_target_missing`` — an error when
    *fail_on_missing* is True, else a warning (the link will be dropped at
    rewrite time). A reference that points at a directory topic with several
    slide notebooks and carries no ``/notebook-stem`` disambiguator is reported
    as a ``cross_reference_ambiguous`` warning.

    Because the resolver is built from ``course.sections`` (already filtered by
    any active ``--section`` selection), a reference to a real topic that is
    excluded only by section filtering is correctly reported here.
    """
    from clm.core.course_files.notebook_file import NotebookFile

    resolver = course.cross_reference_resolver
    findings: list[CrossReferenceFinding] = []
    seen: set[tuple[str, str]] = set()

    for section in course.sections:
        for topic in section.topics:
            for file in topic.files:
                if not isinstance(file, NotebookFile):
                    continue
                try:
                    text = file.source_path.read_text(encoding="utf-8")
                except OSError as exc:  # pragma: no cover - defensive
                    logger.debug("Could not read %s for xref scan: %s", file.path, exc)
                    continue
                if not has_cross_references(text):
                    continue
                source = str(file.path)
                for reference in extract_cross_references(text):
                    topic_id, notebook_stem = split_reference(reference)
                    target, ambiguous, _count = resolver._select(topic_id, notebook_stem)
                    key = (source, reference)
                    if target is None:
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(
                            CrossReferenceFinding(
                                severity="error" if fail_on_missing else "warning",
                                type="cross_reference_target_missing",
                                reference=reference,
                                message=(
                                    f"Cross-reference target '{reference}' in "
                                    f"'{file.path.name}' is not included in the "
                                    f"course."
                                ),
                                source_file=source,
                            )
                        )
                    elif ambiguous:
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(
                            CrossReferenceFinding(
                                severity="warning",
                                type="cross_reference_ambiguous",
                                reference=reference,
                                message=(
                                    f"Cross-reference '{reference}' in "
                                    f"'{file.path.name}' points at a topic with "
                                    f"multiple slide notebooks; resolved "
                                    f"deterministically to the first. Add a "
                                    f"'/notebook-stem' disambiguator to be explicit."
                                ),
                                source_file=source,
                            )
                        )

    return findings


def _relative_posix(target: PurePosixPath, start: PurePosixPath) -> str:
    """Return the POSIX relative path from directory *start* to file *target*.

    Both are expressed relative to a common ``slides/<format>/<kind>/`` root.
    *start* is a directory (the referring file's section dir); *target* is a
    file path (section dir + filename).
    """
    start_parts = list(start.parts)
    target_parts = list(target.parts)

    # Find common prefix length.
    common = 0
    for a, b in zip(start_parts, target_parts, strict=False):
        if a != b:
            break
        common += 1

    up = [".."] * (len(start_parts) - common)
    down = target_parts[common:]
    rel_parts = up + down
    if not rel_parts:
        return "."
    return "/".join(rel_parts)


def _encode_href(path: str) -> str:
    """Percent-encode a relative href so it is a usable Markdown link target.

    CLM output filenames follow ``"{number_in_section:02} {title}{ext}"`` and
    therefore almost always contain spaces (and may contain other characters
    such as parentheses). A bare space is not valid inside a CommonMark inline
    link destination, so renderers — nbconvert's ``HTMLExporter``,
    JupyterLab/VS Code — leave ``[text](02 Foo.html)`` as *literal text*
    rather than emitting a working ``<a>`` anchor. Percent-encoding the path
    while preserving the ``/`` separators (e.g. ``../Workshops/03%20Foo.html``)
    is treated as a link by every renderer and resolves back to the real file
    in the browser (issue #17).
    """
    return quote(path, safe="/")
