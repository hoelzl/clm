"""Resolve the course-wide *default* for the authoring-sidecar layout.

This is a **write-time** convenience only: it chooses where a *newly created*
sidecar lands when neither an explicit ``--layout`` flag nor a per-topic sidecar
directory has already decided. It has two consumers:

* authoring tools (``clm voiceover extract`` / ``sync``) choosing where a new
  voiceover companion goes, and
* the build choosing where the *first* HTTP-replay cassette for a topic is
  recorded (``NotebookFile.expected_cassette_path``).

Either way it never changes *output* — the build always reads both layouts via
``resolve_companion`` / ``NotebookFile`` cassette resolution — so a course can
flip its default freely; only the on-disk location of a newly written sidecar
moves.

The full precedence for a new companion (highest first) is:

1. an explicit ``--layout {subdir,sibling}`` flag,
2. a per-topic sidecar directory that already exists,
3. the course default this module resolves,
4. the built-in fallback, ``sibling``.

This module owns only step 3, which is itself (see :func:`resolve_layout`):

1. the ``CLM_SIDECAR_LAYOUT`` environment variable, else
2. the per-course ``<sidecar-layout>`` value from the course spec (when the
   caller threads one in), else
3. ``[tool.clm] sidecar-layout`` in the nearest ancestor ``pyproject.toml``,
   else
4. ``None`` (the caller falls back to step 4 above).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from clm.core.utils.pyproject_settings import find_nearest_pyproject, read_tool_clm_key

SIDECAR_LAYOUTS = ("subdir", "sibling")


def _coerce(value: str | None) -> str | None:
    """Normalise a layout string to ``"subdir"`` / ``"sibling"`` or ``None``."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized in SIDECAR_LAYOUTS else None


@dataclass(frozen=True)
class SidecarLayoutResolution:
    """The resolved course-wide sidecar-layout default, and *why*.

    Mirrors ``CacheDirResolution`` in ``clm.infrastructure.llm.cache``: the
    provenance fields let ``clm config show`` / ``locate`` explain the
    resolution without re-deriving it. ``layout`` is ``None`` when nothing is
    configured (source ``"unset"``), leaving per-topic auto-detection in
    charge.
    """

    layout: str | None
    source: str  # "env" | "spec" | "pyproject" | "unset"
    pyproject_path: Path | None = None


def resolve_course_sidecar_default(path: Path) -> str | None:
    """Return the course-wide sidecar-layout default near ``path``, or ``None``.

    ``CLM_SIDECAR_LAYOUT`` wins over ``[tool.clm] sidecar-layout``; an unset or
    unrecognised value falls through. Returns ``None`` when no course default is
    configured, leaving the caller's per-topic auto-detection in charge.
    """
    return resolve_layout(None, path)


def resolve_layout(spec_default: str | None, path: Path) -> str | None:
    """Resolve the effective sidecar-layout default, highest precedence first.

    1. the ``CLM_SIDECAR_LAYOUT`` environment variable,
    2. ``spec_default`` — the per-course ``<sidecar-layout>`` from the course
       spec (pass ``None`` when there is no spec context),
    3. ``[tool.clm] sidecar-layout`` in the nearest ancestor ``pyproject.toml``.

    Each source is coerced to ``"subdir"`` / ``"sibling"``; an unset or
    unrecognised value falls through to the next. Returns ``None`` when nothing
    is configured, leaving the caller's per-topic auto-detection in charge.
    """
    return describe_layout(spec_default, path).layout


def describe_layout(spec_default: str | None, path: Path) -> SidecarLayoutResolution:
    """Resolve like :func:`resolve_layout`, but report the winning source too.

    The resolution logic lives here so ``resolve_layout`` and the
    ``clm config show`` / ``locate`` provenance display cannot drift apart.
    """
    env = _coerce(os.environ.get("CLM_SIDECAR_LAYOUT"))
    if env is not None:
        return SidecarLayoutResolution(layout=env, source="env")
    spec = _coerce(spec_default)
    if spec is not None:
        return SidecarLayoutResolution(layout=spec, source="spec")
    pyproject = find_nearest_pyproject(path)
    if pyproject is not None:
        from_file = _coerce(read_tool_clm_key(pyproject, "sidecar-layout"))
        if from_file is not None:
            return SidecarLayoutResolution(
                layout=from_file, source="pyproject", pyproject_path=pyproject
            )
    return SidecarLayoutResolution(layout=None, source="unset")


def effective_write_layout(path: Path, flag: str | None) -> str | None:
    """Fold the ``--layout`` flag with the course default into one write layout.

    Returns the value to pass as ``layout`` to ``expected_companion`` /
    ``extract_voiceover``:

    - the explicit ``flag`` if given (step 1);
    - else the course default (``"subdir"`` or ``"sibling"``) when one is
      configured — including an explicit ``"sibling"``, which **is** forced:
      the auto fallback now leans *subdir* for a new companion, so a course that
      deliberately asks for ``sibling`` must be honoured rather than collapsing
      into the auto path;
    - otherwise ``None``, so ``expected_companion`` auto-detects (existing
      ``voiceover/`` dir → subdir; else existing sibling for the deck → sibling;
      else → subdir).
    """
    if flag is not None:
        return flag
    return resolve_course_sidecar_default(path)
