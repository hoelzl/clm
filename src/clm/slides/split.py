"""Bidirectional split/unify primitives for bilingual slide files.

This is Phase 5 of the slide-format-redesign feature. See
``handover-slide-format-redesign-clm.md`` §2.4 + §3 Phase 5.

``split`` rewrites a bilingual ``deck.py`` into the two single-language
files ``deck.de.py`` and ``deck.en.py``. ``unify`` is the inverse. Both
operations are designed to be byte-identical round trips::

    unify(*split(deck.py)) == deck.py
    split(unify(de.py, en.py)) == (de.py, en.py)

The round-trip property is non-negotiable — it is the trust foundation
that lets reverts, mid-migration coexistence, and diff-based verification
all work. The Hypothesis test suite at ``tests/slides/test_split.py``
pins both directions on generated and real fixtures.

**Header macro split (decided 2026-05-19):** bilingual files use the
existing two-arg ``header(de_title, en_title)`` Jinja macro; split files
use sibling macros ``header_de(de_title)`` (in ``*.de.py``) and
``header_en(en_title)`` (in ``*.en.py``). The j2 import line is
rewritten accordingly when the original is exactly ``import header``.

**Shared cells** (no ``lang`` attribute, not header/import) are copied
verbatim to both outputs and must be byte-identical between inputs when
unifying. Divergent shared cells are a hard error — Phase 6's validator
extension will surface this for builds; ``unify`` raises eagerly so the
user notices before writing.

**Canonical pattern requirement for round-trip.** ``unify`` recovers the
original interleaving by treating shared cells as alignment points and
pairing adjacent DE/EN cells with matching ``slide_id``. This is
unambiguous for the canonical pattern actually used by the course
repos: paired language-tagged cells appear as adjacent DE-then-EN with
the same ``slide_id``. A solo language-tagged cell (e.g. DE-only with
no EN sibling) is still emitted, but the relative order of a DE-only
solo and an EN-only solo cannot in general be recovered from the two
split outputs — the information is gone after the split. Real fixtures
in PythonCourses always pair tagged cells, so this restriction is
invisible in practice. Phase 3's validator flags missing slide_id; once
that promotes to error in 1.8, the canonical pattern is enforced
upstream too.

**Voiceover companion split (hardening 2026-06).** A slide file may have a
sibling voiceover companion (``slides_X.py`` → ``voiceover_X.py``, see
:func:`clm.slides.voiceover_tools.companion_path`). Splitting a bilingual
deck without touching that companion would orphan it — the build would no
longer find a companion next to either ``slides_X.de.py`` or
``slides_X.en.py``. So ``split_in_file`` splits the companion in lockstep
into ``voiceover_X.de.py`` / ``voiceover_X.en.py``, and ``unify_in_file``
recombines them. A companion has no header macro — it is just voiceover
cells carrying ``lang`` (and ``for_slide`` / ``vo_anchor``) — so the same
:func:`split_text` / :func:`unify_texts` primitives route it by language
and the round trip is byte-identical. This is well-defined because #162
guarantees ``de_id == en_id``, so each companion cell's owning slide
exists in its language's half. The companion dependency is imported
lazily, so ``split``/``unify`` of a plain deck never touch the voiceover
layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from clm.infrastructure.utils.path_utils import atomic_write_all
from clm.slides.raw_cells import RawCell, reconstruct, split_cells

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SplitError(Exception):
    """Raised when ``split`` cannot produce both outputs from a bilingual file."""


class UnifyError(Exception):
    """Raised when ``unify`` cannot reassemble a bilingual file.

    Concrete reasons include divergent shared cells, missing/extra cells,
    and mismatched header macros between the two inputs.
    """


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SplitResult:
    """Outcome of one :func:`split_in_file` call.

    ``de_companion`` / ``en_companion`` are set only when ``source`` had a
    sibling voiceover companion that was split alongside the deck;
    ``source_companion`` records the bilingual companion that was read.
    Companion overwrites are also listed in ``overwrote``.
    """

    source: str
    de_path: str
    en_path: str
    wrote: bool = False
    overwrote: list[str] = field(default_factory=list)
    source_companion: str | None = None
    de_companion: str | None = None
    en_companion: str | None = None


@dataclass
class UnifyResult:
    """Outcome of one :func:`unify_in_file` call.

    ``target_companion`` is set only when sibling voiceover companions were
    recombined alongside the deck; ``companion_overwrote`` reports whether
    that companion target already existed.
    """

    de_source: str
    en_source: str
    target: str
    wrote: bool = False
    overwrote: bool = False
    target_companion: str | None = None
    companion_overwrote: bool = False


# ---------------------------------------------------------------------------
# Cell classification
# ---------------------------------------------------------------------------


_BILINGUAL_HEADER_RE = re.compile(
    r'(\{\{\s*header\s*\(\s*")([^"]*)("\s*,\s*")([^"]*)("\s*\)\s*\}\})'
)
_HEADER_DE_RE = re.compile(r'(\{\{\s*header_de\s*\(\s*")([^"]*)("\s*\)\s*\}\})')
_HEADER_EN_RE = re.compile(r'(\{\{\s*header_en\s*\(\s*")([^"]*)("\s*\)\s*\}\})')

# Exact bare-form import line — anything more elaborate (extra macros, aliases)
# is treated as a shared j2 directive and copied verbatim to both outputs.
_HEADER_IMPORT_RE = re.compile(r"^(#\s*j2\s+from\s+\S+\s+import\s+)header\s*$")
_HEADER_DE_IMPORT_RE = re.compile(r"^(#\s*j2\s+from\s+\S+\s+import\s+)header_de\s*$")
_HEADER_EN_IMPORT_RE = re.compile(r"^(#\s*j2\s+from\s+\S+\s+import\s+)header_en\s*$")


def _is_bilingual_header_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_BILINGUAL_HEADER_RE.search(cell.lines[0]))


def _is_header_de_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_HEADER_DE_RE.search(cell.lines[0]))


def _is_header_en_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_HEADER_EN_RE.search(cell.lines[0]))


def _is_bilingual_header_import_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_HEADER_IMPORT_RE.match(cell.lines[0]))


def _is_header_de_import_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_HEADER_DE_IMPORT_RE.match(cell.lines[0]))


def _is_header_en_import_cell(cell: RawCell) -> bool:
    return cell.metadata.is_j2 and bool(_HEADER_EN_IMPORT_RE.match(cell.lines[0]))


# ---------------------------------------------------------------------------
# Split: bilingual → (DE, EN)
# ---------------------------------------------------------------------------


def split_text(text: str) -> tuple[str, str]:
    """Return ``(de_text, en_text)`` for a bilingual slide-file ``text``.

    The split is purely structural — no cell content is reformatted. Cells
    are routed by ``lang`` attribute: ``lang="de"`` → DE only,
    ``lang="en"`` → EN only, no lang → both. The bilingual ``header``
    macro call and its ``import header`` directive are rewritten into the
    sibling-macro forms (``header_de`` / ``header_en``) for the
    appropriate language.

    Raises :class:`SplitError` if the file contains a ``header_de`` or
    ``header_en`` cell, which indicates the input is already split.
    """
    preamble, cells = split_cells(text)

    de_cells: list[RawCell] = []
    en_cells: list[RawCell] = []

    for cell in cells:
        if _is_header_de_cell(cell) or _is_header_en_cell(cell):
            raise SplitError(
                f"input already contains a split header macro at line "
                f"{cell.line_number}: {cell.lines[0]!r}"
            )

        if _is_bilingual_header_cell(cell):
            de_cells.append(_rewrite_header_to_de(cell))
            en_cells.append(_rewrite_header_to_en(cell))
            continue

        if _is_bilingual_header_import_cell(cell):
            de_cells.append(_rewrite_import_to_de(cell))
            en_cells.append(_rewrite_import_to_en(cell))
            continue

        lang = cell.metadata.lang
        if lang == "de":
            de_cells.append(cell)
        elif lang == "en":
            en_cells.append(cell)
        else:
            # Shared cell: no lang attribute (j2 directives, language-neutral
            # code, narrative without lang). Copied verbatim to both files
            # — the unify step requires shared cells to be byte-identical.
            de_cells.append(_clone_cell(cell))
            en_cells.append(_clone_cell(cell))

    return (
        reconstruct(preamble, de_cells),
        reconstruct(preamble, en_cells),
    )


def _clone_cell(cell: RawCell) -> RawCell:
    return RawCell(
        lines=list(cell.lines),
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _rewrite_header_to_de(cell: RawCell) -> RawCell:
    new_header = _BILINGUAL_HEADER_RE.sub(
        lambda m: f'{{{{ header_de("{m.group(2)}") }}}}',
        cell.lines[0],
    )
    new_lines = [new_header, *cell.lines[1:]]
    return RawCell(
        lines=new_lines,
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _rewrite_header_to_en(cell: RawCell) -> RawCell:
    new_header = _BILINGUAL_HEADER_RE.sub(
        lambda m: f'{{{{ header_en("{m.group(4)}") }}}}',
        cell.lines[0],
    )
    new_lines = [new_header, *cell.lines[1:]]
    return RawCell(
        lines=new_lines,
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _rewrite_import_to_de(cell: RawCell) -> RawCell:
    new_header = _HEADER_IMPORT_RE.sub(lambda m: f"{m.group(1)}header_de", cell.lines[0])
    new_lines = [new_header, *cell.lines[1:]]
    return RawCell(
        lines=new_lines,
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _rewrite_import_to_en(cell: RawCell) -> RawCell:
    new_header = _HEADER_IMPORT_RE.sub(lambda m: f"{m.group(1)}header_en", cell.lines[0])
    new_lines = [new_header, *cell.lines[1:]]
    return RawCell(
        lines=new_lines,
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


@dataclass
class _CompanionSplitPlan:
    """A planned split of a sibling voiceover companion (read, not yet written)."""

    source: Path
    de_path: Path
    en_path: Path
    de_text: str
    en_text: str


def _plan_companion_split(source: Path, de_path: Path, en_path: Path) -> _CompanionSplitPlan | None:
    """Plan splitting ``source``'s voiceover companion in lockstep with the deck.

    Returns ``None`` when ``source`` has no sibling ``voiceover_*.py``.
    Otherwise reads the bilingual companion and routes its cells by language
    with the same :func:`split_text` primitive used for the deck — companions
    carry no header macro, so this is a pure per-language route that preserves
    each cell's ``for_slide`` / ``vo_anchor`` verbatim. Raising here (e.g. a
    malformed companion) aborts the whole split before any file is written.

    The ``voiceover_tools`` import is deferred so a plain deck split never
    pulls in the voiceover layer.
    """
    from clm.slides.voiceover_tools import companion_path

    companion = companion_path(source)
    if not companion.exists():
        return None
    comp_de_text, comp_en_text = split_text(companion.read_text(encoding="utf-8"))
    return _CompanionSplitPlan(
        source=companion,
        de_path=companion_path(de_path),
        en_path=companion_path(en_path),
        de_text=comp_de_text,
        en_text=comp_en_text,
    )


def split_in_file(
    source: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SplitResult:
    """Split ``source`` (bilingual ``.py``) into adjacent ``.de.py`` /
    ``.en.py`` companions.

    If ``source`` has a sibling voiceover companion (``voiceover_*.py``) it is
    split in lockstep into ``voiceover_*.de.py`` / ``voiceover_*.en.py`` so the
    deck split never orphans the narration; see the module docstring.

    Refuses if any target (deck or companion half) exists unless ``force=True``.
    In ``dry_run`` mode no files are written; the result still reports what
    *would* have been overwritten.
    """
    source = source.resolve()
    de_path = _split_target(source, "de")
    en_path = _split_target(source, "en")

    text = source.read_text(encoding="utf-8")
    de_text, en_text = split_text(text)

    companion = _plan_companion_split(source, de_path, en_path)

    overwrote: list[str] = []
    if de_path.exists():
        overwrote.append(str(de_path))
    if en_path.exists():
        overwrote.append(str(en_path))
    if companion is not None:
        if companion.de_path.exists():
            overwrote.append(str(companion.de_path))
        if companion.en_path.exists():
            overwrote.append(str(companion.en_path))

    if overwrote and not force:
        raise SplitError(
            "refusing to overwrite existing split companions without --force: "
            + ", ".join(overwrote)
        )

    if not dry_run:
        writes = [(de_path, de_text), (en_path, en_text)]
        if companion is not None:
            writes.append((companion.de_path, companion.de_text))
            writes.append((companion.en_path, companion.en_text))
        atomic_write_all(writes)

    return SplitResult(
        source=str(source),
        de_path=str(de_path),
        en_path=str(en_path),
        wrote=not dry_run,
        overwrote=overwrote,
        source_companion=str(companion.source) if companion is not None else None,
        de_companion=str(companion.de_path) if companion is not None else None,
        en_companion=str(companion.en_path) if companion is not None else None,
    )


def _split_target(source: Path, lang: str) -> Path:
    """Return the ``<basename>.<lang>.py`` companion path for ``source``."""
    if source.suffix != ".py":
        raise SplitError(f"source must be a .py file: {source}")
    basename = source.name[: -len(".py")]
    if basename.endswith(".de") or basename.endswith(".en"):
        raise SplitError(f"source already looks split (ends with .de/.en): {source}")
    return source.with_name(f"{basename}.{lang}.py")


# ---------------------------------------------------------------------------
# Unify: (DE, EN) → bilingual
# ---------------------------------------------------------------------------


def unify_texts(de_text: str, en_text: str) -> str:
    """Return the bilingual text reconstructed from ``de_text`` and ``en_text``.

    Walks both cell lists with parallel cursors and interleaves cells back
    into the canonical bilingual order:

    - DE-only and EN-only cells are paired greedily (DE first when both
      cursors point at language-tagged cells).
    - Shared cells (no ``lang``) must be byte-identical at the corresponding
      position in both inputs.
    - ``header_de`` / ``header_en`` cells combine into the bilingual
      ``header(de, en)`` form; their adjacent ``import`` directives
      collapse from the split forms back to the bare ``import header``.

    Raises :class:`UnifyError` on any structural mismatch (divergent shared
    cell, leftover cells, header mismatch).
    """
    de_preamble, de_cells = split_cells(de_text)
    en_preamble, en_cells = split_cells(en_text)

    if de_preamble != en_preamble:
        raise UnifyError("DE and EN files differ in their preamble (lines before any cell)")

    out_cells: list[RawCell] = []
    i_de = 0
    i_en = 0

    while i_de < len(de_cells) or i_en < len(en_cells):
        de_cell = de_cells[i_de] if i_de < len(de_cells) else None
        en_cell = en_cells[i_en] if i_en < len(en_cells) else None

        # Both done: loop exits next iteration; nothing to emit.
        if de_cell is None and en_cell is None:
            break

        # Header-import pairing: rewrite to the bilingual import line.
        if (
            de_cell is not None
            and en_cell is not None
            and _is_header_de_import_cell(de_cell)
            and _is_header_en_import_cell(en_cell)
        ):
            out_cells.append(_collapse_imports(de_cell, en_cell))
            i_de += 1
            i_en += 1
            continue

        # Header-macro pairing: rebuild the bilingual ``header(de, en)`` call.
        if (
            de_cell is not None
            and en_cell is not None
            and _is_header_de_cell(de_cell)
            and _is_header_en_cell(en_cell)
        ):
            out_cells.append(_collapse_header_macros(de_cell, en_cell))
            i_de += 1
            i_en += 1
            continue

        # Shared cell pairing: byte-identical or it is an error.
        if (
            de_cell is not None
            and en_cell is not None
            and _is_shared(de_cell)
            and _is_shared(en_cell)
        ):
            if de_cell.lines != en_cell.lines:
                raise UnifyError(
                    f"shared cell content diverges: DE line {de_cell.line_number}, "
                    f"EN line {en_cell.line_number}"
                )
            out_cells.append(_clone_cell(de_cell))
            i_de += 1
            i_en += 1
            continue

        # DE-tagged cell at the DE cursor: emit it, and pair with the EN
        # cursor when it sits on an EN-tagged cell with the matching
        # ``slide_id`` (the canonical adjacent DE→EN pairing in
        # bilingual files). If the slide_ids differ — or one side is
        # missing — the cells are not part of the same logical pair, so
        # we emit DE alone and let the EN cell be handled on a later
        # iteration. Cells that lack ``slide_id`` entirely (legacy
        # un-tagged content) pair by adjacency, the same rule split
        # used when emitting them.
        if de_cell is not None and de_cell.metadata.lang == "de":
            out_cells.append(_clone_cell(de_cell))
            i_de += 1
            if (
                en_cell is not None
                and en_cell.metadata.lang == "en"
                and _slide_ids_pair(de_cell, en_cell)
            ):
                out_cells.append(_clone_cell(en_cell))
                i_en += 1
            continue

        # EN-tagged cell at the EN cursor with no DE pair available.
        if en_cell is not None and en_cell.metadata.lang == "en":
            out_cells.append(_clone_cell(en_cell))
            i_en += 1
            continue

        # Anything else is a structural mismatch we cannot interleave.
        _raise_alignment_error(de_cell, en_cell)

    return reconstruct(de_preamble, out_cells)


def _slide_ids_pair(de_cell: RawCell, en_cell: RawCell) -> bool:
    """Return True iff two language-tagged cells belong to the same logical slide.

    Phase 3 makes ``slide_id`` mandatory on slide/subslide/narrative cells,
    so the matching id is the authoritative pairing signal. Cells that
    legitimately carry no ``slide_id`` (mostly bare ``# %% lang="de"`` /
    ``# %% lang="en"`` code cells) fall back to adjacency — both sides
    being id-less is treated as the same pair.
    """
    de_id = de_cell.metadata.slide_id
    en_id = en_cell.metadata.slide_id
    return de_id == en_id


def _is_shared(cell: RawCell) -> bool:
    """Return True iff ``cell`` is a no-lang cell that is not a header macro.

    Header import lines and bilingual/split header macros are handled by
    dedicated branches; everything else without a ``lang`` attribute is
    treated as shared between the two outputs.
    """
    if cell.metadata.lang is not None:
        return False
    if _is_bilingual_header_cell(cell) or _is_header_de_cell(cell) or _is_header_en_cell(cell):
        return False
    if (
        _is_bilingual_header_import_cell(cell)
        or _is_header_de_import_cell(cell)
        or _is_header_en_import_cell(cell)
    ):
        return False
    return True


def _collapse_imports(de_cell: RawCell, en_cell: RawCell) -> RawCell:
    """Merge ``import header_de`` + ``import header_en`` cells back to ``import header``.

    Body lines (everything after the header line) must agree between the
    two inputs — they came from the same bilingual cell originally. The
    DE side wins on disagreement only after we've raised.
    """
    de_new_header = _HEADER_DE_IMPORT_RE.sub(lambda m: f"{m.group(1)}header", de_cell.lines[0])
    en_new_header = _HEADER_EN_IMPORT_RE.sub(lambda m: f"{m.group(1)}header", en_cell.lines[0])
    if de_new_header != en_new_header:
        raise UnifyError(
            f"header-import lines differ between DE and EN: "
            f"{de_cell.lines[0]!r} vs {en_cell.lines[0]!r}"
        )
    if de_cell.lines[1:] != en_cell.lines[1:]:
        raise UnifyError(
            f"trailing lines of header-import cell differ between DE and EN "
            f"(DE line {de_cell.line_number}, EN line {en_cell.line_number})"
        )
    return RawCell(
        lines=[de_new_header, *de_cell.lines[1:]],
        line_number=de_cell.line_number,
        metadata=de_cell.metadata,
    )


def _collapse_header_macros(de_cell: RawCell, en_cell: RawCell) -> RawCell:
    """Combine ``header_de(de)`` + ``header_en(en)`` cells into ``header(de, en)``."""
    de_match = _HEADER_DE_RE.search(de_cell.lines[0])
    en_match = _HEADER_EN_RE.search(en_cell.lines[0])
    if de_match is None or en_match is None:
        # Defensive: classification should have prevented this.
        raise UnifyError(
            f"header macro pair failed to parse: DE={de_cell.lines[0]!r} EN={en_cell.lines[0]!r}"
        )
    de_title = de_match.group(2)
    en_title = en_match.group(2)

    # Surrounding text (anything outside the ``{{ ... }}`` block) on the DE
    # side stays — typically the leading ``# `` comment prefix. The EN
    # version's surrounding text must be the same up to the macro call.
    de_surround = (
        de_cell.lines[0][: de_match.start()],
        de_cell.lines[0][de_match.end() :],
    )
    en_surround = (
        en_cell.lines[0][: en_match.start()],
        en_cell.lines[0][en_match.end() :],
    )
    if de_surround != en_surround:
        raise UnifyError(
            f"header macro surrounding text differs between DE and EN: "
            f"{de_cell.lines[0]!r} vs {en_cell.lines[0]!r}"
        )

    if de_cell.lines[1:] != en_cell.lines[1:]:
        raise UnifyError(
            f"trailing lines of header macro cell differ between DE and EN "
            f"(DE line {de_cell.line_number}, EN line {en_cell.line_number})"
        )

    new_macro = f'{{{{ header("{de_title}", "{en_title}") }}}}'
    new_header_line = f"{de_surround[0]}{new_macro}{de_surround[1]}"
    return RawCell(
        lines=[new_header_line, *de_cell.lines[1:]],
        line_number=de_cell.line_number,
        metadata=de_cell.metadata,
    )


def _raise_alignment_error(de_cell: RawCell | None, en_cell: RawCell | None) -> None:
    de_desc = (
        f"DE line {de_cell.line_number}: {de_cell.lines[0]!r}" if de_cell is not None else "DE end"
    )
    en_desc = (
        f"EN line {en_cell.line_number}: {en_cell.lines[0]!r}" if en_cell is not None else "EN end"
    )
    raise UnifyError(f"cannot align DE/EN cells — {de_desc}; {en_desc}")


@dataclass
class _CompanionUnifyPlan:
    """A planned recombination of split voiceover companions (read, not written)."""

    target: Path
    text: str


def _plan_companion_unify(
    de_source: Path, en_source: Path, target: Path
) -> _CompanionUnifyPlan | None:
    """Plan recombining the split voiceover companions of a ``.de`` / ``.en`` pair.

    Returns ``None`` when neither half has a sibling ``voiceover_*.<lang>.py``.
    A half that is absent is treated as empty so the present narration is never
    dropped (the inverse of :func:`_plan_companion_split`, which always writes
    both halves). Raising here (divergent shared companion cell, misalignment)
    aborts the whole unify before any file is written.
    """
    from clm.slides.voiceover_tools import companion_path

    de_comp = companion_path(de_source)
    en_comp = companion_path(en_source)
    if not de_comp.exists() and not en_comp.exists():
        return None
    de_text = de_comp.read_text(encoding="utf-8") if de_comp.exists() else ""
    en_text = en_comp.read_text(encoding="utf-8") if en_comp.exists() else ""
    return _CompanionUnifyPlan(target=companion_path(target), text=unify_texts(de_text, en_text))


def unify_in_file(
    de_source: Path,
    en_source: Path,
    *,
    target: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> UnifyResult:
    """Unify ``de_source`` + ``en_source`` into a bilingual ``target`` file.

    Default ``target`` is derived from ``de_source``: ``foo.de.py`` →
    ``foo.py``. If the pair has sibling voiceover companions
    (``voiceover_*.de.py`` / ``voiceover_*.en.py``) they are recombined in
    lockstep into ``voiceover_*.py`` — the inverse of :func:`split_in_file`'s
    companion split.

    Refuses to overwrite an existing target (deck or companion) unless
    ``force=True``. In ``dry_run`` mode no file is written.
    """
    de_source = de_source.resolve()
    en_source = en_source.resolve()
    if target is None:
        target = _unify_target(de_source, en_source)
    target = target.resolve()

    de_text = de_source.read_text(encoding="utf-8")
    en_text = en_source.read_text(encoding="utf-8")
    unified = unify_texts(de_text, en_text)

    companion = _plan_companion_unify(de_source, en_source, target)

    overwrote = target.exists()
    companion_overwrote = companion is not None and companion.target.exists()
    if (overwrote or companion_overwrote) and not force:
        blocking = [str(target)] if overwrote else []
        if companion_overwrote:
            blocking.append(str(companion.target))  # type: ignore[union-attr]
        raise UnifyError(
            "refusing to overwrite existing target without --force: " + ", ".join(blocking)
        )

    if not dry_run:
        writes = [(target, unified)]
        if companion is not None:
            writes.append((companion.target, companion.text))
        atomic_write_all(writes)

    return UnifyResult(
        de_source=str(de_source),
        en_source=str(en_source),
        target=str(target),
        wrote=not dry_run,
        overwrote=overwrote,
        target_companion=str(companion.target) if companion is not None else None,
        companion_overwrote=companion_overwrote,
    )


def _unify_target(de_source: Path, en_source: Path) -> Path:
    """Pick the bilingual companion path from a ``foo.de.py`` / ``foo.en.py`` pair."""
    de_base = _strip_lang_suffix(de_source, "de")
    en_base = _strip_lang_suffix(en_source, "en")
    if de_base != en_base:
        raise UnifyError(f"DE and EN sources do not share a basename: {de_source} vs {en_source}")
    return de_base


def _strip_lang_suffix(path: Path, lang: str) -> Path:
    if path.suffix != ".py":
        raise UnifyError(f"source must be a .py file: {path}")
    stem = path.name[: -len(".py")]
    suffix = f".{lang}"
    if not stem.endswith(suffix):
        raise UnifyError(f"source does not end in {suffix}.py: {path}")
    return path.with_name(f"{stem[: -len(suffix)]}.py")
