"""Content conservation: a pairing-independent check on one apply pass (#1130 step 2).

The differ decides which DE cell is which EN cell, and for un-id'd shared
cells that decision is often a positional guess. A wrong guess used to turn
into a silent loss: an edit overwritten or a cell duplicated, with the next
report clean. This check looks only at the cell *contents* before and after
the pass, never at the pairing, so a mis-pairing cannot also fool the check
that guards against it. (It does not see cell *order*: a mis-paired move is
the job of the pairing itself, #1130 step 1.)

**Content conservation.** Over the shared, inline cells of each half,
counted by content (header without the id attribute, body, trailing blank
lines dropped):

* *agreed content survives*: content present on both halves before the pass
  keeps at least its smaller count on each half — neither half changed it, so
  nothing may remove it;
* *edits survive*: content on a half that the ledger never recorded (an
  author's edit or insertion) keeps its count on that half;
* *no duplication*: no content exceeds its larger count across the halves
  (content neither half had before is a ``body`` answer's own text).

An explicit answer that discards content (a side choice on a conflict, a
``remove``, a ``body``) exempts exactly the cells it names.

A failed check is a violation the pre-write verify withholds like any
introduced structural violation: the pass is recomputed without the slide
groups it names, and nothing it would have lost is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from attrs import frozen

from clm.slides.bilingual_doc import Lang, Member, SideCell
from clm.slides.doc_identity import content_fingerprint
from clm.slides.doc_lenses import lines_sans_id
from clm.slides.slug import strip_preserve_marker

_SIDES: tuple[Lang, Lang] = ("de", "en")

#: Answered actions that only move cells: their answer discards no content.
_PLACEMENT_ACTIONS = frozenset({"pool_placement_divergence", "order_decision"})


@frozen
class ConservationViolation:
    """A pass that would lose, duplicate or misplace content.

    Shaped like :class:`clm.slides.sync_verify.VerifyViolation` as far as the
    pre-write withhold reads it: ``slide_id`` is ``None`` and
    ``slide_groups`` names the groups to withhold.
    """

    kind: str
    message: str
    slide_groups: tuple[str, ...]
    locus: str
    slide_id: str | None = None


def content_key(cell: SideCell) -> str:
    """A cell's content for conservation, trailing blank lines dropped.

    An id'd cell's key leads with its bare id: an id says "a distinct cell",
    so an id'd cell and an un-id'd one with the same bytes are not copies of
    each other.
    """
    lines = list(lines_sans_id(cell))
    while len(lines) > 1 and lines[-1].strip() == "":
        lines.pop()
    bare = strip_preserve_marker(cell.slide_id) if cell.slide_id else ""
    prefix = f"id={bare}\n" if bare else ""
    return prefix + "\n".join(lines)


def _excerpt(key: str) -> str:
    lines = key.splitlines()
    if lines and lines[0].startswith("id="):
        lines = lines[1:]
    body = [line for line in lines[1:] if line.strip()]
    text = " / ".join(body) if body else (lines[0] if lines else "")
    return text if len(text) <= 60 else text[:57] + "..."


def _shared_cells(
    members: Iterable[tuple[str, Member]],
) -> Iterable[tuple[str, Lang, SideCell]]:
    for group, member in members:
        if member.langness != "shared" or member.layout != "inline":
            continue
        for lang in _SIDES:
            cell = member.side(lang)
            if cell is not None and cell.part == "deck":
                yield group, lang, cell


@frozen
class _Census:
    counts: dict[Lang, Counter[str]]
    groups: dict[str, set[str]]
    #: content key -> ledger-style fingerprints seen for it, per half
    fps: dict[tuple[Lang, str], set[str]]


def _census(members: Iterable[tuple[str, Member]]) -> _Census:
    counts: dict[Lang, Counter[str]] = {"de": Counter(), "en": Counter()}
    groups: dict[str, set[str]] = {}
    fps: dict[tuple[Lang, str], set[str]] = {}
    for group, lang, cell in _shared_cells(members):
        key = content_key(cell)
        counts[lang][key] += 1
        groups.setdefault(key, set()).add(group)
        fps.setdefault((lang, key), set()).add(content_fingerprint(cell))
    return _Census(counts, groups, fps)


def _item_cells(item: object) -> dict[Lang, SideCell | None]:
    """The DE and EN cells a diff row holds (the ``twin`` convention)."""
    member = getattr(item, "member", None)
    twin = getattr(item, "twin", None)
    de = member.side("de") if member is not None else None
    en_holder = twin if twin is not None else member
    en = en_holder.side("en") if en_holder is not None else None
    return {"de": de, "en": en}


def discarded_by_answers(
    landed: Sequence[tuple[object, str]], choices: Mapping[str, Sequence[str]]
) -> set[tuple[Lang, str]]:
    """``(half, content)`` pairs an explicit answer of a landed row discards.

    A side choice discards the other side's cell; any other content answer
    (``remove``, ``body``, …) may replace or delete both. Placement answers and
    ``confirm`` discard nothing.
    """
    out: set[tuple[Lang, str]] = set()
    for item, _provenance in landed:
        action = getattr(item, "action", "")
        if action in _PLACEMENT_ACTIONS:
            continue
        for choice in choices.get(getattr(item, "key", ""), ()):
            if choice == "confirm":
                continue
            cells = _item_cells(item)
            for lang in _SIDES:
                cell = cells[lang]
                if cell is None or choice == lang:
                    continue
                out.add((lang, content_key(cell)))
    return out


def check_conservation(
    before: Iterable[tuple[str, Member]],
    after: Iterable[tuple[str, Member]],
    baseline_fps: set[str] | None,
    exempt: set[tuple[Lang, str]],
) -> list[ConservationViolation]:
    """The content-conservation violations of one projected pass.

    ``baseline_fps`` holds every fingerprint the ledger recorded for the
    deck; ``None`` (no ledger yet) disables the *edits survive* rule, since
    nothing tells an edit from recorded content.
    """
    pre = _census(before)
    post = _census(after)
    out: list[ConservationViolation] = []
    keys = set(pre.counts["de"]) | set(pre.counts["en"]) | set(post.counts["de"])
    keys |= set(post.counts["en"])
    for key in sorted(keys):
        de, en = pre.counts["de"][key], pre.counts["en"][key]
        agreed, most = min(de, en), max(de, en)
        groups = tuple(sorted(pre.groups.get(key, set()) | post.groups.get(key, set())))
        for lang in _SIDES:
            if (lang, key) in exempt:
                continue
            was, now = pre.counts[lang][key], post.counts[lang][key]
            problem = None
            if now < agreed:
                problem = f"would remove content both halves agree on from {lang.upper()}"
            elif most and now > most:
                # (content neither half had is a ``body`` answer's own text)
                problem = (
                    f"would duplicate content on {lang.upper()} ({now} copies, at most {most})"
                )
            elif (
                baseline_fps is not None
                and now < was
                and not pre.fps.get((lang, key), set()) <= baseline_fps
            ):
                problem = f"would drop an unrecorded edit from {lang.upper()}"
            if problem is not None:
                out.append(
                    ConservationViolation(
                        kind="content-conservation",
                        message=f"this pass {problem}: {_excerpt(key)!r}",
                        slide_groups=groups,
                        locus=f"{lang}:{key}",
                    )
                )
    return out
