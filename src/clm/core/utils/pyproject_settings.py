"""Read CLM settings from a ``pyproject.toml`` ``[tool.clm]`` table.

The single implementation behind every ``[tool.clm]`` consumer (A7, #802).
CLM keeps exactly two keys in this table — ``cache_dir`` (the shared
LLM/voiceover cache directory, resolved by
``clm.infrastructure.llm.cache.describe_cache_dir``) and ``sidecar-layout``
(the course-wide authoring-sidecar default, resolved by
``clm.core.sidecar_layout``). Both are *course-repo-scoped* settings: they
travel with the repository like any other ``[tool.X]`` table, which is why
they live in ``pyproject.toml`` rather than the operator-owned ``ClmConfig``
files (``clm.toml`` is discovered the same way but is CLM-specific; the
``[tool.clm]`` table predates it and is the documented home for these two).

This module only parses; each consumer keeps its own discovery/anchoring
semantics (nearest-ancestor walk for ``sidecar-layout``, project-root plus
git-worktree anchoring for ``cache_dir``). New ``[tool.clm]`` keys should be
weighed against a ``ClmConfig`` field first — the table is deliberately
small, and everything in it must also be surfaced by ``clm config show`` /
``locate``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def read_tool_clm(pyproject: Path) -> dict[str, Any]:
    """Return the parsed ``[tool.clm]`` table of ``pyproject``, or ``{}``.

    Unreadable or unparseable files yield ``{}`` — every consumer treats a
    broken ``pyproject.toml`` as "no setting", never as an error.
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python <3.11 not supported
        return {}
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = data.get("tool", {}).get("clm", {})
    return table if isinstance(table, dict) else {}


def read_tool_clm_key(pyproject: Path, key: str) -> str | None:
    """Return the non-empty string value of ``key`` in ``[tool.clm]``, else ``None``.

    Non-string and empty values read as unset, matching how both existing
    consumers treated them.
    """
    value = read_tool_clm(pyproject).get(key)
    if isinstance(value, str) and value:
        return value
    return None


def find_nearest_pyproject(start: Path) -> Path | None:
    """Return the nearest ancestor ``pyproject.toml`` of ``start``, or ``None``.

    Walks upward from ``start`` (or its parent, if ``start`` is a file) and
    stops at the **first** ``pyproject.toml`` found — a project root that does
    not set a key yields "unset" rather than leaking a value from an unrelated
    parent project.
    """
    base = start if start.is_dir() else start.parent
    for directory in (base, *base.parents):
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            return pyproject
    return None
