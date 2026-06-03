"""Structural anchors for slide pairing: DE/EN groups and the title macro.

Shared by :mod:`clm.slides.assign_ids` (Phase 2 — assigning slide_ids
to paired cells) and :mod:`clm.slides.validator` (Phase 3 — verifying
that already-assigned ids honor adjacency and pair-equivalence). The
helpers operate on any cell-like object that exposes ``metadata`` and
``header`` attributes, which covers both the validator's
:class:`clm.notebooks.slide_parser.Cell` and ``assign_ids``'s private
``_Cell`` dataclass.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from clm.infrastructure.utils.path_utils import split_lang_suffix
from clm.notebooks.slide_parser import CellMetadata

# Title-slide anchors:
#
# * Bilingual form: ``# {{ header("DE Title", "EN Title") }}`` — group 1
#   captures the EN title (the cross-language anchor used elsewhere).
# * Split form (Phase 5): ``# {{ header_de("DE Title") }}`` in ``*.de.py``
#   files and ``# {{ header_en("EN Title") }}`` in ``*.en.py`` files — group
#   2 captures the single title argument.
#
# The macro line itself never carries ``slide_id`` metadata — its presence
# anchors :data:`TITLE_SLIDE_ID` for following narrative cells.
HEADER_MACRO_RE = re.compile(
    r"\{\{\s*"
    r"(?:"
    r'header\s*\(\s*"[^"]*"\s*,\s*"([^"]*)"\s*\)'  # bilingual: captures EN title
    r"|"
    r'header_(?:de|en)\s*\(\s*"([^"]*)"\s*\)'  # split: captures the local title
    r")"
    r"\s*\}\}"
)

TITLE_SLIDE_ID = "title"


class CellLike(Protocol):
    """Structural protocol: cells produced by the slide parser or by the
    Phase 2 assign-ids splitter both satisfy this shape.
    """

    metadata: CellMetadata
    header: str


def is_title_macro_cell(cell: CellLike) -> bool:
    """Return True iff ``cell`` is the j2 ``header()`` title-slide macro line."""
    if not cell.metadata.is_j2:
        return False
    return bool(HEADER_MACRO_RE.search(cell.header))


def build_slide_groups(cells: Sequence[CellLike]) -> list[tuple[int, ...]]:
    """Group slide/subslide cell indices by source-order DE/EN adjacency.

    Each returned tuple is either ``(idx,)`` for a solo slide cell or
    ``(de_idx, en_idx)`` (in that source order) for an adjacent
    different-language pair. The grouping never spans non-slide cells —
    intervening code, j2, or narrative cells don't split a pair, because
    the algorithm walks the *slide-only* index list. Pairing requires
    that both members carry a ``lang`` attribute and that the two langs
    differ; identical-lang or lang-less neighbours stay solo.
    """
    slide_indices = [i for i, c in enumerate(cells) if c.metadata.is_slide_start]
    groups: list[tuple[int, ...]] = []
    i = 0
    while i < len(slide_indices):
        a = slide_indices[i]
        if i + 1 < len(slide_indices):
            b = slide_indices[i + 1]
            lang_a = cells[a].metadata.lang
            lang_b = cells[b].metadata.lang
            if lang_a and lang_b and lang_a != lang_b:
                groups.append((a, b))
                i += 2
                continue
        groups.append((a,))
        i += 1
    return groups


def build_slide_pairs(cells: Sequence[CellLike]) -> dict[int, int]:
    """Map every slide-cell index to the cell that *drives* its slug.

    EN-derived policy (handover §2.3): when a DE slide cell sits next
    to an EN slide cell in the source order, both cells share the slug
    derived from the EN heading. The returned map gives every slide
    cell the index of the cell to slug from — itself if solo, the EN
    sibling if paired.
    """
    pairs: dict[int, int] = {}
    for group in build_slide_groups(cells):
        if len(group) == 1:
            pairs[group[0]] = group[0]
        else:
            a, b = group
            en_idx = a if cells[a].metadata.lang == "en" else b
            pairs[a] = en_idx
            pairs[b] = en_idx
    return pairs


# ---------------------------------------------------------------------------
# Path-level split-pair derivation
#
# A split-format deck lives in two files, ``<deck>.de.py`` and
# ``<deck>.en.py``, that must stay in #162 ``slide_id`` parity. These helpers
# are the single home for the ``.de`` <-> ``.en`` twin arithmetic that used to
# live, copied, in ``assign_ids`` (defensive id reuse), ``validator`` (the
# single-file parity detective), and the ``slides sync`` CLI (the pairing
# guard). Keeping one copy avoids the four-way drift the duplication invited.
# ---------------------------------------------------------------------------


def split_twin(path: Path) -> Path | None:
    """The sibling split half (``.de.py`` <-> ``.en.py``) if it exists on disk.

    Returns ``None`` when ``path`` is not a recognised split half or the twin
    file is absent.
    """
    suffix = split_lang_suffix(path)
    if suffix is None:
        return None
    other = "en" if suffix == "de" else "de"
    parts = path.name.split(".")
    # split_lang_suffix guarantees the form ``<stem>.<de|en>.<ext>``.
    parts[-2] = other
    twin = path.with_name(".".join(parts))
    return twin if twin.exists() else None


def split_twin_pair(path: Path) -> tuple[Path, Path] | None:
    """If ``path`` is a split half whose twin exists on disk, return the ordered
    ``(de_path, en_path)`` pair; else ``None``.

    Used by the single-file validate path so a standalone
    ``clm validate slides_x.de.py`` (and the pre-commit gate) catches twin
    divergence even when not run over a whole directory.
    """
    twin = split_twin(path)
    if twin is None:
        return None
    return (path, twin) if split_lang_suffix(path) == "de" else (twin, path)


def split_lang_tag(path: Path) -> str | None:
    """The trailing ``.de`` / ``.en`` language tag of a filename, if present.

    Prefix-agnostic on purpose: unlike
    :func:`~clm.infrastructure.utils.path_utils.split_lang_suffix` it does *not*
    require the ``slides_``/``topic_``/``project_`` routing prefix — it only
    looks for a ``.de`` / ``.en`` segment immediately before the final
    extension. ``clm slides sync`` reconciles whatever two halves the author
    hands it (the build's topic-routing prefix is a separate concern), so the
    pairing guard must recognise e.g. ``apis.de.py`` as the DE half too.
    """
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    for lang in ("de", "en"):
        if stem.endswith(f".{lang}"):
            return lang
    return None


def _split_family(path: Path) -> str:
    """The deck-family key for the guard: the filename with its ``.de``/``.en``
    tag removed (so the two halves of one deck share a key). Extension is kept,
    so a ``.de.py`` and a ``.en.cpp`` are *different* families.
    """
    tag = split_lang_tag(path)
    if tag is None:
        return path.name
    ext = path.suffix
    stem = path.name[: -len(ext)] if ext else path.name
    return f"{stem[: -(len(tag) + 1)]}{ext}"


def order_split_pair(a: Path, b: Path) -> tuple[Path, Path] | None:
    """Order two caller-supplied paths as ``(de_path, en_path)`` if they form a
    valid same-deck split pair, auto-correcting a swapped order; else ``None``.

    Unlike :func:`split_twin_pair` (which *derives* the twin of one path from
    disk and is prefix-gated for build-time routing), this validates a pair the
    caller already holds — the ``clm slides sync`` pairing guard. It is
    deliberately prefix-agnostic (see :func:`split_lang_tag`): it returns
    ``None`` when either path lacks a ``.de``/``.en`` tag, both carry the same
    tag, or the two belong to different decks (different :func:`_split_family`).
    Existence on disk is the caller's concern (e.g. ``click.Path(exists=True)``).
    """
    ta = split_lang_tag(a)
    tb = split_lang_tag(b)
    if ta is None or tb is None or ta == tb:
        return None
    if _split_family(a) != _split_family(b):
        return None
    return (a, b) if ta == "de" else (b, a)


def derive_split_twin(path: Path) -> Path | None:
    """The sibling split half on disk, **prefix-agnostic** (unlike
    :func:`split_twin`, which is gated on the ``slides_``/``topic_``/``project_``
    routing prefix). Swaps the ``.de`` ↔ ``.en`` tag in the filename and returns
    the twin only if it exists on disk; ``None`` when ``path`` carries no
    ``.de``/``.en`` tag or the twin is absent.

    This is the disk-aware sibling of :func:`order_split_pair`, for the
    prefix-agnostic CLI surfaces — ``voiceover extract`` auto-pairing and the
    ``clm slides sync`` single-path contract — which must recognise any
    ``<deck>.de``/``.en`` pair the author hands over, not just routing-prefixed
    course files. A **voiceover companion** (``voiceover_*.py``) is deliberately
    *not* treated as a deck half: it carries a ``.de``/``.en`` tag, but it is the
    *output* of an extract, never a deck to auto-pair — so a companion passed by
    mistake derives no twin (preventing a re-extract that would empty both
    companions).
    """
    if path.name.startswith("voiceover_"):
        return None
    tag = split_lang_tag(path)
    if tag is None:
        return None
    other = "en" if tag == "de" else "de"
    parts = path.name.split(".")
    parts[-2] = other
    twin = path.with_name(".".join(parts))
    return twin if twin.exists() else None


def derive_split_pair(path: Path) -> tuple[Path, Path] | None:
    """Ordered ``(de_path, en_path)`` for a split half whose twin exists on disk
    — the prefix-agnostic, disk-aware analogue of :func:`split_twin_pair`.

    Returns ``None`` when ``path`` is not a ``.de``/``.en`` half or its twin is
    absent. The result is validated and ordered through :func:`order_split_pair`,
    so a degenerate non-pair (e.g. cross-family) still yields ``None``.
    """
    twin = derive_split_twin(path)
    if twin is None:
        return None
    return order_split_pair(path, twin)
