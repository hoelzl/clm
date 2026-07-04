"""Reading deck texts at a git ref — the sync-free git helpers.

Extracted at the v3 cutover (#520 Phase 4) from the deleted v2 core
(``sync_plan._git_ref_text``) and shadow harness (``sync_shadow.
bundle_texts_at_ref``) because the keep-components need them: the structural
verify's no-drop check reads each half at ``HEAD``, and ``sync report
--since REF`` reads the whole ≤4-file bundle at a forensic ref. Everything
here is read-only and degrades to ``None`` when git is unavailable, the ref
does not resolve, or the file is untracked there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from clm.slides.voiceover_tools import COMPANION_SUBDIR, companion_name

__all__ = ["bundle_texts_at_ref", "git_ref_text"]


def _git_capture(cwd: Path, *args: str) -> str | None:
    """``git <args>`` run in ``cwd`` — stdout, or ``None`` on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _git_historical_paths(path: Path, ref: str) -> list[str]:
    """Repo-root-relative names ``path`` has had through history, newest first.

    Follows git rename detection (``git log --follow -M``) so a deck that was
    renamed (or whose content git tracks across a rename) can be located at an
    arbitrary ``ref`` even though its *current* name did not exist there. Each
    returned name is repo-root-relative (``git show <ref>:<name>`` addresses it
    directly). Empty when git is unavailable or the file has no tracked history.
    ``ref`` is accepted for symmetry but does not constrain the walk — the caller
    tries every historical name at ``ref`` and keeps the one that resolves (at any
    given commit the file exists under exactly one of them).
    """
    out = _git_capture(
        path.parent, "log", "--follow", "-M", "--name-only", "--format=", "--", path.name
    )
    if out is None:
        return []
    names: list[str] = []
    for line in out.splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


def git_ref_text(path: Path, ref: str = "HEAD") -> str | None:
    """The text of ``path`` at git ``ref`` (default ``HEAD``), or ``None``.

    ``None`` when git is unavailable, the file is untracked at ``ref`` (even after
    following renames), the ref does not resolve, or ``git show`` fails. ``ref`` may
    be any revision spec (``HEAD~1``, a commit SHA, ``origin/master``, …).

    Issue #2: a deck renamed since ``ref`` does not exist there under its *current*
    name, which used to degrade silently to "no baseline". We first try the
    current name (the fast, common path), then fall back to each name the file has
    had through history (rename-following), so a rename no longer hides the
    committed text. A topic *split* (one file becoming several) is not a git rename
    and is not recovered here.
    """
    text = _git_capture(path.parent, "show", f"{ref}:./{path.name}")
    if text is not None:
        return text
    for root_rel in _git_historical_paths(path, ref):
        text = _git_capture(path.parent, "show", f"{ref}:{root_rel}")
        if text is not None:
            return text
    return None


def _repo_root(path: Path) -> Path | None:
    out = _git_capture(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def _text_at_ref(root: Path, path: Path, ref: str) -> str | None:
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    return _git_capture(root, "show", f"{ref}:{rel}")


def bundle_texts_at_ref(
    de_path: Path, en_path: Path, ref: str
) -> tuple[str | None, str | None, str | None, str | None]:
    """The ≤4-file bundle texts at ``ref`` (``None`` = absent at that ref).

    Companions are resolved through the same subdir-then-sibling precedence
    as :func:`clm.slides.voiceover_tools.resolve_companion`, but against the
    ref's tree instead of the working tree.
    """
    root = _repo_root(de_path)
    if root is None:
        return None, None, None, None

    def companion_at_ref(deck_path: Path) -> str | None:
        name = companion_name(deck_path)
        for candidate in (
            deck_path.parent / COMPANION_SUBDIR / name,
            deck_path.with_name(name),
        ):
            text = _text_at_ref(root, candidate, ref)
            if text is not None:
                return text
        return None

    return (
        _text_at_ref(root, de_path, ref),
        _text_at_ref(root, en_path, ref),
        companion_at_ref(de_path),
        companion_at_ref(en_path),
    )
