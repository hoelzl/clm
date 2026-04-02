"""Filename convention helpers for the recording workflow.

Naming scheme:
    <course-slug>/<section-name>/<topic-name>--RAW.mp4   (raw recording)
    <course-slug>/<section-name>/<topic-name>--RAW.wav   (processed audio)
    <course-slug>/<section-name>/<topic-name>.mp4         (final output)

All name components are sanitized via ``sanitize_file_name`` from
``clm.core.utils.text_utils`` to ensure filesystem-safe paths.

This module is pure functions with no I/O.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from clm.core.utils.text_utils import sanitize_file_name

DEFAULT_RAW_SUFFIX = "--RAW"


def recording_relative_dir(course_slug: str, section_name: str) -> PurePosixPath:
    """Build the relative directory for a recording: ``<course>/<section>/``.

    Both components are sanitized for filesystem safety.
    """
    return PurePosixPath(sanitize_file_name(course_slug)) / sanitize_file_name(section_name)


def raw_filename(topic_name: str, ext: str = ".mp4", raw_suffix: str = DEFAULT_RAW_SUFFIX) -> str:
    """Build a raw recording filename: ``<topic>--RAW.mp4``."""
    return f"{sanitize_file_name(topic_name)}{raw_suffix}{ext}"


def final_filename(topic_name: str, ext: str = ".mp4") -> str:
    """Build the final output filename: ``<topic>.mp4``."""
    return f"{sanitize_file_name(topic_name)}{ext}"


def parse_raw_stem(stem: str, raw_suffix: str = DEFAULT_RAW_SUFFIX) -> tuple[str, bool]:
    """Parse a file stem into ``(base_name, is_raw)``.

    >>> parse_raw_stem("my_topic--RAW")
    ('my_topic', True)
    >>> parse_raw_stem("my_topic")
    ('my_topic', False)
    """
    if stem.endswith(raw_suffix):
        return stem[: -len(raw_suffix)], True
    return stem, False
