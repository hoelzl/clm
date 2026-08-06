"""Voiceover companion file naming and location (Phase 8 S1, #802).

Where a slide deck's extracted voiceover companion lives and how it is
found. Pure path logic shared by the build (payload-time narration merge,
``NotebookFile.companion_voiceover_path``) and the authoring tools
(``extract``/``inline``/``sync``/``validate``) — the write-side workflows
stay in ``clm.slides.voiceover_tools``.
"""

from pathlib import Path

# Topic-relative subdirectory that may hold extracted voiceover companions
# instead of placing them as siblings of the slide file. Auto-detected on read
# by the companion file's presence (see :func:`resolve_companion`) — the
# voiceover analogue of the ``cassettes/`` cassette sidecar.
COMPANION_SUBDIR = "voiceover"


def companion_name(slide_path: Path) -> str:
    """Return the companion voiceover *filename* for a slide file.

    Directory-independent — the name only. Known slide prefixes are replaced
    with ``voiceover_``; the slide's own extension (``.py``/``.cs``/``.cpp``/
    ``.java``/``.ts``) is preserved, and any ``.de`` / ``.en`` language tag (part
    of the stem) is kept so the two halves of a split deck never collide:

    ``slides_intro.py`` → ``voiceover_intro.py``
    ``slides_010_x.de.cs`` → ``voiceover_010_x.de.cs``
    ``topic_overview.cpp`` → ``voiceover_overview.cpp``
    ``project_setup.py`` → ``voiceover_setup.py``
    """
    stem = slide_path.stem
    ext = slide_path.suffix
    # Replace known prefixes
    for prefix in ("slides_", "topic_", "project_"):
        if stem.startswith(prefix):
            return f"voiceover_{stem[len(prefix) :]}{ext}"
    # Fallback: prepend voiceover_
    return f"voiceover_{stem}{ext}"


def companion_path(slide_path: Path) -> Path:
    """Return the *sibling* companion path for a slide file.

    This is the nominal companion location next to the slide — the
    backward-compatible default used as a write target and for display. To find
    a companion that may have been relocated into the ``voiceover/``
    subdirectory, use :func:`resolve_companion` instead.
    """
    return slide_path.with_name(companion_name(slide_path))


def companion_locations(slide_path: Path) -> list[Path]:
    """Return every *existing* companion path for a slide, in read-precedence
    order (the ``voiceover/`` subdir before the sibling).

    Normally length 0 or 1. Length ≥ 2 means the same companion exists in *both*
    the relocated subdir and as a sibling — an ambiguity where
    :func:`resolve_companion` silently prefers the relocated copy. ``clm
    validate`` surfaces that case so it can be reconciled.
    """
    name = companion_name(slide_path)
    out: list[Path] = []
    nested = slide_path.parent / COMPANION_SUBDIR / name
    if nested.exists():
        out.append(nested)
    sibling = slide_path.with_name(name)
    if sibling.exists():
        out.append(sibling)
    return out


def resolve_companion(slide_path: Path) -> Path | None:
    """Return the *existing* companion for a slide file, or ``None``.

    Prefers the relocated ``<topic>/voiceover/<name>`` when present, else the
    sibling ``<topic>/<name>``. Location-config-free: it finds the companion in
    either layout, so reads (the build merge, ``inline``, ``validate``, the
    ``sync`` baseline) work without knowing how a given topic is organised. The
    ``voiceover/`` subdirectory is auto-detected by the file's presence — exactly
    as ``cassettes/`` is for cassettes. When a companion exists in *both*
    locations the relocated one wins.
    """
    locations = companion_locations(slide_path)
    return locations[0] if locations else None


def expected_companion(slide_path: Path, *, layout: str | None = None) -> Path:
    """Return the *write target* path for a slide's companion.

    Where a newly-created companion (``extract``, ``sync``, ``split``) should be
    written. Resolution:

    - ``layout="subdir"``: ``<topic>/voiceover/<name>`` (the dir is created by
      the caller on write).
    - ``layout="sibling"``: ``<topic>/<name>`` (next to the slide).
    - ``layout=None`` (auto): prefer the ``voiceover/`` subdir — when that
      directory already exists, **or** for a brand-new companion. The one
      exception is a deck that *already* has a sibling companion: that one stays
      a sibling so a single deck is never split across both layouts. So the auto
      precedence is: existing ``voiceover/`` dir → subdir; else existing sibling
      companion for *this* deck → sibling; else → subdir (the default for new
      companions). ``NotebookFile.expected_cassette_path`` uses the same rule for
      cassettes.

    Reads do not consult this — they use :func:`resolve_companion`, which finds
    the companion in either layout regardless of the write target.
    """
    name = companion_name(slide_path)
    parent = slide_path.parent
    if layout == "subdir":
        return parent / COMPANION_SUBDIR / name
    if layout == "sibling":
        return parent / name
    if (parent / COMPANION_SUBDIR).is_dir():
        return parent / COMPANION_SUBDIR / name
    if (parent / name).exists():
        return parent / name
    return parent / COMPANION_SUBDIR / name
