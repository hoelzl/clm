"""Scope a set of deck files down to part of a corpus (gap #4).

Conversions routinely want to touch only *part* of a slides tree — mint ids on
the bilingual decks but leave ``.de.py`` / ``.en.py`` split pairs for
``clm slides sync``; skip an ``_archive/`` directory; touch only the decks that
actually ship. The agent that hit this did it by running over everything and then
``git checkout``-ing the files it shouldn't have touched — clumsy and error-prone.

This module centralizes the three predicates so ``clm slides assign-ids`` and
``clm slides normalize`` (and anything else operating on a deck set) share one
implementation:

- ``--only bilingual|split`` — keep only bilingual decks (no ``.de``/``.en`` tag)
  or only split halves;
- ``--exclude GLOB`` — drop decks matching a glob (matched against the full path
  *and* each path component, so ``--exclude _archive`` drops an ``_archive/`` dir);
- ``--shipping-only`` — keep only decks reachable from a course spec.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path

from clm.slides.pairing import split_lang_tag


def _matches_any_glob(path: Path, patterns: Iterable[str]) -> bool:
    """Whether *path* matches any of *patterns*.

    A pattern matches if it globs the full POSIX path string or any single path
    component — so a bare ``_archive`` drops every deck under an ``_archive/``
    directory without the caller needing ``*/_archive/*``.
    """
    posix = path.as_posix()
    parts = path.parts
    for pat in patterns:
        if fnmatch.fnmatch(posix, pat) or any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
    return False


def _keep_for_only(path: Path, only: str | None) -> bool:
    """Apply the ``--only bilingual|split`` filter to a single deck."""
    if only is None:
        return True
    tag = split_lang_tag(path)
    if only == "bilingual":
        return tag is None
    if only == "split":
        return tag is not None
    raise ValueError(f"Unknown --only value: {only!r} (expected 'bilingual' or 'split')")


def filter_decks(
    files: Iterable[Path],
    *,
    only: str | None = None,
    exclude: Iterable[str] = (),
    shipping: set[Path] | None = None,
) -> list[Path]:
    """Return the decks in *files* that pass every active scope predicate.

    Args:
        files: Candidate deck paths.
        only: ``"bilingual"``, ``"split"``, or ``None`` (no language filter).
        exclude: Glob patterns; a deck matching any is dropped.
        shipping: When given, keep only decks whose resolved path is in this set
            (the shipping set from :func:`clm.core.spec_decks.shipping_set`).

    Returns:
        The surviving decks, order preserved.
    """
    exclude = tuple(exclude)
    kept: list[Path] = []
    for f in files:
        if not _keep_for_only(f, only):
            continue
        if exclude and _matches_any_glob(f, exclude):
            continue
        if shipping is not None and f.resolve() not in shipping:
            continue
        kept.append(f)
    return kept


def course_root_for_path(path: Path) -> Path | None:
    """Infer the course root (the parent of ``slides/``) from a slides path.

    Returns ``None`` when no ``slides`` ancestor is found, so callers can fall
    back to an explicit ``--specs-dir`` / ``--data-dir``.
    """
    resolved = path.resolve()
    if resolved.name == "slides":
        return resolved.parent
    for parent in resolved.parents:
        if parent.name == "slides":
            return parent.parent
    return None


def resolve_shipping_set(specs_dir: Path, slides_dir: Path) -> set[Path]:
    """The shipping set across every ``*.xml`` spec in *specs_dir*.

    Thin wrapper over :func:`clm.core.spec_decks.shipping_set` so deck-scoping
    callers don't each re-glob the specs directory.
    """
    from clm.core.spec_decks import shipping_set

    spec_files = sorted(specs_dir.glob("*.xml"))
    return shipping_set(spec_files, slides_dir)
