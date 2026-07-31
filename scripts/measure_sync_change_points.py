"""How many structural change points does each keying rule expose?

A positional key re-keys when a "change point" above it inside its slide is
added, removed or renamed.

  anchor-scoped (today) : change points = slide anchors
  id-scoped (proposed)  : change points = slide anchors + every id'd cell

Counts both, so the trade can be argued from numbers instead of intuition.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.doc_lenses import _bare, load_bundle
from clm.slides.raw_cells import split_cells


def counts(raw_cells: list) -> tuple[int, int, int]:
    """(anchors, id'd non-anchors, id-less cells) in one deck half."""
    anchors = idd_non_anchor = idless = 0
    for cell in raw_cells:
        meta = cell.metadata
        bare = _bare(meta.slide_id)
        if meta.is_slide_start:
            anchors += 1
        elif bare is not None:
            idd_non_anchor += 1
        else:
            idless += 1
    return anchors, idd_non_anchor, idless


def main(root: Path) -> int:
    skip = {"_archive", "voiceover", "notes"}
    decks = sorted(
        p
        for p in root.rglob("*.de.py")
        if not (skip & set(p.parts)) and not p.name.startswith(("voiceover_", "notes_"))
    )
    anchors = idd = idless = 0
    for de_path in decks:
        try:
            bundle = load_bundle(de_path)
        except Exception:  # noqa: BLE001
            continue
        if bundle.outcome.deck is None:
            continue
        token = comment_token_for_path(bundle.de_path)
        _, raw = split_cells(bundle.de_path.read_text(encoding="utf-8"), token)
        a, i, n = counts(raw)
        anchors += a
        idd += i
        idless += n
    print(f"decks                     : {len(decks)}")
    print(f"slide anchors             : {anchors}")
    print(f"id'd NON-anchor cells     : {idd}")
    print(f"id-less (positional) cells: {idless}")
    print()
    print(f"change points, anchor-scoped : {anchors}")
    print(
        f"change points, id-scoped     : {anchors + idd}"
        f"  ({(anchors + idd) / max(anchors, 1):.1f}x more)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
