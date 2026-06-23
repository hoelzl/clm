"""Slide file normalization engine.

Applies mechanical fixes to percent-format ``.py`` slide files:

- **Placeholder start demotion**: removes the ``start`` tag from code cells
  with no real scaffolding (body only ``# Your solution here`` / ``pass`` /
  ``...``) when the workshop solution that follows is a markdown ``alt`` /
  ``completed`` run rather than a paired ``completed`` code cell
- **Tag migration**: ``alt`` → ``completed`` after ``start`` cells
- **Workshop tag insertion**: adds ``workshop`` to workshop heading cells,
  and symmetrizes ``workshop``/``end-workshop`` tags across DE/EN heading
  pairs so single-language split builds detect the same workshop ranges as
  bilingual builds
- **Interleaving normalization**: reorders cells so paired DE/EN cells
  are adjacent

Uses a three-tier strategy for interleaving:

- Tier 1: Structural count check (DE vs EN per category)
- Tier 2: Positional pairing with similarity verification
- Tier 3: Report for issues that need manual resolution
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from clm.core.topic_resolver import (
    build_topic_map,
    find_slide_files,
    find_slide_files_recursive,
    matches_for_binding,
)
from clm.notebooks.slide_parser import comment_token_for_path, parse_cell_header
from clm.slides.pairing import build_slide_groups
from clm.slides.raw_cells import RawCell as _RawCell
from clm.slides.raw_cells import reconstruct as _reconstruct
from clm.slides.raw_cells import split_cells as _split_raw_cells

if TYPE_CHECKING:
    from clm.slides.assign_ids import AssignOptions

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """A single change applied (or proposed in dry-run) by the normalizer."""

    file: str
    operation: str  # "tag_migration", "workshop_tags", "interleaving"
    line: int
    description: str


@dataclass
class ReviewItem:
    """An issue found during normalization that requires manual review."""

    file: str
    issue: str  # "count_mismatch", "similarity_failure"
    suggestion: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class NormalizationResult:
    """Result of normalizing one or more slide files."""

    files_modified: int = 0
    changes: list[Change] = field(default_factory=list)
    review_items: list[ReviewItem] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.changes and not self.review_items:
            return "clean"
        if self.review_items:
            return "partial"
        return "applied"

    @property
    def summary(self) -> str:
        parts: list[str] = []
        if self.files_modified:
            parts.append(
                f"{self.files_modified} file{'s' if self.files_modified != 1 else ''} modified"
            )
        op_counts: dict[str, int] = {}
        for c in self.changes:
            op_counts[c.operation] = op_counts.get(c.operation, 0) + 1
        for op, count in sorted(op_counts.items()):
            parts.append(f"{count} {op.replace('_', ' ')}{'s' if count != 1 else ''}")
        if self.review_items:
            ri = len(self.review_items)
            parts.append(f"{ri} item{'s' if ri != 1 else ''} for review")
        if not parts:
            parts.append("no changes needed")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

ALL_OPERATIONS = frozenset(
    {
        "preamble_code",
        "placeholder_start",
        "tag_migration",
        "workshop_tags",
        "interleaving",
        "slide_ids",
        "cell_spacing",
    }
)


# ---------------------------------------------------------------------------
# Preamble code: wrap bare code folded into a header cell into its own cell
# ---------------------------------------------------------------------------


def _apply_preamble_code(
    cells: list[_RawCell], file_path: str, comment_token: str = "#"
) -> list[Change]:
    """Move code folded into a leading j2 (header) cell into its own ``%% `` cell.

    Code between the ``# {{ header(...) }}`` macro call and the first ``%% ``
    cell has no cell marker, so jupytext folds it into the header cell and the
    title markdown — silently dropped from a DE build yet kept in the split DE
    half, so bilingual and split builds diverge (issue #253). Re-homing it in a
    shared ``%% `` code cell makes every build include it identically (and run
    it as code). Mutates ``cells`` in place; idempotent on a conforming deck.
    Runs before the other passes so they see the canonical cell structure;
    inter-cell spacing is finalized later by ``cell_spacing``.
    """
    from clm.slides.preamble_code import wrap_preamble_code

    changes: list[Change] = []
    for finding in wrap_preamble_code(cells, comment_token):
        changes.append(
            Change(
                file=file_path,
                operation="preamble_code",
                line=finding.first_code_line,
                description=(
                    "Wrapped code that preceded the first cell marker into a "
                    f"`{comment_token} %%` code cell"
                ),
            )
        )
    return changes


# ---------------------------------------------------------------------------
# Placeholder start demotion: scaffolding-less start cells (#233 item 4a)
# ---------------------------------------------------------------------------

# A line that is *only* a solution-placeholder comment. Anchored on both ends:
# a placeholder phrase followed by real text (e.g. "# Your code here: Train
# linear model") is a genuine hint, not a placeholder.
_PLACEHOLDER_COMMENT_RE = re.compile(
    r"^(?:#|//)\s*(?:"
    r"your (?:solution|code) here"
    r"|(?:ihre?|deine?) l(?:ö|oe)sung hier"
    r"|(?:ihr|dein) code hier"
    r")\s*[:!.]?\s*$",
    re.IGNORECASE,
)

# Code lines that carry no scaffolding on their own.
_PLACEHOLDER_CODE_LINES = frozenset({"pass", "..."})


def _is_placeholder_body(cell: _RawCell) -> bool:
    """True if a code cell's body is only solution placeholders (no scaffolding)."""
    non_blank = [ln.strip() for ln in cell.lines[1:] if ln.strip()]
    if not non_blank:
        return False
    return all(
        ln in _PLACEHOLDER_CODE_LINES or _PLACEHOLDER_COMMENT_RE.match(ln) for ln in non_blank
    )


def _remove_tag_from_header(header: str, tag: str) -> str:
    """Remove a tag from a cell header line, dropping ``tags=[]`` if it empties."""
    tags_match = re.search(r"tags=\[([^\]]*)\]", header)
    if not tags_match:
        return header
    kept = [
        t.strip()
        for t in tags_match.group(1).split(",")
        if t.strip() and t.strip().strip("\"'") != tag
    ]
    if kept:
        new_attr = f"tags=[{', '.join(kept)}]"
        return header[: tags_match.start()] + new_attr + header[tags_match.end() :]
    before = header[: tags_match.start()].rstrip()
    after = header[tags_match.end() :].strip()
    return f"{before} {after}" if after else before


def _apply_placeholder_start(cells: list[_RawCell], file_path: str) -> list[Change]:
    """Demote ``start`` cells that hold no scaffolding (issue #233 item 4a).

    Workshop tasks are authored as a plain placeholder cell (``# Your solution
    here``) followed by markdown/code ``alt`` solution cells — the workshop
    range mechanism blanks the solutions in code-along output, so the
    ``start``/``completed`` pairing has no role there. Tagging the placeholder
    ``start`` anyway is a recurring authoring slip, and ``tag_migration`` then
    compounds it by promoting the adjacent markdown ``alt`` to ``completed`` —
    a shape that is semantically impossible (a markdown cell cannot be the
    solution code of a live-coding pair).

    The fix mirrors the manual repair applied across the PythonCourses decks:
    drop the ``start`` tag, and rename an already-promoted markdown
    ``completed`` partner back to ``alt``. The discriminator is deliberately
    a conjunction — the ``start`` body must be placeholder-only AND the
    immediately following cell must be a markdown ``alt``/``completed`` cell.
    Placeholder ``start`` cells paired with a *code* ``completed`` cell are
    left untouched: that pairing validates and renders correctly.

    Must run before ``_apply_tag_migration`` so the demotion wins before the
    migration can promote the adjacent markdown ``alt``.
    """
    changes: list[Change] = []

    for idx, cell in enumerate(cells):
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "code" or "start" not in meta.tags:
            continue
        if not _is_placeholder_body(cell):
            continue

        nxt = cells[idx + 1] if idx + 1 < len(cells) else None
        if nxt is None or nxt.metadata.is_j2 or nxt.metadata.cell_type != "markdown":
            continue
        nxt_tags = nxt.metadata.tags
        if "completed" not in nxt_tags and "alt" not in nxt_tags:
            continue

        new_header = _remove_tag_from_header(cell.header, "start")
        cell.header = new_header
        cell.metadata = parse_cell_header(new_header)
        changes.append(
            Change(
                file=file_path,
                operation="placeholder_start",
                line=cell.line_number,
                description=(
                    "Removed 'start' tag from placeholder-only cell "
                    "(workshop solution follows as markdown, not a code 'completed')"
                ),
            )
        )

        if "completed" in nxt_tags:
            new_nxt_header = nxt.header.replace('"completed"', '"alt"')
            nxt.header = new_nxt_header
            nxt.metadata = parse_cell_header(new_nxt_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="placeholder_start",
                    line=nxt.line_number,
                    description=(
                        'Renamed "completed" -> "alt" '
                        "(markdown cell promoted after a placeholder-only start)"
                    ),
                )
            )

    return changes


# ---------------------------------------------------------------------------
# Tag migration: alt → completed after start cells
# ---------------------------------------------------------------------------


def _apply_tag_migration(cells: list[_RawCell], file_path: str) -> list[Change]:
    changes: list[Change] = []
    prev_has_start = False

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2:
            prev_has_start = False
            continue

        if prev_has_start and "alt" in meta.tags:
            new_header = cell.header.replace('"alt"', '"completed"')
            cell.header = new_header
            cell.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="tag_migration",
                    line=cell.line_number,
                    description='Renamed "alt" -> "completed" (follows start cell)',
                )
            )

        prev_has_start = "start" in meta.tags

    return changes


# ---------------------------------------------------------------------------
# Workshop tags: add 'workshop' to workshop heading cells
# ---------------------------------------------------------------------------

_WORKSHOP_RE = re.compile(r"^(?:#|//)\s+##\s+(Workshop|Mini-Workshop)\s*:", re.MULTILINE)


def _apply_workshop_tags(cells: list[_RawCell], file_path: str) -> list[Change]:
    changes: list[Change] = []

    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        if "workshop" in meta.tags:
            continue

        if _WORKSHOP_RE.search(cell.body):
            new_header = _add_tag_to_header(cell.header, "workshop")
            cell.header = new_header
            cell.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="workshop_tags",
                    line=cell.line_number,
                    description="Added 'workshop' tag to workshop heading cell",
                )
            )

    return changes


def _add_tag_to_header(header: str, tag: str) -> str:
    """Add a tag to a cell header line."""
    tags_match = re.search(r"tags=\[([^\]]*)\]", header)
    if tags_match:
        existing = tags_match.group(1).strip()
        if existing:
            new_tags = f'{existing}, "{tag}"'
        else:
            new_tags = f'"{tag}"'
        return header[: tags_match.start()] + f"tags=[{new_tags}]" + header[tags_match.end() :]
    return header.rstrip() + f' tags=["{tag}"]'


# ---------------------------------------------------------------------------
# Workshop tag symmetry: propagate workshop/end-workshop across DE/EN pairs
# ---------------------------------------------------------------------------

# Tags that scope a *slide* (a DE/EN pair), not a single language. They must
# appear on both halves of a slide so that a single-language split build sees
# the same workshop ranges a bilingual build does.
_SLIDE_SCOPED_TAGS = ("workshop", "end-workshop")


def _apply_workshop_symmetry(cells: list[_RawCell], file_path: str) -> list[Change]:
    """Propagate slide-scoped ``workshop``/``end-workshop`` tags across pairs.

    ``workshop`` and ``end-workshop`` mark a *slide* (a DE/EN heading pair) as a
    workshop boundary. The notebook build derives workshop ranges from these
    tags to decide which code cells to blank in the Code-Along/Partial kinds.

    A *bilingual* build runs that range scan over the interleaved cell stream,
    so a tag on **either** language's heading establishes a range that — by
    cell index — also covers the other language's cells. A single-language
    *split* build (``*.de.py`` / ``*.en.py``) only sees one language: if the
    tag is missing on that language's heading, no range is detected and the
    workshop's solution code leaks into the Code-Along/Partial output instead
    of being blanked.

    This pass closes that gap by copying the tag to the untagged half of every
    DE/EN heading pair. Bilingual output is unchanged (the range was already
    detected); split output now matches it. Headings must be adjacent in source
    order to be paired (see :func:`clm.slides.pairing.build_slide_groups`),
    which holds after interleaving and after ``unify``.
    """
    changes: list[Change] = []
    for group in build_slide_groups(cells):
        if len(group) != 2:
            continue
        i, j = group
        for tag in _SLIDE_SCOPED_TAGS:
            i_has = tag in cells[i].metadata.tags
            j_has = tag in cells[j].metadata.tags
            if i_has == j_has:
                continue
            target = cells[j] if i_has else cells[i]
            new_header = _add_tag_to_header(target.header, tag)
            target.header = new_header
            target.metadata = parse_cell_header(new_header)
            changes.append(
                Change(
                    file=file_path,
                    operation="workshop_tags",
                    line=target.line_number,
                    description=(
                        f"Propagated '{tag}' tag to paired {target.metadata.lang or '?'} heading"
                    ),
                )
            )
    return changes


# ---------------------------------------------------------------------------
# Interleaving: three-tier DE/EN reordering
# ---------------------------------------------------------------------------

_PAIRING_CATEGORIES = ("markdown", "code", "voiceover", "notes")


def _classify_cell(cell: _RawCell) -> str:
    """Classify a cell for interleaving purposes.

    Returns one of: ``"j2"``, ``"shared"``, ``"markdown"``, ``"code"``,
    ``"voiceover"``, ``"notes"``.
    """
    meta = cell.metadata
    if meta.is_j2:
        return "j2"
    if meta.is_narrative:
        if "voiceover" in meta.tags:
            return "voiceover"
        return "notes"
    if meta.lang is None:
        return "shared"
    if meta.cell_type == "code":
        return "code"
    return "markdown"


def _is_start_completed_pair(de_cell: _RawCell, en_cell: _RawCell) -> bool:
    """True if a DE/EN code pair is a ``start``/``completed`` cohesion unit.

    A same-language ``start`` cell (or its paired ``completed`` cell)
    represents the *same* logical cell shown in two output variants. Its
    DE/EN counterpart is identified by the matching ``start``/``completed``
    tag and document position — not by content similarity, which routinely
    differs for localized identifiers (e.g. ``begruessung`` vs ``greeting``).
    """
    de_tags = set(de_cell.metadata.tags)
    en_tags = set(en_cell.metadata.tags)
    return ("start" in de_tags and "start" in en_tags) or (
        "completed" in de_tags and "completed" in en_tags
    )


#: Similarity checks that a *localization* divergence legitimately fails — different
#: code identifiers across DE/EN (``code_structure``) or a length difference
#: (``line_count``). When a pair is ALREADY adjacent (no reorder is needed) and *only*
#: these fail, it is correctly interleaved and not a structural authoring error, so it is
#: not flagged (#236, the agent-confirmed-interleave convergence). The structural checks
#: (``tags`` / ``heading_level`` / ``bullet_count``) are NOT here: a mismatch in those is
#: a likely authoring bug, so it still flags even when adjacent (``clm course gate`` relies
#: on that).
_SOFT_SIMILARITY_CHECKS = frozenset({"code_structure", "line_count"})


def _apply_interleaving(
    cells: list[_RawCell],
    file_path: str,
    *,
    canonicalize_start_completed: bool = False,
    confirmed_pairings: set[tuple[int, int]] | None = None,
) -> tuple[list[_RawCell], list[Change], list[ReviewItem]]:
    """Reorder cells for proper DE/EN interleaving.

    Args:
        cells: The cells to reorder in place (returns a new list).
        file_path: Source path, used for change/review reporting.
        canonicalize_start_completed: When ``True``, ``start``/``completed``
            code pairs are paired *structurally* (by tag + position) and
            forced into the canonical interleave
            ``[DE_start, EN_start, DE_completed, EN_completed]``, bypassing
            the content-similarity gate that otherwise leaves them in the
            permitted cohesion layout ``[DE_start, DE_completed, EN_start,
            EN_completed]``. Used to pre-normalize decks before a split, so
            the round-trip ``unify(split(deck)) == deck`` holds byte-for-byte.
        confirmed_pairings: ``{(de_line, en_line)}`` the agent confirmed correct
            from the ``similarity_failure`` worklist (#236). A positional pair
            whose ``(de.line, en.line)`` is in the set bypasses the similarity gate
            and IS reordered into adjacency — once adjacent, a re-run leaves it clean
            via the localization convergence above. Line numbers are taken from the
            same (unmodified) file the worklist reported, so a drifted pairing simply
            does not match and stays flagged (fail-safe).

    Returns ``(reordered_cells, changes, review_items)``.
    """
    confirmed_pairings = confirmed_pairings or set()
    changes: list[Change] = []
    review_items: list[ReviewItem] = []

    # Classify and group cells by (language, category)
    de_by_cat: dict[str, list[int]] = {cat: [] for cat in _PAIRING_CATEGORIES}
    en_by_cat: dict[str, list[int]] = {cat: [] for cat in _PAIRING_CATEGORIES}

    for idx, cell in enumerate(cells):
        cat = _classify_cell(cell)
        if cat in _PAIRING_CATEGORIES:
            if cell.metadata.lang == "de":
                de_by_cat[cat].append(idx)
            elif cell.metadata.lang == "en":
                en_by_cat[cat].append(idx)

    # Tier 1 + 2: count check and positional pairing with similarity
    confident_pairs: dict[int, int] = {}  # de_idx → en_idx

    for cat in _PAIRING_CATEGORIES:
        de_indices = de_by_cat[cat]
        en_indices = en_by_cat[cat]

        if len(de_indices) != len(en_indices):
            # Tier 1 failure: count mismatch → report
            if de_indices or en_indices:
                review_items.append(
                    ReviewItem(
                        file=file_path,
                        issue="count_mismatch",
                        suggestion=(
                            f"DE has {len(de_indices)} {cat} cell(s) "
                            f"but EN has {len(en_indices)}. "
                            f"Review whether cells need to be added, removed, "
                            f"or reclassified."
                        ),
                        details={
                            "category": cat,
                            "de_count": len(de_indices),
                            "en_count": len(en_indices),
                            "de_cells": [
                                {
                                    "line": cells[i].line_number,
                                    "preview": _cell_preview(cells[i]),
                                }
                                for i in de_indices
                            ],
                            "en_cells": [
                                {
                                    "line": cells[i].line_number,
                                    "preview": _cell_preview(cells[i]),
                                }
                                for i in en_indices
                            ],
                        },
                    )
                )
            continue

        # Tier 2: positional pairing with similarity verification
        for pair_idx, (de_i, en_i) in enumerate(zip(de_indices, en_indices, strict=True)):
            failed = _check_similarity(cells[de_i], cells[en_i])
            if (
                failed
                and canonicalize_start_completed
                and _is_start_completed_pair(cells[de_i], cells[en_i])
            ):
                # Structural start/completed pairing is authoritative for
                # these cells; ignore content-similarity failures (e.g.
                # localized function names) and force the canonical
                # interleave.
                failed = []
            if failed and (cells[de_i].line_number, cells[en_i].line_number) in confirmed_pairings:
                # The agent reviewed the worklist and confirmed this DE/EN positional
                # pairing is correct despite the divergence (#236) — bypass the gate and
                # reorder it into adjacency, exactly as a confident pair.
                failed = []
            if failed:
                if en_i == de_i + 1 and set(failed) <= _SOFT_SIMILARITY_CHECKS:
                    # Already adjacent (no reorder needed) and only a localization-expected
                    # divergence — correctly interleaved, not a structural error → do not
                    # flag (#236 convergence). Left in place; not added to confident_pairs
                    # (it does not move). A structural mismatch still falls through to flag.
                    continue
                review_items.append(
                    ReviewItem(
                        file=file_path,
                        issue="similarity_failure",
                        suggestion=_similarity_suggestion(cells[de_i], cells[en_i], failed),
                        # The agent pairing worklist (#236): the failed positional pair with
                        # the FULL DE/EN bodies + a similarity score, so an agent can confirm
                        # the pairing (a localized-but-correct twin) or spot a mis-pairing —
                        # not just the truncated previews.
                        details={
                            "pair_index": pair_idx,
                            "category": cat,
                            "similarity_score": _similarity_score(cells[de_i], cells[en_i], failed),
                            "de_cell": {
                                "line": cells[de_i].line_number,
                                "tags": list(cells[de_i].metadata.tags),
                                "preview": _cell_preview(cells[de_i]),
                                "body": _cell_body(cells[de_i]),
                            },
                            "en_cell": {
                                "line": cells[en_i].line_number,
                                "tags": list(cells[en_i].metadata.tags),
                                "preview": _cell_preview(cells[en_i]),
                                "body": _cell_body(cells[en_i]),
                            },
                            "failed_checks": failed,
                        },
                    )
                )
            else:
                confident_pairs[de_i] = en_i

    # If no confident pairs, nothing to reorder
    confident_en = set(confident_pairs.values())
    if not confident_en:
        return cells, changes, review_items

    # Build reordered cell list:
    # Walk original order, skip confident EN cells (they get inserted
    # right after their DE partner).
    reordered: list[_RawCell] = []
    for idx, cell in enumerate(cells):
        if idx in confident_en:
            continue
        reordered.append(cell)
        if idx in confident_pairs:
            reordered.append(cells[confident_pairs[idx]])

    # Check whether the order actually changed
    if [id(c) for c in reordered] == [id(c) for c in cells]:
        return cells, changes, review_items

    # Count how many EN cells actually moved
    moved = sum(1 for de_i, en_i in confident_pairs.items() if en_i != de_i + 1)
    if moved:
        changes.append(
            Change(
                file=file_path,
                operation="interleaving",
                line=1,
                description=f"Reordered {moved} DE/EN pair(s) to be adjacent",
            )
        )

    return reordered, changes, review_items


# ---------------------------------------------------------------------------
# Similarity checks (Tier 2)
# ---------------------------------------------------------------------------


def _check_similarity(de_cell: _RawCell, en_cell: _RawCell) -> list[str]:
    """Verify structural similarity between a DE/EN pair.

    Returns a list of failed check names (empty = similar).
    """
    failed: list[str] = []
    de_meta = de_cell.metadata
    en_meta = en_cell.metadata

    # Tags check (ignoring order)
    if set(de_meta.tags) != set(en_meta.tags):
        failed.append("tags")

    # Line count check (within ±50%)
    de_lines = sum(1 for ln in de_cell.lines[1:] if ln.strip())
    en_lines = sum(1 for ln in en_cell.lines[1:] if ln.strip())
    if de_lines > 0 and en_lines > 0:
        ratio = min(de_lines, en_lines) / max(de_lines, en_lines)
        if ratio < 0.5:
            failed.append("line_count")
    elif (de_lines == 0) != (en_lines == 0) and max(de_lines, en_lines) > 2:
        failed.append("line_count")

    if de_meta.cell_type == "markdown" and en_meta.cell_type == "markdown":
        # Heading level check
        de_level = _heading_level(de_cell)
        en_level = _heading_level(en_cell)
        if de_level != en_level and (de_level > 0 or en_level > 0):
            failed.append("heading_level")

        # Bullet count check (within ±2)
        de_bullets = _bullet_count(de_cell)
        en_bullets = _bullet_count(en_cell)
        if abs(de_bullets - en_bullets) > 2:
            failed.append("bullet_count")

    if de_meta.cell_type == "code" and en_meta.cell_type == "code":
        de_names = _code_names(de_cell)
        en_names = _code_names(en_cell)
        if de_names != en_names and (de_names or en_names):
            failed.append("code_structure")

    return failed


def _heading_level(cell: _RawCell) -> int:
    """Extract the markdown heading level (0 = no heading)."""
    for line in cell.lines[1:]:
        # Strip the comment prefix (either comment family).
        if line.startswith("# "):
            inner = line[2:]
        elif line.startswith("// "):
            inner = line[3:]
        elif line.startswith("#"):
            inner = line[1:]
        elif line.startswith("//"):
            inner = line[2:]
        else:
            continue
        inner = inner.lstrip()
        if inner.startswith("#"):
            level = 0
            for ch in inner:
                if ch == "#":
                    level += 1
                else:
                    break
            return level
    return 0


def _bullet_count(cell: _RawCell) -> int:
    """Count top-level list items in a markdown cell."""
    count = 0
    for line in cell.lines[1:]:
        if line.startswith(("# - ", "# * ", "// - ", "// * ")):
            count += 1
        elif re.match(r"^(?:#|//) \d+\.\s", line):
            count += 1
    return count


_DEF_RE = re.compile(r"^(?:def|class)\s+(\w+)")


def _code_names(cell: _RawCell) -> frozenset[str]:
    """Extract function/class names defined in a code cell."""
    names: set[str] = set()
    for line in cell.lines[1:]:
        m = _DEF_RE.match(line.strip())
        if m:
            names.add(m.group(1))
    return frozenset(names)


def _cell_preview(cell: _RawCell, max_len: int = 60) -> str:
    """Return a brief preview of a cell's content."""
    for line in cell.lines[1:]:
        text = line.strip()
        if text:
            if len(text) > max_len:
                return text[:max_len] + "..."
            return text
    return ""


def _cell_body(cell: _RawCell) -> str:
    """The cell's full content — the lines after its ``# %%`` header, trailing blanks trimmed.

    The ``similarity_failure`` worklist (#236) carries this so an agent has the *whole* DE
    and EN cell to judge the pairing by, not just the truncated :func:`_cell_preview`.
    """
    return "\n".join(cell.lines[1:]).rstrip("\n")


def _applicable_check_count(de_cell: _RawCell, en_cell: _RawCell) -> int:
    """How many similarity checks could fire for a DE/EN pair, given their cell types.

    Tags + line-count always apply; heading-level + bullet-count are markdown-only;
    code-structure is code-only — so the denominator of :func:`_similarity_score` reflects
    only the checks that were actually possible for this pair.
    """
    n = 2  # tags + line_count
    if de_cell.metadata.cell_type == "markdown" and en_cell.metadata.cell_type == "markdown":
        n += 2  # heading_level + bullet_count
    if de_cell.metadata.cell_type == "code" and en_cell.metadata.cell_type == "code":
        n += 1  # code_structure
    return n


def _similarity_score(de_cell: _RawCell, en_cell: _RawCell, failed: list[str]) -> float:
    """A 0.0–1.0 structural-similarity score (the fraction of applicable checks that passed).

    The ``similarity_failure`` worklist (#236) surfaces this so an agent can rank how likely
    a flagged positional pair is still the *correct* twin (a high score — a single localized
    divergence) versus a genuine mis-pairing (a low score).
    """
    applicable = _applicable_check_count(de_cell, en_cell)
    if applicable == 0:
        return 0.0
    return round(1.0 - len(failed) / applicable, 2)


def _similarity_suggestion(de_cell: _RawCell, en_cell: _RawCell, failed: list[str]) -> str:
    """Generate a human-readable suggestion for a similarity failure."""
    parts: list[str] = []
    if "tags" in failed:
        parts.append(
            f"Tags differ: DE has {sorted(de_cell.metadata.tags)} "
            f"but EN has {sorted(en_cell.metadata.tags)}"
        )
    if "heading_level" in failed:
        parts.append("Heading levels differ")
    if "bullet_count" in failed:
        parts.append("Bullet counts differ significantly")
    if "code_structure" in failed:
        parts.append("Different function/class definitions")
    if "line_count" in failed:
        parts.append("Line counts differ by more than 50%")
    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Slide ID auto-generation — delegates to clm.slides.assign_ids
# ---------------------------------------------------------------------------


def _apply_slide_ids(
    cells: list[_RawCell],
    path: Path,
    options: AssignOptions | None = None,
    *,
    twin_ids: list[str | None] | None = None,
) -> tuple[list[Change], list[ReviewItem]]:
    """Assign ``slide_id`` metadata using the shared assign-ids engine.

    This is a thin adapter over :func:`clm.slides.assign_ids.assign_ids_for_cells`
    so ``clm slides normalize`` produces ids by the same rules as the
    dedicated ``clm slides assign-ids`` command — EN-derived kebab slugs,
    German transliteration, narrative inheritance, ``!``-preserve marker
    — instead of the older naive slug-and-fallback scheme.

    The engine mutates ``cells`` in place (assignments are written into
    the cell headers). We translate its ``AssignedId`` records into the
    normalizer's :class:`Change` records and its ``Refusal`` records
    into :class:`ReviewItem` records.

    ``twin_ids`` is forwarded verbatim to
    :func:`~clm.slides.assign_ids.assign_ids_for_cells` (the #162 defensive):
    when supplied, an id-less slide adopts the positionally-corresponding
    sibling-half id instead of minting a divergent slug.
    """
    from clm.slides.assign_ids import AssignOptions as _AO
    from clm.slides.assign_ids import assign_ids_for_cells

    if options is None:
        options = _AO()

    result = assign_ids_for_cells(cells, path, options, twin_ids=twin_ids)

    changes = [
        Change(
            file=a.file,
            operation="slide_ids",
            line=a.line,
            description=f'Added slide_id="{a.slide_id}" (source={a.source})',
        )
        for a in result.assignments
    ]

    review_items: list[ReviewItem] = []
    for r in result.refusals:
        details: dict = {"severity": r.severity, "line": r.line}
        if r.proposed_slug:
            details["proposed_slug"] = r.proposed_slug
        if r.proposed_title:
            details["proposed_title"] = r.proposed_title
        review_items.append(
            ReviewItem(
                file=r.file,
                issue=f"slide_id_{r.severity}_refusal",
                suggestion=r.reason,
                details=details,
            )
        )

    return changes, review_items


# ---------------------------------------------------------------------------
# Cell spacing: blank line between cells + markdown leading blank comment
# ---------------------------------------------------------------------------


def _apply_cell_spacing(
    cells: list[_RawCell], file_path: str, comment_token: str = "#"
) -> list[Change]:
    """Normalize inter-cell and intra-cell whitespace (two mechanical fixes).

    The counterparts of the validator's ``cell-separation`` and
    ``markdown-blank-lead`` warnings:

    1. A non-j2 cell must be preceded by a blank line — represented as a trailing
       empty body line on the *previous* cell. j2 cells (the tight-coupled title
       header block ``# j2 import`` → ``# {{ header }}``) are exempt.
    2. A non-j2 markdown cell's body must start with a blank comment line (``#``),
       so content that opens with a bullet or heading renders correctly.

    Mutates ``cells`` in place. Idempotent: a conforming deck yields no changes.
    """
    changes: list[Change] = []

    # Fix 1 — markdown leading blank comment. Done first; it only touches the
    # FRONT of a cell's body, so it never interferes with the trailing-blank pass.
    for cell in cells:
        meta = cell.metadata
        if meta.is_j2 or meta.cell_type != "markdown":
            continue
        body = cell.lines[1:]
        if not body:
            continue
        first = body[0]
        if first.strip() == comment_token:
            continue  # already a blank comment
        if first.strip() == "":
            # A bare blank line where the comment belongs — promote it.
            cell.lines[1] = comment_token
        else:
            cell.lines.insert(1, comment_token)
        changes.append(
            Change(
                file=file_path,
                operation="cell_spacing",
                line=cell.line_number,
                description=f"Added leading blank comment line (`{comment_token}`) to markdown cell",
            )
        )

    # Fix 2 — blank-line separation before each non-j2 cell.
    for prev, cur in zip(cells, cells[1:], strict=False):
        if cur.metadata.is_j2:
            continue
        if len(prev.lines) > 1 and prev.lines[-1].strip() == "":
            continue  # already separated
        prev.lines.append("")
        changes.append(
            Change(
                file=file_path,
                operation="cell_spacing",
                line=cur.line_number,
                description="Added blank line before cell",
            )
        )

    return changes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_file(
    path: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
    confirmed_pairings: set[tuple[int, int]] | None = None,
) -> NormalizationResult:
    """Normalize a single slide file.

    Args:
        path: Path to the ``.py`` slide file.
        operations: Which operations to apply.  ``None`` means all.
        dry_run: If ``True``, preview changes without modifying files.
        assign_options: Options forwarded to the shared assign-ids engine
            for the ``slide_ids`` operation (``--force``,
            ``--accept-content-derived``, LLM suggester, …). ``None``
            uses defaults — refuse headingless slides, never overwrite
            existing ids, no LLM.
        canonicalize_start_completed: Forwarded to the interleaving pass;
            forces ``start``/``completed`` cohesion pairs into the canonical
            interleave so a subsequent ``split``/``unify`` round-trips
            byte-for-byte. No effect unless ``interleaving`` runs.
        confirmed_pairings: ``{(de_line, en_line)}`` the agent confirmed from the
            ``similarity_failure`` worklist (#236); each bypasses the interleaving
            similarity gate and is reordered into adjacency. Per-file (keyed by line),
            so it is only meaningful for a single-file run. No effect unless
            ``interleaving`` runs.

    Returns:
        A :class:`NormalizationResult` with changes and review items.
    """
    op_set = set(operations) if operations else set(ALL_OPERATIONS)
    if "all" in op_set:
        op_set = set(ALL_OPERATIONS)

    file_str = str(path)
    comment_token = comment_token_for_path(path)
    text = path.read_text(encoding="utf-8")
    preamble, cells = _split_raw_cells(text, comment_token)

    all_changes: list[Change] = []
    all_review: list[ReviewItem] = []

    # Apply operations in deterministic order. Preamble-code wrapping runs first
    # so every later pass (interleaving, slide_ids, cell_spacing) sees the
    # canonical cell structure with the code in its own cell.
    if "preamble_code" in op_set:
        all_changes.extend(_apply_preamble_code(cells, file_str, comment_token))

    # Placeholder-start demotion must precede tag migration: once the bogus
    # 'start' tag is gone, the migration no longer promotes the adjacent
    # markdown 'alt' to 'completed' (#233 item 4a).
    if "placeholder_start" in op_set:
        all_changes.extend(_apply_placeholder_start(cells, file_str))

    if "tag_migration" in op_set:
        all_changes.extend(_apply_tag_migration(cells, file_str))

    if "workshop_tags" in op_set:
        all_changes.extend(_apply_workshop_tags(cells, file_str))
        all_changes.extend(_apply_workshop_symmetry(cells, file_str))

    if "interleaving" in op_set:
        cells, interleave_changes, interleave_reviews = _apply_interleaving(
            cells,
            file_str,
            canonicalize_start_completed=canonicalize_start_completed,
            confirmed_pairings=confirmed_pairings,
        )
        all_changes.extend(interleave_changes)
        all_review.extend(interleave_reviews)

    if "slide_ids" in op_set:
        slide_id_changes, slide_id_reviews = _apply_slide_ids(cells, path, assign_options)
        all_changes.extend(slide_id_changes)
        all_review.extend(slide_id_reviews)

    # Cell spacing runs last, on the final cell order (after any interleaving
    # reorder), so blank-line separation reflects the cells' final adjacency.
    if "cell_spacing" in op_set:
        all_changes.extend(_apply_cell_spacing(cells, file_str, comment_token))

    modified = False
    if all_changes and not dry_run:
        new_text = _reconstruct(preamble, cells)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            modified = True

    return NormalizationResult(
        files_modified=1 if modified else 0,
        changes=all_changes,
        review_items=all_review,
    )


def normalize_directory(
    path: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize all slide files in a directory (recursive)."""
    return normalize_files(
        find_slide_files_recursive(path),
        operations=operations,
        dry_run=dry_run,
        assign_options=assign_options,
        canonicalize_start_completed=canonicalize_start_completed,
    )


def normalize_files(
    slide_files: list[Path],
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize an explicit list of slide files.

    Factored out of :func:`normalize_directory` so a caller that has already
    selected a subset of decks (``clm slides normalize --only`` / ``--exclude``
    / ``--shipping-only``) normalizes exactly that set without a second walk.
    """
    combined = NormalizationResult()

    for sf in slide_files:
        result = normalize_file(
            sf,
            operations=operations,
            dry_run=dry_run,
            assign_options=assign_options,
            canonicalize_start_completed=canonicalize_start_completed,
        )
        combined.files_modified += result.files_modified
        combined.changes.extend(result.changes)
        combined.review_items.extend(result.review_items)

    return combined


def normalize_course(
    course_spec_path: Path,
    slides_dir: Path,
    *,
    operations: list[str] | None = None,
    dry_run: bool = False,
    assign_options: AssignOptions | None = None,
    canonicalize_start_completed: bool = False,
) -> NormalizationResult:
    """Normalize all slides referenced by a course spec."""
    from clm.core.course_spec import CourseSpec

    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)
    combined = NormalizationResult()

    for binding in spec.iter_topic_bindings():
        matches = matches_for_binding(topic_map, binding.topic_id, binding.effective_module)
        for match in matches:
            slide_files = find_slide_files(match.path)
            for sf in slide_files:
                result = normalize_file(
                    sf,
                    operations=operations,
                    dry_run=dry_run,
                    assign_options=assign_options,
                    canonicalize_start_completed=canonicalize_start_completed,
                )
                combined.files_modified += result.files_modified
                combined.changes.extend(result.changes)
                combined.review_items.extend(result.review_items)

    return combined
