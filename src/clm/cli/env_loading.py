"""Shared ``.env`` discovery + loading for CLI commands.

Several commands need the project's ``.env`` (API keys, model overrides) on the
process environment before they run. A typical course checkout keeps keys in a
``.env`` at the project root and **does not export them** — the notebooks read
them via ``load_dotenv()``. A CLI command that only inspects ``os.environ``
therefore silently behaves as if no key were configured (Issue: ``clm slides
sync`` deferring every add because ``OPENROUTER_API_KEY`` lived only in ``.env``).

``find_env_file`` walks up from a starting directory; ``load_env_files`` loads
the first ``.env`` found for each starting directory (de-duplicated) without
overriding values already present in the environment. ``clm build`` and
``clm slides sync`` share this so the discovery rule stays in one place.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["find_env_file", "load_env_files"]


def find_env_file(start_dir: Path) -> Path | None:
    """Walk up from ``start_dir`` looking for a ``.env`` file.

    Returns the path to the first ``.env`` found, or ``None``. The spec/deck
    file is often in a subdirectory (e.g. ``slides/.../topic_.../``) while
    ``.env`` sits at the project root, so we ascend until the filesystem root.
    """
    search_dir = start_dir
    while True:
        candidate = search_dir / ".env"
        if candidate.is_file():
            return candidate
        parent = search_dir.parent
        if parent == search_dir:
            break
        search_dir = parent
    return None


def load_env_files(*start_dirs: Path, override: bool = False) -> list[Path]:
    """Discover and load a ``.env`` for each starting directory.

    For every directory in ``start_dirs`` the first ``.env`` found by
    :func:`find_env_file` (ascending) is loaded into ``os.environ``. Duplicate
    files (the common case where both deck halves live in the same directory)
    are loaded once. ``override`` is passed through to ``python-dotenv``; the
    default ``False`` means an already-exported value wins over the file. Returns
    the list of ``.env`` files actually loaded, in discovery order.
    """
    from dotenv import load_dotenv

    loaded: list[Path] = []
    seen: set[Path] = set()
    for start in start_dirs:
        env_file = find_env_file(start.resolve())
        if env_file is None or env_file in seen:
            continue
        seen.add(env_file)
        if load_dotenv(env_file, override=override):
            loaded.append(env_file)
            logger.info("Loaded environment from %s", env_file)
    return loaded
