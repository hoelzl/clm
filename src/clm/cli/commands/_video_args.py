"""Shared expansion of positional VIDEO arguments for the harvest commands."""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path

import click

_GLOB_CHARS = re.compile(r"[*?\[]")


def _natural_sort_key(path: Path) -> list:
    parts = re.split(r"(\d+)", path.name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def expand_video_args(videos: tuple[str, ...]) -> list[Path]:
    """Expand glob patterns in positional video arguments.

    Arguments containing ``*``, ``?``, or ``[`` are expanded relative to
    the current working directory and sorted with a natural-numeric
    comparator so ``Teil 2.mp4`` precedes ``Teil 10.mp4``. Literal
    arguments are returned as-is after an existence check. Ordering
    between arguments is preserved; only matches within a single glob
    argument are reordered.
    """
    expanded: list[Path] = []
    for raw in videos:
        if _GLOB_CHARS.search(raw):
            matches = sorted(
                (Path(m) for m in _glob.glob(raw, recursive=False)),
                key=_natural_sort_key,
            )
            if not matches:
                raise click.BadParameter(
                    f"no files match glob pattern: {raw}",
                    param_hint="VIDEOS",
                )
            expanded.extend(matches)
        else:
            p = Path(raw)
            if not p.exists():
                raise click.BadParameter(
                    f"path does not exist: {raw}",
                    param_hint="VIDEOS",
                )
            expanded.append(p)
    return expanded
