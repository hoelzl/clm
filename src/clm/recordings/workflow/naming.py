"""Filename convention helpers for the recording workflow.

Naming scheme::

    <course-slug>/<section-name>/<deck-name>--RAW.mp4           (raw recording)
    <course-slug>/<section-name>/<deck-name> (part N)--RAW.mp4  (multi-part raw)
    <course-slug>/<section-name>/<deck-name>.mp4                (final output)
    <course-slug>/<section-name>/<deck-name> (part N).mp4       (multi-part final)

All name components are sanitized via ``sanitize_file_name`` from
``clm.core.utils.text_utils`` to ensure filesystem-safe paths.

Most functions are pure with no I/O; :func:`find_existing_recordings`
is the exception — it scans a directory for matching files.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from clm.core.utils.text_utils import sanitize_file_name

DEFAULT_RAW_SUFFIX = "--RAW"

_PART_RE = re.compile(r"^(.*?) \((?:part|Teil) (\d+)\)$")
_PART_TAKE_RE = re.compile(r"^(.*?) \((?:part|Teil) (\d+), take (\d+)\)$")
_TAKE_ONLY_RE = re.compile(r"^(.*?) \(take (\d+)\)$")

_PART_LABELS: dict[str, str] = {"de": "Teil", "en": "part"}


def recording_relative_dir(course_slug: str, section_name: str) -> PurePosixPath:
    """Build the relative directory for a recording: ``<course>/<section>/``.

    Both components are sanitized for filesystem safety.
    """
    return PurePosixPath(sanitize_file_name(course_slug)) / sanitize_file_name(section_name)


def _part_suffix(part: int, lang: str = "en") -> str:
    """Return ``" (part N)"`` or ``" (Teil N)"`` when *part* > 0, else ``""``."""
    if part <= 0:
        return ""
    label = _PART_LABELS.get(lang, "part")
    return f" ({label} {part})"


def _part_take_suffix(part: int, take: int, lang: str = "en") -> str:
    """Return the combined ``(part N, take K)`` suffix for superseded takes.

    When ``part == 0``, the single-part form ``(take K)`` is used.  Always
    English-labelled ``take`` regardless of ``lang`` — users never see this
    suffix outside the ``takes/`` history shelf.
    """
    if part <= 0:
        return f" (take {take})"
    label = _PART_LABELS.get(lang, "part")
    return f" ({label} {part}, take {take})"


def raw_filename(
    deck_name: str,
    ext: str = ".mp4",
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    *,
    part: int = 0,
    lang: str = "en",
) -> str:
    """Build a raw recording filename.

    >>> raw_filename("03 Intro")
    '03 Intro--RAW.mp4'
    >>> raw_filename("03 Intro", part=2)
    '03 Intro (part 2)--RAW.mp4'
    >>> raw_filename("03 Intro", part=2, lang="de")
    '03 Intro (Teil 2)--RAW.mp4'
    """
    return f"{sanitize_file_name(deck_name)}{_part_suffix(part, lang)}{raw_suffix}{ext}"


def final_filename(deck_name: str, ext: str = ".mp4", *, part: int = 0, lang: str = "en") -> str:
    """Build the final output filename.

    >>> final_filename("03 Intro")
    '03 Intro.mp4'
    >>> final_filename("03 Intro", part=1)
    '03 Intro (part 1).mp4'
    >>> final_filename("03 Intro", part=1, lang="de")
    '03 Intro (Teil 1).mp4'
    """
    return f"{sanitize_file_name(deck_name)}{_part_suffix(part, lang)}{ext}"


def parse_raw_stem(stem: str, raw_suffix: str = DEFAULT_RAW_SUFFIX) -> tuple[str, bool]:
    """Parse a file stem into ``(base_name, is_raw)``.

    The base name includes the ``(part N)`` suffix when present so that
    callers can use :func:`parse_part` to extract it separately.

    >>> parse_raw_stem("my_deck--RAW")
    ('my_deck', True)
    >>> parse_raw_stem("my_deck (part 1)--RAW")
    ('my_deck (part 1)', True)
    >>> parse_raw_stem("my_deck")
    ('my_deck', False)
    """
    if stem.endswith(raw_suffix):
        return stem[: -len(raw_suffix)], True
    return stem, False


def parse_part(base_name: str) -> tuple[str, int]:
    """Split an optional ``(part N)`` suffix from *base_name*.

    >>> parse_part("03 Intro (part 2)")
    ('03 Intro', 2)
    >>> parse_part("03 Intro")
    ('03 Intro', 0)
    """
    m = _PART_RE.match(base_name)
    if m:
        return m.group(1), int(m.group(2))
    return base_name, 0


def parse_part_take(base_name: str) -> tuple[str, int, int]:
    """Split a ``(part N, take K)`` or ``(take K)`` suffix from *base_name*.

    Returns ``(deck_name, part_number, take_number)`` where a zero
    ``take_number`` means "no take suffix found". Part 0 with take>0 means
    a single-part recording's take, e.g. ``(take 2)`` without ``(part ...)``.

    >>> parse_part_take("03 Intro (part 2, take 3)")
    ('03 Intro', 2, 3)
    >>> parse_part_take("03 Intro (take 2)")
    ('03 Intro', 0, 2)
    >>> parse_part_take("03 Intro (part 2)")
    ('03 Intro', 2, 0)
    >>> parse_part_take("03 Intro")
    ('03 Intro', 0, 0)
    """
    m = _PART_TAKE_RE.match(base_name)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))
    m = _TAKE_ONLY_RE.match(base_name)
    if m:
        return m.group(1), 0, int(m.group(2))
    base, part = parse_part(base_name)
    return base, part, 0


def take_filename(
    deck_name: str,
    ext: str = ".mp4",
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    *,
    part: int = 0,
    take: int,
    is_raw: bool = False,
    lang: str = "en",
) -> str:
    """Build a filename for a superseded take under ``takes/``.

    >>> take_filename("03 Intro", part=2, take=1)
    '03 Intro (part 2, take 1).mp4'
    >>> take_filename("03 Intro", part=0, take=2)
    '03 Intro (take 2).mp4'
    >>> take_filename("03 Intro", part=2, take=1, is_raw=True)
    '03 Intro (part 2, take 1)--RAW.mp4'
    """
    suffix = _part_take_suffix(part, take, lang)
    raw = raw_suffix if is_raw else ""
    return f"{sanitize_file_name(deck_name)}{suffix}{raw}{ext}"


def find_existing_recordings(
    directory: Path,
    deck_name: str,
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
) -> dict[int, Path]:
    """Scan *directory* for raw recordings matching *deck_name*.

    Returns a dict mapping part numbers to file paths.  Part 0 means
    the unsuffixed file (``deck--RAW.ext``); part N>0 means a file
    with ``(part N)`` in the name.

    Only considers files whose stem ends with *raw_suffix*.
    """
    sanitized = sanitize_file_name(deck_name)
    result: dict[int, Path] = {}

    if not directory.is_dir():
        return result

    for child in directory.iterdir():
        if not child.is_file():
            continue
        base_with_part, is_raw = parse_raw_stem(child.stem, raw_suffix)
        if not is_raw:
            continue
        base, part = parse_part(base_with_part)
        if base == sanitized:
            result[part] = child

    return result
