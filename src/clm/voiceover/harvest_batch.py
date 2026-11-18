"""Spec-driven harvest addressing: decks and their recordings from a course spec (#1104).

The recordings workflow writes every finished video to a fixed layout, computed
from the spec exactly as the recording dashboard computes it::

    <root>/final/<output-dir-name[lang]>/<section name[lang]>/<deck file name>[ (part N)].<ext>

(``(Teil N)`` for German recordings). This module turns a spec into the list of
recorded decks — each deck's source file (the recorded-language half) and its
ordered video parts — so that agents never build deck paths or video globs by
hand. A glob such as ``topic_*_strings`` once matched ``topic_180_c_strings``
before ``topic_190_strings`` and a report was computed against the wrong deck;
here ``--topic`` is an exact topic id, resolved by the build's own resolver.

Decisions (issue #1104, owner, 2026-11-18): D1 the layout above is the only
video source (not the per-machine recording state file, not tree globs); D3
exact topic ids, sections by id or 1-based position; D4 parts ordered by part
number, a gap is a note (``invalidate`` never renumbers), an unnumbered file
next to numbered parts is an error for that deck, a deck with no video is
``no_recording``; D7 ``unharvested`` reads the sync ledger's
``harvest:<fingerprint>`` provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.section import Section

#: Video extensions the recordings workflow produces or accepts as finals.
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"})


class SpecAddressError(Exception):
    """The spec, the recordings root or the requested selection is unusable."""


@dataclass
class RecordedDeck:
    """One deck of the spec and the recordings found for it in the layout."""

    section_index: int
    """1-based position of the deck's section among the course's sections."""
    section_id: str | None
    section_name: str
    topic_id: str
    deck: Path
    """The deck file in the recorded language (the half harvest takes)."""
    deck_name: str
    """The recording name: ``<NN> <title>`` as the dashboard shows it."""
    video_dir: Path
    videos: list[Path] = field(default_factory=list)
    parts: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def has_recording(self) -> bool:
        return bool(self.videos)

    def to_json(self, base: Path | None = None) -> dict:
        return {
            "section": self.section_name,
            "section_index": self.section_index,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "deck": _display(self.deck, base),
            "deck_name": self.deck_name,
            "videos": [str(v) for v in self.videos],
            "parts": self.parts,
            "notes": self.notes,
            "error": self.error,
        }


def _display(path: Path, base: Path | None) -> str:
    if base is not None:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(path)


def recordings_final_dir(root: Path) -> Path:
    """``<root>/final`` — the directory the workflow writes finished videos to."""
    from clm.recordings.workflow.directories import final_dir

    return final_dir(root)


def find_parts(
    video_dir: Path, deck_name: str
) -> tuple[list[Path], list[int], list[str], str | None]:
    """The final videos of *deck_name* in *video_dir*: ``(videos, parts, notes, error)``.

    Parts are ordered by part number (D4). Part 0 is the unnumbered single
    recording. A gap in the numbering is a note; an unnumbered file next to
    numbered parts, or two files for one part (different extensions), is an
    error — the batch cannot know which one the trainer meant.
    """
    from clm.core.utils.text_utils import sanitize_file_name
    from clm.recordings.workflow.naming import parse_part

    if not video_dir.is_dir():
        return [], [], [], None
    wanted = sanitize_file_name(deck_name)
    by_part: dict[int, list[Path]] = {}
    for child in video_dir.iterdir():
        if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        base, part = parse_part(child.stem)
        if base != wanted:
            continue
        by_part.setdefault(part, []).append(child)
    if not by_part:
        return [], [], [], None

    duplicates = {p: sorted(paths) for p, paths in by_part.items() if len(paths) > 1}
    if duplicates:
        listing = "; ".join(
            f"part {p}: " + ", ".join(x.name for x in paths)
            for p, paths in sorted(duplicates.items())
        )
        return [], [], [], f"more than one video file for the same part ({listing})"
    if 0 in by_part and len(by_part) > 1:
        names = ", ".join(sorted(paths[0].name for paths in by_part.values()))
        return (
            [],
            [],
            [],
            f"an unnumbered video next to numbered parts ({names}); keep one form",
        )
    parts = sorted(by_part)
    videos = [by_part[p][0] for p in parts]
    notes: list[str] = []
    if parts != [0]:
        missing = sorted(set(range(1, parts[-1] + 1)) - set(parts))
        if missing:
            notes.append(
                "parts " + ", ".join(str(m) for m in missing) + " are missing "
                "(gaps are expected after `clm recordings invalidate`)"
            )
    return videos, parts, notes, None


def _select_sections(sections: list[Section], section: str | None) -> list[tuple[int, Section]]:
    indexed = list(enumerate(sections, start=1))
    if section is None:
        return indexed
    by_id = [(i, s) for i, s in indexed if s.id is not None and s.id == section]
    if by_id:
        return by_id
    if section.isdigit():
        n = int(section)
        if 1 <= n <= len(sections):
            return [indexed[n - 1]]
        raise SpecAddressError(f"section position {n} is out of range (1..{len(sections)})")
    ids = ", ".join(s.id for s in sections if s.id) or "none"
    raise SpecAddressError(
        f"no section with id {section!r} (ids in this spec: {ids}); "
        "a section without an id is addressed by its 1-based position"
    )


def resolve_recorded_decks(
    spec_file: Path,
    lang: str,
    recordings_root: Path | None,
    *,
    topic: str | None = None,
    section: str | None = None,
    deck: str | None = None,
) -> list[RecordedDeck]:
    """Every deck the spec builds in *lang*, with its recordings from the layout.

    *topic* is an exact topic id; *section* a section id or 1-based position;
    *deck* narrows a topic holding several decks (a deck file stem such as
    ``slides_intro``, with or without the language tag, or the recording name).
    With *recordings_root* ``None`` no videos are looked up (the deck list
    only). Raises :class:`SpecAddressError` for an unusable spec or selection.
    """
    from clm.core.course import Course
    from clm.core.course_paths import resolve_course_paths
    from clm.core.course_spec import CourseSpec, CourseSpecError

    try:
        spec = CourseSpec.from_file(spec_file)
    except (CourseSpecError, OSError, ValueError) as exc:
        raise SpecAddressError(f"cannot read spec {spec_file}: {exc}") from None
    course_root, _ = resolve_course_paths(spec_file)
    course = Course.from_spec(spec, course_root, output_root=course_root)

    if topic is not None and not any(t.id == topic for s in course.sections for t in s.topics):
        raise SpecAddressError(
            f"topic {topic!r} is not in {spec_file.name} (or its section is disabled); "
            "--topic takes an exact topic id"
        )

    from clm.core.utils.text_utils import sanitize_file_name

    video_root = (
        recordings_final_dir(recordings_root) / sanitize_file_name(spec.output_dir_name[lang])
        if recordings_root is not None
        else None
    )

    decks: list[RecordedDeck] = []
    for index, sec in _select_sections(list(course.sections), section):
        section_name = sec.name[lang]
        video_dir = (
            video_root / sanitize_file_name(section_name) if video_root is not None else Path()
        )
        for nb in sec.notebooks:
            nb_lang = getattr(nb, "output_language_filter", None)
            if nb_lang in ("de", "en") and nb_lang != lang:
                continue
            if topic is not None and nb.topic.id != topic:
                continue
            deck_name = nb.file_name(lang, "")
            if deck is not None and not _deck_matches(nb.path, deck_name, deck):
                continue
            videos, parts, notes, error = (
                find_parts(video_dir, deck_name) if video_root is not None else ([], [], [], None)
            )
            decks.append(
                RecordedDeck(
                    section_index=index,
                    section_id=sec.id,
                    section_name=section_name,
                    topic_id=nb.topic.id,
                    deck=nb.path,
                    deck_name=deck_name,
                    video_dir=video_dir,
                    videos=videos,
                    parts=parts,
                    notes=notes,
                    error=error,
                )
            )
    return decks


def _deck_matches(path: Path, deck_name: str, wanted: str) -> bool:
    from clm.core.slide_text.pairing import split_lang_tag

    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    candidates = {path.name, stem, deck_name}
    tag = split_lang_tag(path)
    if tag is not None and stem.endswith(f".{tag}"):
        candidates.add(stem[: -len(tag) - 1])
    return wanted in candidates


def resolve_one_deck(
    spec_file: Path,
    lang: str,
    *,
    topic: str | None,
    deck: str | None = None,
    recordings_root: Path | None = None,
) -> RecordedDeck:
    """The single deck ``--spec --topic [--deck]`` addresses (D2).

    Ambiguity is refused: a topic with several decks needs ``--deck``. With no
    *recordings_root* the returned deck carries no videos (accept/verify need
    none).
    """
    if topic is None:
        raise SpecAddressError("--spec needs --topic to address one deck")
    found = resolve_recorded_decks(spec_file, lang, recordings_root, topic=topic, deck=deck)
    if not found:
        where = f"topic {topic!r}" + (f" with --deck {deck!r}" if deck else "")
        raise SpecAddressError(f"{where} has no deck in {lang} in {spec_file.name}")
    if len(found) > 1:
        names = ", ".join(d.deck.name for d in found)
        raise SpecAddressError(
            f"topic {topic!r} holds {len(found)} decks ({names}); say which with --deck STEM"
        )
    return found[0]


def deck_already_harvested(deck: Path, fingerprint: str) -> bool:
    """Whether the deck's sync ledger records ``harvest:<fingerprint>`` (D7).

    A member keeps that provenance until a later sync write replaces it, so a
    ``False`` can also mean "harvested, then re-banked by sync" — the batch then
    harvests again, which errs on the safe side.
    """
    from clm.core.slide_text.pairing import derive_split_pair
    from clm.slides import doc_ledger

    pair = derive_split_pair(deck)
    de_path = pair[0] if pair is not None else deck
    ledger_path = doc_ledger.ledger_path_for(de_path)
    if not ledger_path.is_file():
        return False
    try:
        ledger = doc_ledger.load(ledger_path)
    except Exception:
        return False
    entry = ledger.decks.get(doc_ledger.deck_key_for(de_path))
    if entry is None:
        return False
    wanted = f"harvest:{fingerprint}"
    return any(member.provenance == wanted for member in entry.members.values())


def unclaimed_videos(
    spec_file: Path, lang: str, recordings_root: Path, decks: list[RecordedDeck]
) -> list[Path]:
    """Final videos in the course's recordings tree that no deck of *decks* claims.

    Usually a deck renamed or retitled after its recording (the recording name
    is ``<NN> <title>``), or a deck removed from the spec. Only meaningful for an
    unfiltered resolution.
    """
    from clm.core.course_spec import CourseSpec
    from clm.core.utils.text_utils import sanitize_file_name

    spec = CourseSpec.from_file(spec_file)
    video_root = recordings_final_dir(recordings_root) / sanitize_file_name(
        spec.output_dir_name[lang]
    )
    if not video_root.is_dir():
        return []
    claimed = {v.resolve() for d in decks for v in d.videos}
    erroneous_dirs = {(d.video_dir.resolve(), d.deck_name) for d in decks if d.error}
    from clm.recordings.workflow.naming import parse_part

    found: list[Path] = []
    for path in sorted(video_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if path.resolve() in claimed:
            continue
        base, _ = parse_part(path.stem)
        if any(
            path.parent.resolve() == d and base == sanitize_file_name(name)
            for d, name in erroneous_dirs
        ):
            continue  # already reported as that deck's error
        found.append(path)
    return found
