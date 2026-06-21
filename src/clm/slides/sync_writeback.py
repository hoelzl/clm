"""Shared cell-preserving write infrastructure for the sync apply path.

Used by :mod:`clm.slides.sync_apply` — the Issue #166 authoring apply
engine — which drives ``find_cell`` / ``replace_cell_body`` /
``delete_cell``, keyed by ``(slide_id, role)`` rather than line number.

These paths must keep cell headers and trailing-blank padding verbatim so
the surrounding bytes never shift; the Phase 5 round-trip invariant is
what makes `clm slides split` / `unify` work, and the sync write paths
inherit that contract.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from clm.notebooks.slide_parser import CellMetadata, comment_token_for_path, parse_cell_header
from clm.slides.code_cell_extract import extract_from_code
from clm.slides.raw_cells import RawCell, reconstruct, split_cells
from clm.slides.slug import slugify

__all__ = [
    "CODE_ROLE",
    "FileState",
    "anchor_of",
    "build_cell",
    "build_twin_cell",
    "cell_content_hash",
    "construct_of",
    "role_of",
    "row_anchor",
    "set_header_tags",
    "swap_lang",
]

# Markdown tags that name a narrative sync role. Duplicated from
# ``clm.slides.sync_plan`` to keep this low-level write module free of an
# import cycle (sync_plan imports this module).
_SYNC_ROLE_TAGS = {"slide", "subslide", "voiceover", "notes"}

# The synthetic role for a localized (``lang=``) code cell that also carries a
# ``slide_id``: it has a stable cross-language identity, so it is reconciled
# per-cell like a narrative cell (its body translated rather than judged — see
# ``sync_apply._apply_edit``). Language-neutral or id-less code is handled
# structurally by :mod:`clm.slides.sync_code`, not through a role.
CODE_ROLE = "code"


def role_of(metadata: CellMetadata) -> str | None:
    """Return the per-cell sync role of a cell from its metadata, or ``None``.

    The cells reconciled **per (slide_id, role)** by the Issue #166 engine:

    - narrative markdown tagged ``slide`` / ``subslide`` / ``voiceover`` /
      ``notes`` → that tag;
    - auxiliary markdown carrying a ``slide_id`` but **no** narrative tag (an
      ``alt`` solution note, or an untagged explanatory cell) → its first tag,
      else ``"markdown"`` — so it too has a stable per-cell identity;
    - a **localized** code cell (has both ``lang`` and ``slide_id``) →
      :data:`CODE_ROLE`.

    Everything else (j2 headers, language-neutral code, id-less code) returns
    ``None``: it is not reconciled per-cell. Language-neutral / id-less code is
    propagated structurally by :mod:`clm.slides.sync_code`. Public so
    :mod:`clm.slides.sync_apply` and :mod:`clm.slides.sync_plan` reuse the exact
    same predicate instead of keeping divergent copies.
    """
    if metadata.is_j2:
        return None
    if metadata.cell_type == "code":
        # A localized id'd code cell is twinned per-cell; bare/id-less code is
        # structural (handled by sync_code), so it has no per-cell role.
        if metadata.lang is not None and metadata.slide_id is not None:
            return CODE_ROLE
        return None
    # markdown: a narrative tag wins; otherwise an id-carrying aux cell still
    # syncs under a per-cell role derived from its (non-narrative) tag.
    for tag in metadata.tags:
        if tag in _SYNC_ROLE_TAGS:
            return tag
    if metadata.slide_id is not None:
        return metadata.tags[0] if metadata.tags else "markdown"
    return None


def _cell_matches(cell: RawCell, slide_id: str, role: str) -> bool:
    """Whether ``cell`` carries ``slide_id`` in sync ``role``."""
    return cell.metadata.slide_id == slide_id and role_of(cell.metadata) == role


def _trailing_blanks(cell: RawCell) -> int:
    """Count the blank body lines at the end of ``cell``."""
    n = 0
    for line in reversed(cell.lines[1:]):
        if line == "":
            n += 1
        else:
            break
    return n


def _set_trailing_blanks(cell: RawCell, n: int) -> None:
    """Force ``cell`` to end with exactly ``n`` blank body lines."""
    body = cell.lines[1:]
    while body and body[-1] == "":
        body.pop()
    body.extend([""] * n)
    cell.lines = [cell.lines[0], *body]


def cell_content_hash(text: str) -> str:
    """Hash ``text`` the way ``Cell.content`` is hashed.

    ``Cell.content`` operates on the body as the parser produces it: body
    lines joined by ``\\n`` then ``.strip()``-ed. Apply-time writes carry
    whatever the LLM proposed (or the user edited), which may have extra
    leading/trailing whitespace — strip the same way before hashing so
    re-runs find a matching cache row.
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def construct_of(metadata: CellMetadata, body: str) -> str | None:
    """The deterministic *construct* component of a cell's content anchor.

    For a code cell, the AST construct name from :func:`extract_from_code`
    slugified — ``"function my_fun"`` → ``"function-my-fun"``, ``"class X"`` →
    ``"class-x"``, ``"import time"`` → ``"import-time"``. ``None`` for non-code,
    j2, or unparsable cells (shell escapes, magic, half-finished stubs) — the
    anchor then falls back to the slide_id or the content hash. A pure function
    of content, so it is always re-derivable and adds no header churn
    (Issue #190 §4). ``body`` is the cell body as the parser yields it (a code
    cell's raw Python, no ``# `` prefix).
    """
    if metadata.is_j2 or metadata.cell_type != "code":
        return None
    extraction = extract_from_code(body)
    if extraction is None:
        return None
    return slugify(extraction.text) or None


def anchor_of(metadata: CellMetadata, body: str) -> str:
    """The content-derived identity of a cell: ``hand slide_id > construct > hash``.

    The Issue #190 §4 anchor — stable across translations and immune to the git
    commit cadence, and **never written into the file**. The three components are
    prefixed (``id:`` / ``construct:`` / ``hash:``) so a hand id ``"foo"`` and a
    construct slug ``"foo"`` can never collide. The hash branch reuses
    :func:`cell_content_hash`, so it hashes the *same* canonical stripped form as
    the watermark's ``content_hash`` (else CRLF/LF drift would re-introduce the
    item-3 churn this anchor exists to remove).
    """
    if metadata.slide_id is not None:
        return f"id:{metadata.slide_id}"
    construct = construct_of(metadata, body)
    if construct is not None:
        return f"construct:{construct}"
    return f"hash:{cell_content_hash(body)}"


def row_anchor(slide_id: str | None, construct: str | None, content_hash: str) -> str:
    """The content anchor of a stored watermark row — the row-side :func:`anchor_of`.

    Derives the same ``id: > construct: > hash:`` identity from a persisted row
    that :func:`anchor_of` derives from a live cell, so the anchor passes can match
    a current cell against its baseline. The two must stay in lockstep.
    """
    if slide_id is not None:
        return f"id:{slide_id}"
    if construct is not None:
        return f"construct:{construct}"
    return f"hash:{content_hash}"


_LANG_ATTR_RE = re.compile(r'lang="[^"]*"')
_TAGS_ATTR_RE = re.compile(r"tags=\[[^\]]*\]")


def set_header_tags(header: str, tags: Sequence[str]) -> str:
    """Return ``header`` with its ``tags=[…]`` set to exactly ``tags``.

    Replaces an existing ``tags=[…]`` block in place (keeping its position
    relative to ``lang=`` / ``slide_id=``), inserts one at the end when absent,
    or drops the block entirely when ``tags`` is empty. Used to mirror a tag set
    onto a target cell during a ``retag`` apply (Issue #198) without disturbing
    the rest of the header (slide_id, lang, markdown-vs-code) or the body. Tag
    order is preserved from ``tags`` (the source cell's order), matching the
    ``tags=["a", "b"]`` serialization the normalizer emits.
    """
    block = ("tags=[" + ", ".join(f'"{t}"' for t in tags) + "]") if tags else ""
    match = _TAGS_ATTR_RE.search(header)
    if match:
        if block:
            return header[: match.start()] + block + header[match.end() :]
        # Removing the block can leave a doubled space; tidy it.
        stripped = header[: match.start()] + header[match.end() :]
        return re.sub(r"  +", " ", stripped).rstrip()
    if not block:
        return header
    return header.rstrip() + " " + block


def swap_lang(header: str, lang: str) -> str:
    """Return ``header`` with its ``lang="…"`` set to ``lang`` (inserted if absent).

    Used to build a target-language twin of a source cell while keeping its
    slide_id, tags, and markdown-vs-code cell type verbatim.
    """
    if _LANG_ATTR_RE.search(header):
        return _LANG_ATTR_RE.sub(f'lang="{lang}"', header)
    if "[markdown]" in header:
        return header.replace("[markdown]", f'[markdown] lang="{lang}"', 1)
    # bare "<token> %%" code-cell header — insert lang after the %% marker for
    # either comment family.
    marker = "// %%" if header.startswith("// %%") else "# %%"
    return header.replace(marker, f'{marker} lang="{lang}"', 1)


def build_cell(
    comment_token: str,
    *,
    cell_type: str,
    lang: str | None,
    tags: Sequence[str],
    slide_id: str,
    body: str,
) -> RawCell:
    """Build a brand-new cell from scratch (the P2 structural-insert factory).

    Unlike :func:`build_twin_cell`, which clones an existing header, this mints a
    header in the canonical attribute order the normalizer emits
    (``[markdown] lang=… tags=[…] slide_id=…``) for a fresh cell that has no
    source to copy from. ``comment_token`` is the deck's line-comment token
    (``"#"`` / ``"//"``) — the percent marker is ``"<token> %%"``. ``body`` is
    used bare (leading/trailing blank lines stripped); the caller's insert
    primitive grants the deck's separator based on final position.
    """
    marker = f"{comment_token} %%"
    parts = [marker]
    if cell_type == "markdown":
        parts.append("[markdown]")
    if lang is not None:
        parts.append(f'lang="{lang}"')
    if tags:
        parts.append("tags=[" + ", ".join(f'"{t}"' for t in tags) + "]")
    parts.append(f'slide_id="{slide_id}"')
    header = " ".join(parts)
    body_lines = body.split("\n")
    while body_lines and body_lines[0] == "":
        body_lines.pop(0)
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return RawCell(
        lines=[header, *body_lines],
        line_number=0,
        metadata=parse_cell_header(header, comment_token),
    )


def build_twin_cell(source_cell: RawCell, target_lang: str, target_body: str) -> RawCell:
    """Build the target-language twin of ``source_cell``.

    Preserves the source header verbatim except for the language attribute (so
    slide_id, tags, and the markdown-vs-code cell type carry over), and uses the
    translated ``target_body`` bare (no leading/trailing blank lines) — the
    caller's insert primitive grants the deck's separator based on final
    position.
    """
    header = swap_lang(source_cell.lines[0], target_lang)
    body_lines = target_body.split("\n")
    while body_lines and body_lines[0] == "":
        body_lines.pop(0)
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
    return RawCell(lines=[header, *body_lines], line_number=0, metadata=parse_cell_header(header))


@dataclass
class FileState:
    """In-memory representation of one slide file, ready for batched writes.

    Loaded once per path; ``replace_cell_body`` mutates the matching cell
    in place; ``flush`` writes back via :func:`raw_cells.reconstruct`
    iff anything changed. Multiple writes against the same path share
    one ``FileState`` so they round-trip through a single read+write.
    """

    path: Path
    preamble: str
    cells: list[RawCell]
    dirty: bool = False
    ends_with_newline: bool = True

    @classmethod
    def load(cls, path: Path) -> FileState:
        text = path.read_text(encoding="utf-8")
        preamble, cells = split_cells(text, comment_token_for_path(path))
        return cls(
            path=path,
            preamble=preamble,
            cells=cells,
            ends_with_newline=text.endswith("\n"),
        )

    def find_cell(self, slide_id: str, role: str) -> RawCell | None:
        """Return the cell carrying ``slide_id`` in ``role``, or ``None``.

        Used by the Issue #166 apply engine, whose proposals are keyed by
        ``(slide_id, role)`` rather than by line number — so deletes and
        edits stay correct even as earlier operations shift line numbers.
        """
        for cell in self.cells:
            if _cell_matches(cell, slide_id, role):
                return cell
        return None

    def replace_cell_body(self, slide_id: str, role: str, new_text: str) -> bool:
        """Rewrite the body of the ``(slide_id, role)`` cell in place.

        Returns ``False`` when no such cell exists. Header and trailing
        blank-line padding stay verbatim.
        """
        cell = self.find_cell(slide_id, role)
        if cell is None:
            return False
        self._rewrite_cell_body(cell, new_text)
        self.dirty = True
        return True

    def replace_cell_tags(self, slide_id: str, role: str, new_tags: Sequence[str]) -> bool:
        """Set the ``(slide_id, role)`` cell's tag set to ``new_tags`` in place.

        Rewrites only the header's ``tags=[…]`` (Issue #198 tag mirroring),
        keeping slide_id, lang, cell-type, body, and trailing blanks verbatim —
        the header-only counterpart of :meth:`replace_cell_body`. Returns
        ``False`` when no such cell exists; ``True`` (a no-op, not dirtied) when
        the header already carries exactly ``new_tags``. Because a ``retag`` only
        ever targets a cell matched by the same ``(slide_id, role)`` key, the
        role-defining tag is part of ``new_tags`` (or the role is tag-independent,
        as for a localized code cell), so the cell's role never changes.
        """
        cell = self.find_cell(slide_id, role)
        if cell is None:
            return False
        new_header = set_header_tags(cell.lines[0], new_tags)
        if new_header != cell.lines[0]:
            cell.lines[0] = new_header
            cell.metadata = parse_cell_header(new_header)
            self.dirty = True
        return True

    def replace_idless_localized_tags(
        self, lang: str, position: int, new_tags: Sequence[str]
    ) -> bool:
        """Set the tags of the ``position``-th ``lang`` cell, if it is id-less localized.

        Tier C of Issue #198: an id-less localized cell (a ``lang=`` cell with no
        ``slide_id``, so :func:`role_of` is ``None``) has no ``(slide_id, role)``
        key, so :meth:`replace_cell_tags` cannot find it. The classifier instead
        identifies it by its **position** among the deck's non-j2 ``lang`` cells —
        the same per-language ordinal the watermark records — and this method
        rewrites only that cell's ``tags=[…]`` (body, ``lang``, cell type, trailing
        blanks verbatim), exactly like :meth:`replace_cell_tags`.

        Returns ``False`` (and dirties nothing) when ``position`` is out of range
        **or** the cell there is *not* id-less localized — a mismatch means the
        stream drifted since the plan was built, so the caller surfaces it as an
        error rather than retagging the wrong cell. ``True`` when the cell is
        found (a no-op if its tags already match ``new_tags``).
        """
        idx = -1
        for cell in self.cells:
            meta = cell.metadata
            if meta.is_j2 or meta.lang != lang:
                continue
            idx += 1
            if idx != position:
                continue
            if role_of(meta) is not None:
                return False  # an id-carrying cell sits here — stream drifted; refuse
            new_header = set_header_tags(cell.lines[0], new_tags)
            if new_header != cell.lines[0]:
                cell.lines[0] = new_header
                cell.metadata = parse_cell_header(new_header)
                self.dirty = True
            return True
        return False

    def delete_cell(self, slide_id: str, role: str) -> bool:
        """Remove the ``(slide_id, role)`` cell, lines and all.

        Returns ``False`` when no such cell exists. The cell owns its
        boundary line, body, and trailing blanks, so dropping it from the
        list leaves the surrounding cells' bytes untouched.
        """
        for i, cell in enumerate(self.cells):
            if _cell_matches(cell, slide_id, role):
                del self.cells[i]
                self.dirty = True
                return True
        return False

    def replace_cell_body_obj(self, target: RawCell, new_text: str) -> bool:
        """Rewrite a specific cell's body in place (located by object identity).

        Issue #403 Phase B: a narrative cell is keyed by its positional *anchor*,
        not ``(slide_id, role)``, so the caller resolves the exact cell instance and
        hands it here. Header and trailing blank padding stay verbatim. Returns
        ``False`` when the cell is no longer in the deck (it shifted/was removed).
        """
        for cell in self.cells:
            if cell is target:
                self._rewrite_cell_body(cell, new_text)
                self.dirty = True
                return True
        return False

    def delete_cell_obj(self, target: RawCell) -> bool:
        """Remove a specific cell (located by object identity), lines and all.

        The anchor-keyed (Issue #403) counterpart of :meth:`delete_cell`. Returns
        ``False`` when the cell is not present.
        """
        for i, cell in enumerate(self.cells):
            if cell is target:
                del self.cells[i]
                self.dirty = True
                return True
        return False

    def separator_blanks(self) -> int:
        """The deck's inter-cell blank-line gap (0 = tight, 1 = blank-separated).

        The **most common** trailing-blank count among the non-last cells, with
        j2 header cells excluded. The first cell is often a ``# j2`` header that
        sits tight against its sibling macro call (gap 0) even though the rest of
        the deck is blank-separated (gap 1); reading only ``cells[0]`` then
        mis-reports the convention as tight. The last cell is excluded because it
        carries the terminal-newline artifact, not a separator. Compute **before**
        a structural mutation. A genuinely non-uniform deck falls back to its
        dominant gap (documented limitation).
        """
        if len(self.cells) < 2:
            return 0
        from collections import Counter

        counts = [_trailing_blanks(c) for c in self.cells[:-1] if not c.metadata.is_j2]
        if not counts:
            counts = [_trailing_blanks(self.cells[0])]
        return Counter(counts).most_common(1)[0][0]

    def insert_after(self, slide_id: str, role: str, new_cell: RawCell) -> bool:
        """Insert ``new_cell`` immediately after the ``(slide_id, role)`` cell.

        Returns ``False`` when the anchor cell is absent. Used by the Issue
        #166 add path to place a translated counterpart next to its neighbour.
        """
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        for i, cell in enumerate(self.cells):
            if _cell_matches(cell, slide_id, role):
                self.cells.insert(i + 1, new_cell)
                self.dirty = True
                self._place_inserted(new_cell, original_last, sep)
                return True
        return False

    def insert_after_cell(self, anchor_cell: RawCell, new_cell: RawCell) -> bool:
        """Insert ``new_cell`` immediately after ``anchor_cell`` (matched by identity).

        Unlike :meth:`insert_after` (keyed by ``(slide_id, role)``), this places a
        cell after an arbitrary, possibly id-less anchor — the primitive behind the
        Issue #403 positional narrative placement, where a voiceover lands after its
        resolved predecessor content cell (a code cell, an id-less markdown cell, …)
        rather than after a keyed slide. Returns ``False`` when ``anchor_cell`` is
        not in this deck.
        """
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        for i, cell in enumerate(self.cells):
            if cell is anchor_cell:
                self.cells.insert(i + 1, new_cell)
                self.dirty = True
                self._place_inserted(new_cell, original_last, sep)
                return True
        return False

    def insert_before_first_sync_cell(self, new_cell: RawCell) -> None:
        """Insert ``new_cell`` ahead of the first sync cell (after the head).

        The anchor for an add with no preceding shared cell — it becomes the
        deck's first slide, sitting after any j2 header / intro cells but
        before the existing slides/narrative. Appends only when the deck has no
        sync cell at all.
        """
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        for i, cell in enumerate(self.cells):
            if role_of(cell.metadata) is not None:
                self.cells.insert(i, new_cell)
                self.dirty = True
                self._place_inserted(new_cell, original_last, sep)
                return
        self.cells.append(new_cell)
        self.dirty = True
        self._place_inserted(new_cell, original_last, sep)

    def move_cell(self, slide_id: str, role: str, direction: str) -> bool:
        """Swap the ``(slide_id, role)`` cell with its neighbour (``"up"``/``"down"``).

        The reorder primitive behind the Studio P2 up/down chevrons. Returns
        ``False`` when the cell is absent or already at the requested boundary.
        Both swapped cells keep their bytes verbatim — only their order changes —
        so untouched cells stay byte-for-byte identical. When the swap moves a
        cell into or out of the last position, the terminal-newline artifact
        ``split_cells`` parked on the last cell is normalised exactly as for an
        insert (cell now last → no explicit trailing blank, restored by
        :meth:`flush`; the displaced cell → the deck separator).
        """
        idx = next((i for i, c in enumerate(self.cells) if _cell_matches(c, slide_id, role)), None)
        if idx is None:
            return False
        target = idx - 1 if direction == "up" else idx + 1
        if target < 0 or target >= len(self.cells):
            return False
        sep = self.separator_blanks()
        original_last = self.cells[-1] if self.cells else None
        self.cells[idx], self.cells[target] = self.cells[target], self.cells[idx]
        self.dirty = True
        if self.cells and self.cells[-1] is not original_last:
            _set_trailing_blanks(self.cells[-1], 0)
            if original_last is not None:
                self.normalize_displaced_last(original_last, sep)
        return True

    def _place_inserted(self, new_cell: RawCell, original_last: RawCell | None, sep: int) -> None:
        """Give ``new_cell`` the deck's separator (or none, if it is now last).

        When ``new_cell`` lands last, it carries no explicit trailing blank —
        :meth:`flush` restores the terminal newline — and the cell it displaced
        from the end is normalised to the separator (its terminal artifact is
        not a real gap).
        """
        if self.cells and self.cells[-1] is new_cell:
            _set_trailing_blanks(new_cell, 0)
            if original_last is not None:
                self.normalize_displaced_last(original_last, sep)
        else:
            _set_trailing_blanks(new_cell, sep)

    def normalize_displaced_last(self, original_last: RawCell, sep: int) -> None:
        """Normalise the trailing blanks of a cell pushed off the end.

        ``split_cells`` parks the file's final newline as a trailing ``""`` on
        the last cell. When a move/insert pushes that cell off the end, that
        ``""`` is the terminal artifact, not a real separator — reset its
        trailing blanks to the deck ``sep`` (so a tight deck loses it and a
        blank-separated deck keeps one). :meth:`flush` restores the terminal
        newline on whatever ends up last. No-op when still last.
        """
        if not self.ends_with_newline or not self.cells:
            return
        if self.cells[-1] is original_last:
            return
        _set_trailing_blanks(original_last, sep)

    def render(self) -> str:
        """The exact text :meth:`flush` would write, computed without touching disk.

        Factored out of :meth:`flush` so a buffered / atomic writer (the Issue
        #190 temp-swap in :mod:`clm.slides.sync_apply`) can reproduce the same
        bytes and swap the file in a single step. Mirrors flush's terminal-newline
        restoration exactly; only meaningful to persist when :attr:`dirty`.
        """
        text = reconstruct(self.preamble, self.cells)
        # Deleting the file's last cell drops the trailing-newline element
        # that ``split_cells`` parked on it; restore the original terminal
        # newline so a remove never emits a "No newline at end of file" diff.
        if self.ends_with_newline and not text.endswith("\n"):
            text += "\n"
        return text

    def flush(self) -> None:
        if not self.dirty:
            return
        self.path.write_text(self.render(), encoding="utf-8", newline="\n")
        self.dirty = False

    @staticmethod
    def _rewrite_cell_body(cell: RawCell, new_text: str) -> None:
        original = cell.lines[1:]
        trailing_blanks = 0
        for line in reversed(original):
            if line == "":
                trailing_blanks += 1
            else:
                break

        new_lines = new_text.split("\n")
        while new_lines and new_lines[-1] == "":
            new_lines.pop()
        new_lines.extend([""] * trailing_blanks)

        cell.lines = [cell.lines[0], *new_lines]
