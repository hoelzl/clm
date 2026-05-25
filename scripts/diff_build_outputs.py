#!/usr/bin/env python3
"""Diff two CLM build outputs modulo known cosmetic noise.

Used as the consumer-side workaround for the
[Phase D byte-equivalence gate](../docs/claude/issue-133-investigation.md):
when verifying that bilingual vs split builds of the same deck produce
equivalent output, the raw `.ipynb` / `.py` files differ in two harmless
ways that are not worth blocking a pilot over.

1. **nbformat auto-`id` fields** on every cell are random UUIDs. They
   never match across two independent builds even when the content is
   identical.
2. **jupytext's `lines_to_next_cell` metadata** records deviations from
   PEP 8 expected blank-line counts. Because jupytext's check
   (`pep8_lines_between_cells` in `jupytext/pep8.py`) looks ahead through
   neighboring cells, identical inter-cell whitespace can produce
   different metadata when the bilingual file interleaves DE/EN code
   cells but the split file places a DE markdown cell next. See
   `docs/claude/issue-133-investigation.md` for the full trace and the
   producer-side fix this workaround papers over.

Both of those have an `.ipynb` half (cell metadata) and a `.py` half
(blank-line counts driven by `lines_to_next_cell` on write). This script
normalizes both.

Usage:
    python scripts/diff_build_outputs.py <dir_a> <dir_b>
    python scripts/diff_build_outputs.py <dir_a> <dir_b> --show-diffs

Exits 0 if every file matches (byte-identical OR identical after
normalization); exits 1 if any file still differs.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def _normalize_ipynb(text: str) -> str:
    """Drop nbformat auto-`id` and jupytext `lines_to_next_cell` from `.ipynb`.

    Re-serializes with `json.dumps(..., indent=1, sort_keys=True)` so the
    normalized form is stable across builds even if cell metadata key
    ordering shifts.
    """
    nb = json.loads(text)
    for cell in nb.get("cells", []):
        cell.pop("id", None)
        meta = cell.get("metadata")
        if isinstance(meta, dict):
            meta.pop("lines_to_next_cell", None)
    return json.dumps(nb, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


_CELL_MARKER_RE = re.compile(r"^#\s*%%")


def _normalize_py(text: str) -> str:
    """Collapse blank-line runs immediately before `# %%` markers to one.

    Jupytext writes blank-line counts based on each cell's
    `lines_to_next_cell` metadata (or PEP 8 default when absent). When
    one build records the metadata and the other does not, the same
    cells end up with different inter-cell spacing. We canonicalize
    on "exactly one blank line before each cell marker" so the spacing
    differences fall out.

    Blank lines inside a cell's body (e.g. between two top-level `def`s
    of a code cell) are untouched — only the run that *immediately
    precedes* a cell marker is collapsed.
    """
    lines = text.split("\n")
    out: list[str] = []
    pending_blanks = 0
    for line in lines:
        if line == "":
            pending_blanks += 1
            continue
        if pending_blanks and _CELL_MARKER_RE.match(line):
            out.append("")  # exactly one blank line before each cell marker
        else:
            out.extend([""] * pending_blanks)
        pending_blanks = 0
        out.append(line)
    out.extend([""] * pending_blanks)
    return "\n".join(out)


_NORMALIZERS: dict[str, Callable[[str], str]] = {
    ".ipynb": _normalize_ipynb,
    ".py": _normalize_py,
}


def _normalize_path(p: Path) -> str | None:
    """Return the normalized text of `p`, or None if it has no normalizer."""
    norm = _NORMALIZERS.get(p.suffix)
    if norm is None:
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    return norm(text)


# ---------------------------------------------------------------------------
# Tree walk + diff
# ---------------------------------------------------------------------------


def _walk_tree(root: Path) -> dict[Path, Path]:
    """Map relative path -> absolute path for every file in `root`."""
    return {p.relative_to(root): p for p in root.rglob("*") if p.is_file()}


def _unified_diff(a: str, b: str, label_a: str, label_b: str, *, n: int = 3) -> str:
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=label_a,
            tofile=label_b,
            n=n,
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dir_a", type=Path, help="First build directory.")
    parser.add_argument("dir_b", type=Path, help="Second build directory.")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Compare raw bytes without applying normalizers (debug aid).",
    )
    parser.add_argument(
        "--show-diffs",
        action="store_true",
        help="Print a unified diff for each mismatching file.",
    )
    parser.add_argument(
        "--diff-context",
        type=int,
        default=3,
        help="Lines of context in --show-diffs output (default: 3).",
    )
    args = parser.parse_args(argv)

    if not args.dir_a.is_dir():
        print(f"error: {args.dir_a} is not a directory", file=sys.stderr)
        return 2
    if not args.dir_b.is_dir():
        print(f"error: {args.dir_b} is not a directory", file=sys.stderr)
        return 2

    tree_a = _walk_tree(args.dir_a)
    tree_b = _walk_tree(args.dir_b)

    paths = sorted(set(tree_a) | set(tree_b))

    n_identical = 0
    n_normalized_match = 0
    n_diff_after_norm = 0
    n_raw_diff = 0
    n_only_a = 0
    n_only_b = 0
    n_skipped = 0

    diffs: list[tuple[Path, str]] = []
    only_in_a: list[Path] = []
    only_in_b: list[Path] = []

    for rel in paths:
        if rel not in tree_a:
            n_only_b += 1
            only_in_b.append(rel)
            continue
        if rel not in tree_b:
            n_only_a += 1
            only_in_a.append(rel)
            continue
        pa, pb = tree_a[rel], tree_b[rel]
        raw_a = pa.read_bytes()
        raw_b = pb.read_bytes()
        if raw_a == raw_b:
            n_identical += 1
            continue

        if args.raw:
            n_raw_diff += 1
            diffs.append((rel, ""))
            continue

        norm_a = _normalize_path(pa)
        norm_b = _normalize_path(pb)
        if norm_a is None or norm_b is None:
            n_skipped += 1
            diffs.append((rel, "(no normalizer for this extension)"))
            continue
        if norm_a == norm_b:
            n_normalized_match += 1
            continue
        n_diff_after_norm += 1
        body = (
            _unified_diff(
                norm_a,
                norm_b,
                f"A/{rel.as_posix()}",
                f"B/{rel.as_posix()}",
                n=args.diff_context,
            )
            if args.show_diffs
            else ""
        )
        diffs.append((rel, body))

    print()
    print(f"Summary ({args.dir_a} vs {args.dir_b}):")
    print(f"  identical (byte-for-byte):     {n_identical}")
    if not args.raw:
        print(f"  identical after normalization: {n_normalized_match}")
        print(f"  differ after normalization:    {n_diff_after_norm}")
    else:
        print(f"  differ (raw):                  {n_raw_diff}")
    print(f"  only in A:                     {n_only_a}")
    print(f"  only in B:                     {n_only_b}")
    if n_skipped:
        print(f"  no normalizer (counted as diff): {n_skipped}")

    if only_in_a:
        print()
        print("Only in A:")
        for rel in only_in_a:
            print(f"  {rel}")
    if only_in_b:
        print()
        print("Only in B:")
        for rel in only_in_b:
            print(f"  {rel}")
    if diffs:
        print()
        print("Files that still differ:")
        for rel, body in diffs:
            print(f"  - {rel}")
            if body:
                print(body)

    return 1 if (diffs or only_in_a or only_in_b) else 0


if __name__ == "__main__":
    sys.exit(main())
