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

_PART_RE = re.compile(r"^(.*?) \(part (\d+)\)$")


def recording_relative_dir(course_slug: str, section_name: str) -> PurePosixPath:
    """Build the relative directory for a recording: ``<course>/<section>/``.

    Both components are sanitized for filesystem safety.
    """
    return PurePosixPath(sanitize_file_name(course_slug)) / sanitize_file_name(section_name)


def _part_suffix(part: int) -> str:
    """Return ``" (part N)"`` when *part* > 0, else ``""``."""
    return f" (part {part})" if part > 0 else ""


def raw_filename(
    deck_name: str,
    ext: str = ".mp4",
    raw_suffix: str = DEFAULT_RAW_SUFFIX,
    *,
    part: int = 0,
) -> str:
    """Build a raw recording filename.

    >>> raw_filename("03 Intro")
    '03 Intro--RAW.mp4'
    >>> raw_filename("03 Intro", part=2)
    '03 Intro (part 2)--RAW.mp4'
    """
    return f"{sanitize_file_name(deck_name)}{_part_suffix(part)}{raw_suffix}{ext}"


def final_filename(deck_name: str, ext: str = ".mp4", *, part: int = 0) -> str:
    """Build the final output filename.

    >>> final_filename("03 Intro")
    '03 Intro.mp4'
    >>> final_filename("03 Intro", part=1)
    '03 Intro (part 1).mp4'
    """
    return f"{sanitize_file_name(deck_name)}{_part_suffix(part)}{ext}"


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
