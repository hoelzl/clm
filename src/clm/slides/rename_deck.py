"""Rename a split deck's file stem — pair + companions + ledger key + cache (#991).

The engine behind ``clm slides rename``. A deck's file stem is part of the
authoring surface (``slides_30_task_templates`` teaching agent skills is a
name drifted from its content), but renaming it by hand is a four-place
procedure with silent-failure modes:

* the two halves must move together (``derive_split_pair`` finds the twin by
  stem, so a lone renamed half reads as an unpaired deck);
* the separated voiceover companions are paired by stem too
  (:func:`clm.core.voiceover_companions.companion_name`) — left behind, the
  narration silently drops from the build;
* the per-topic sync ledger keys its deck section by the stem
  (:func:`clm.slides.doc_ledger.deck_key_for`), so the renamed deck reads as
  cold and a later ``confirm`` banks a possibly stale twin — the #572 footgun
  one level up from ``rename-id``;
* the build cache keys its lookup rows on the absolute input path, so a
  renamed deck re-executes although its payloads are unchanged.

This module plans the whole move (so every refusal fires before anything is
touched), executes it atomically enough — ``git mv`` when inside a work tree,
else a rename — and migrates the ledger section and the cache rows as pure
renames, never re-fingerprinting. The CLI adds the report and the
post-rename validation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from attrs import field, frozen

from clm.core.course_renumber import in_git_work_tree, move_path
from clm.core.slide_text.pairing import split_lang_tag
from clm.core.utils.path_utils import SUPPORTED_PROG_LANG_EXTENSIONS
from clm.core.voiceover_companions import companion_locations, companion_name
from clm.slides import doc_ledger
from clm.slides.doc_ledger import deck_key_for, ledger_path_for

logger = logging.getLogger(__name__)

__all__ = [
    "DeckRenameError",
    "DeckRenamePlan",
    "FileMove",
    "apply_deck_rename",
    "plan_deck_rename",
    "validate_new_stem",
]

#: The routing prefixes the build discovers decks by. A stem that drops its
#: prefix would vanish from every course that includes the topic.
_ROUTING_PREFIXES = ("slides_", "topic_", "project_")


class DeckRenameError(ValueError):
    """A refusal raised while planning (nothing has been touched)."""


@frozen
class FileMove:
    """One file the rename moves, with its role for the report."""

    role: str  # "de" | "en" | "de_companion" | "en_companion"
    old: Path
    new: Path


@frozen
class DeckRenamePlan:
    """Everything the rename will do, computed before anything is touched."""

    old_stem: str
    new_stem: str
    moves: tuple[FileMove, ...]
    ledger_path: Path
    #: The ledger holds a section for the old stem (it will be re-keyed).
    ledger_has_section: bool
    warnings: tuple[str, ...] = field(factory=tuple)

    @property
    def de(self) -> FileMove | None:
        return next((m for m in self.moves if m.role == "de"), None)

    @property
    def en(self) -> FileMove | None:
        return next((m for m in self.moves if m.role == "en"), None)

    @property
    def companions(self) -> tuple[FileMove, ...]:
        return tuple(m for m in self.moves if m.role.endswith("_companion"))


def validate_new_stem(new_stem: str, *, old_half: Path) -> str:
    """Check ``new_stem`` is a bare, usable deck stem for ``old_half``'s deck.

    A stem is the language-free, extension-free filename: no path separators,
    no ``.de`` / ``.en`` tag, no source extension (``slides_x.de.py`` is not a
    stem — ``slides_x`` is). A deck whose old stem carries a routing prefix
    must keep one, or the build stops discovering it.
    """
    stem = new_stem.strip()
    if not stem:
        raise DeckRenameError("NEW_STEM must be a non-empty deck stem.")
    if "/" in stem or "\\" in stem:
        raise DeckRenameError(
            f'"{stem}" contains a path separator — NEW_STEM is a bare filename stem '
            "(the deck stays in its topic directory)."
        )
    if any(ch in stem for ch in '"\t\n\r') or stem != stem.strip():
        raise DeckRenameError(f'"{stem}" is not a usable stem (no whitespace or double-quotes).')
    lowered = stem.lower()
    for ext in sorted(SUPPORTED_PROG_LANG_EXTENSIONS):
        if lowered.endswith(ext):
            raise DeckRenameError(
                f'"{stem}" ends with the source extension "{ext}" — pass the bare stem '
                f'("{stem[: -len(ext)]}"); the extension and language tag are kept.'
            )
    for lang in ("de", "en"):
        if lowered.endswith(f".{lang}"):
            raise DeckRenameError(
                f'"{stem}" ends with the language tag ".{lang}" — pass the bare stem '
                f'("{stem[: -len(lang) - 1]}"); both halves are renamed together.'
            )
    old_stem = deck_key_for(old_half)
    if stem == old_stem:
        raise DeckRenameError(f'"{stem}" is already the deck\'s stem — nothing to rename.')
    old_prefixed = old_stem.startswith(_ROUTING_PREFIXES)
    if old_prefixed and not stem.startswith(_ROUTING_PREFIXES):
        prefixes = " / ".join(f'"{p}"' for p in _ROUTING_PREFIXES)
        raise DeckRenameError(
            f'"{stem}" has no routing prefix ({prefixes}) — the build discovers decks by '
            f'it, so "{old_stem}" would vanish from every course that includes this topic.'
        )
    return stem


def plan_deck_rename(
    de: Path | None,
    en: Path | None,
    new_stem: str,
) -> DeckRenamePlan:
    """Plan the rename of the pair ``(de, en)`` (either may be ``None`` for a
    ``--single`` rename) to ``new_stem``.

    Every refusal fires here, before any file moves: a target half or
    companion that already exists, a companion present in both layouts
    (``resolve_companion`` would silently take one and leave the other with
    the old name — the same ambiguity ``clm validate`` reports), or a ledger
    that already holds a section under the new stem (re-keying onto it would
    clobber another deck's trust).
    """
    halves = [(role, half) for role, half in (("de", de), ("en", en)) if half is not None]
    if not halves:
        raise DeckRenameError("nothing to rename: no deck half given.")
    anchor = halves[0][1]
    stem = validate_new_stem(new_stem, old_half=anchor)
    old_stem = deck_key_for(anchor)

    moves: list[FileMove] = []
    warnings: list[str] = []
    for role, half in halves:
        tag = split_lang_tag(half)
        if tag is None:
            raise DeckRenameError(
                f"{half.name} has no .de/.en language tag; `rename` works on split "
                "decks (<deck>.de.<ext> / <deck>.en.<ext>)."
            )
        new_half = half.with_name(f"{stem}.{tag}{half.suffix}")
        moves.append(FileMove(role, half, new_half))
        locations = companion_locations(half)
        if len(locations) > 1:
            names = ", ".join(str(p) for p in locations)
            raise DeckRenameError(
                f"{half.name} has a voiceover companion in both layouts ({names}); "
                "delete or merge one copy (see `clm validate`) before renaming."
            )
        if locations:
            old_companion = locations[0]
            # Same directory as the existing companion (sibling or voiceover/).
            new_companion = old_companion.with_name(companion_name(new_half))
            moves.append(FileMove(f"{role}_companion", old_companion, new_companion))

    # Collision refusal — every target, halves and companions alike.
    for move in moves:
        if move.new.exists():
            raise DeckRenameError(
                f"{move.new.name} already exists in {move.new.parent} — renaming "
                f'"{old_stem}" to "{stem}" would overwrite it. Choose an unused stem.'
            )
    # A stale, differently-named twin would make the renamed half pair with
    # the wrong file: refuse a half whose new twin name is taken by a file
    # that is not part of this rename.
    for _role, half in halves:
        tag = split_lang_tag(half)
        other_tag = "en" if tag == "de" else "de"
        twin_target = half.with_name(f"{stem}.{other_tag}{half.suffix}")
        if twin_target.exists() and all(m.new != twin_target for m in moves):
            raise DeckRenameError(
                f"{twin_target.name} already exists — the renamed {tag} half would pair "
                f"with it; rename both halves together (drop --single) or pick another stem."
            )

    if old_stem.startswith(_ROUTING_PREFIXES) and not stem.startswith(old_stem.split("_", 1)[0]):
        warnings.append(
            f'the routing prefix changes ("{old_stem.split("_", 1)[0]}_" → '
            f'"{stem.split("_", 1)[0]}_") — the deck is routed as a different kind of file.'
        )

    ledger_path = ledger_path_for(anchor)
    ledger = doc_ledger.load(ledger_path) if ledger_path.is_file() else doc_ledger.TopicLedger()
    if stem in ledger.decks:
        raise DeckRenameError(
            f'the sync ledger {ledger_path} already holds a section for "{stem}" — '
            "re-keying onto it would clobber that deck's recorded trust. Remove the "
            "stale section first (or choose another stem)."
        )
    has_section = old_stem in ledger.decks
    warnings.append(
        "evergreen artifacts that carry deck file stems (the cohort "
        "`video-schedule.csv` `deck_file` column) go stale — regenerate them "
        "(`clm run refresh-overviews SPEC` when the spec declares that task, else "
        "`clm calendar generate` / `clm export outline`)."
    )
    return DeckRenamePlan(
        old_stem=old_stem,
        new_stem=stem,
        moves=tuple(moves),
        ledger_path=ledger_path,
        ledger_has_section=has_section,
        warnings=tuple(warnings),
    )


def apply_deck_rename(plan: DeckRenamePlan, *, use_git: bool | None = None) -> tuple[bool, bool]:
    """Move the files and re-key the ledger section.

    Returns ``(used_git, ledger_migrated)``. Moves go through ``git mv``
    inside a work tree (history-preserving, 100 % renames), else
    ``Path.rename``; the plan's collision checks already ran, and the pair's
    files are moved half-first so a crash mid-way leaves a state
    ``derive_split_pair`` still recognises as "twin missing" rather than a
    mispaired deck. The ledger section is re-keyed as a pure rename — the
    recorded fingerprints are keyed by ``slide_id`` / position and never by
    the stem, so the deck stays warm.
    """
    if not plan.moves:
        return False, False
    git = in_git_work_tree(plan.moves[0].old.parent) if use_git is None else use_git
    for move in plan.moves:
        move.new.parent.mkdir(parents=True, exist_ok=True)
        move_path(move.old, move.new, git=git)
    migrated = False
    if plan.ledger_has_section:
        migrated = doc_ledger.rename_deck_key(plan.ledger_path, plan.old_stem, plan.new_stem)
    return git, migrated
