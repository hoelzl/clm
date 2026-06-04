"""Resolve the course-wide *default* for the authoring-sidecar layout.

This is a **write-time** convenience only: it chooses where a *newly created*
voiceover companion lands (``clm voiceover extract`` / ``sync``) when neither an
explicit ``--layout`` flag nor a per-topic ``voiceover/`` directory has already
decided. The build never consults this — it always reads both layouts via
``resolve_companion`` / ``NotebookFile`` cassette resolution — so a course can
flip its default freely without changing any build output.

The full precedence for a new companion (highest first) is:

1. an explicit ``--layout {subdir,sibling}`` flag,
2. a per-topic ``voiceover/`` directory that already exists,
3. the course default this module resolves,
4. the built-in fallback, ``sibling``.

This module owns only step 3, which is itself:

1. the ``CLM_SIDECAR_LAYOUT`` environment variable, else
2. ``[tool.clm] sidecar-layout`` in the nearest ancestor ``pyproject.toml``,
   else
3. ``None`` (the caller falls back to step 4 above).
"""

from __future__ import annotations

import os
from pathlib import Path

SIDECAR_LAYOUTS = ("subdir", "sibling")


def _coerce(value: str | None) -> str | None:
    """Normalise a layout string to ``"subdir"`` / ``"sibling"`` or ``None``."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SIDECAR_LAYOUTS else None


def _read_pyproject_layout(start: Path) -> str | None:
    """Return ``[tool.clm] sidecar-layout`` from the nearest ancestor pyproject.

    Walks upward from ``start`` (or its parent, if it is a file) to the first
    ``pyproject.toml`` and stops there — a project root that does not set the key
    yields ``None`` rather than leaking into an unrelated parent project.
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python <3.11 not supported
        return None

    base = start if start.is_dir() else start.parent
    for directory in (base, *base.parents):
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = data.get("tool", {}).get("clm", {}).get("sidecar-layout")
        return _coerce(value) if isinstance(value, str) else None
    return None


def resolve_course_sidecar_default(path: Path) -> str | None:
    """Return the course-wide sidecar-layout default near ``path``, or ``None``.

    ``CLM_SIDECAR_LAYOUT`` wins over ``[tool.clm] sidecar-layout``; an unset or
    unrecognised value falls through. Returns ``None`` when no course default is
    configured, leaving the caller's per-topic auto-detection in charge.
    """
    env = _coerce(os.environ.get("CLM_SIDECAR_LAYOUT"))
    if env is not None:
        return env
    return _read_pyproject_layout(path)


def effective_write_layout(path: Path, flag: str | None) -> str | None:
    """Fold the ``--layout`` flag with the course default into one write layout.

    Returns the value to pass as ``layout`` to ``expected_companion`` /
    ``extract_voiceover``:

    - the explicit ``flag`` if given (step 1);
    - ``"subdir"`` if the course default is ``subdir`` (step 3) — this still lets
      ``expected_companion``'s ``None``-auto pick the subdir when a ``voiceover/``
      directory already exists (step 2), since both resolve to the subdir;
    - otherwise ``None``, so ``expected_companion`` auto-detects (``voiceover/``
      directory present → subdir, else sibling).

    A course default of ``sibling`` is intentionally *not* forced: it is
    behaviourally identical to the ``None``-auto fallback (no dir → sibling; dir
    present → subdir, because per-topic presence outranks the course default).
    """
    if flag is not None:
        return flag
    if resolve_course_sidecar_default(path) == "subdir":
        return "subdir"
    return None
