"""Migrate //-family slide decks (C#/C++/Java/TS) to the header-line-less title convention.

CLM's authoring tooling (split / sync / assign-ids / normalize / voiceover) assumes
the Python "header-line-less" convention: a deck's title is a standalone
``// {{ header("DE","EN") }}`` j2 cell with NO authored ``// %% [markdown]`` wrapper —
the ``header`` macro emits its own ``%% [markdown] lang="de"`` boundary. C#/C++/Java/TS
decks instead wrap the j2 header call in an authored ``// %% [markdown] ...`` cell.

This script removes that wrapper line so the deck matches the Python convention. It is
the deck-side half of the migration; the clm-side half is the matching macro change in
``src/clm/workers/notebook/templates_<lang>/macros.j2`` (header emits its own leading
boundary). The two MUST land together — applying one without the other shatters or
doubles the title cell, so a reformatted deck requires the new clm.

Build-output impact (validated against the real repos; see ``scripts/verify_header_reformat.py``):
  * ``lang="de"`` wrapper (C++ norm)   -> output BYTE-IDENTICAL (safe no-op).
  * neutral wrapper      (C# norm)     -> title cell goes neutral -> lang="de"; this FIXES a
                                          latent bug (a neutral DE title leaked into the EN
                                          build). Output changes intentionally — review EN titles.

Outliers are NOT rewritten (they need manual handling):
  * pre-content : extra content (e.g. ``<!-- clang-format off -->``) sits inside the wrapper
                  before ``header()`` — removing the wrapper would orphan it into a new cell.
  * code-wrapper: the wrapper was a bare ``// %%`` code cell, not ``[markdown]``.

Usage:
    python scripts/reformat_header_convention.py <course-slides-dir> [--apply] [--ext cs,cpp,java,ts]

Default is a dry run (report only). ``--apply`` rewrites files in place (commit first).
"""

from __future__ import annotations

import argparse
from pathlib import Path

EXCLUDE_DIRS = {"_archive", "_deleted", "_old", ".ipynb_checkpoints"}
DEFAULT_EXTS = ("cs", "cpp", "cxx", "cc", "java", "ts")


def _is_droppable_header_line(line: str) -> bool:
    """Lines inside the title construct that are removed by the reformat:
    the `// %%` wrapper(s), blank/`//` lines, and the (now-unnecessary)
    ``<!-- clang-format off/on -->`` HTML-comment blocks around the title."""
    s = line.strip()
    return (
        s in ("", "//", "// <!--", "// -->")
        or s.startswith("// %%")
        or s.startswith("// clang-format")
    )


def classify_and_reformat(text: str) -> tuple[str, str]:
    """Collapse the title construct to the header-line-less form.

    Replaces the whole title region — the authored ``// %%`` wrapper(s), the
    ``// j2 ... import header`` line, the ``// {{ header(...) }}`` call, and any
    ``<!-- clang-format off/on -->`` HTML comments around it — with just the two
    standalone cells the Python convention uses::

        // j2 from 'macros.j2' import header
        // {{ header("DE", "EN") }}

    The ``header`` macro then supplies the ``%% [markdown] lang="de"`` boundary.
    Handles the simple, neutral-wrapper, clang-format-wrapped and split-cell
    (import and header() in separate ``// %%`` cells) shapes uniformly, and is
    idempotent. Refuses (status ``outlier:*``) only when the title cell holds
    genuine non-droppable content or an unexpected ``lang=``.
    """
    lines = text.split("\n")
    imp = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.lstrip().startswith("// j2") and "import header" in ln
        ),
        None,
    )
    if imp is None:
        return text, "no-header-macro"
    hdr = next(
        (
            i
            for i in range(imp + 1, len(lines))
            if "{{ header" in lines[i] or "{{header" in lines[i]
        ),
        None,
    )
    if hdr is None:
        return text, "outlier:no-header-call"

    # region_start: include a `// %%` wrapper immediately above the import.
    region_start = imp
    if imp > 0 and lines[imp - 1].lstrip().startswith("// %%"):
        region_start = imp - 1
    # region_end: last line of the title construct, before the blank line that
    # precedes the next real `// %%` cell.
    nxt = next(
        (i for i in range(hdr + 1, len(lines)) if lines[i].lstrip().startswith("// %%")),
        len(lines),
    )
    region_end = nxt - 1
    while region_end > hdr and lines[region_end].strip() in ("", "//"):
        region_end -= 1

    region = lines[region_start : region_end + 1]
    # Every region line must be the import, the header call, or droppable junk.
    for i, ln in enumerate(region, start=region_start):
        if i in (imp, hdr):
            continue
        if not _is_droppable_header_line(ln):
            return text, "outlier:pre-content"

    # Determine the title wrapper's language (the [markdown] `// %%` line).
    md_wrappers = [ln for ln in region if ln.lstrip().startswith("// %%") and "[markdown]" in ln]
    if any('lang="en"' in w for w in md_wrappers):
        return text, "outlier:unexpected-lang"
    has_de = any('lang="de"' in w for w in md_wrappers)
    has_neutral = any('lang="' not in w for w in md_wrappers)

    new_region = [lines[imp], lines[hdr]]
    if new_region == region:
        return text, "already-headerless"
    rebuilt = lines[:region_start] + new_region + lines[region_end + 1 :]
    status = "reformatted" if (has_de and not has_neutral) else "neutral->de"
    return "\n".join(rebuilt), status


def iter_decks(root: Path, exts: tuple[str, ...]):
    for ext in exts:
        for p in root.rglob(f"*.{ext}"):
            if EXCLUDE_DIRS & set(p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "import header" in text:
                yield p, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root", type=Path, help="course slides directory")
    ap.add_argument(
        "--apply", action="store_true", help="rewrite files in place (default: dry run)"
    )
    ap.add_argument(
        "--ext", default=",".join(DEFAULT_EXTS), help="comma-separated extensions to scan"
    )
    args = ap.parse_args(argv)
    exts = tuple(e.strip().lstrip(".") for e in args.ext.split(",") if e.strip())

    counts: dict[str, int] = {}
    changed: list[Path] = []
    outliers: list[tuple[Path, str]] = []
    for path, text in iter_decks(args.root, exts):
        new_text, status = classify_and_reformat(text)
        counts[status] = counts.get(status, 0) + 1
        if status in ("reformatted", "neutral->de"):
            changed.append(path)
            if args.apply:
                path.write_text(new_text, encoding="utf-8")
        elif status.startswith("outlier"):
            outliers.append((path, status))

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"\n=== Header-convention reformat ({mode}) — {args.root} ===")
    for k in sorted(counts):
        print(f"  {k:22s}: {counts[k]}")
    print(f"\n  {'reformatted (lang=de, output preserved)':40s}: {counts.get('reformatted', 0)}")
    print(f"  {'neutral->de (output corrected — review EN)':40s}: {counts.get('neutral->de', 0)}")
    if outliers:
        print(f"\n  {len(outliers)} outlier(s) NOT changed (handle manually):")
        for p, s in outliers:
            print(f"    {s:22s} {p}")
    if not args.apply and changed:
        print(
            f"\n  {len(changed)} file(s) would change. Re-run with --apply to write (commit first)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
