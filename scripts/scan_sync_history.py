"""Scan a course repo's git history for the #1130 both-halves move+edit shape.

Background: when BOTH halves of a split deck move and edit the same slide's
un-id'd shared cells (code, shared markdown) between two syncs, the sync
ledger (fingerprints, no bodies) cannot tell which edited cell is which, and
``clm slides sync`` may pair them wrongly (#1130 step 3, withdrawn on this
scan's measurement; see the design doc's section 13). This script measures
how often that shape occurs in a real course repo.

For every commit touching a deck pair (``slides_*.de.<ext>`` /
``slides_*.en.<ext>``), each slide group's un-id'd shared cells are read per
half as ``(span anchor, body)`` pairs: the span anchor is the nearest
preceding id'd cell. A group whose halves agree is "in sync" and becomes the
base for later states.

- A committed state whose halves disagree is classified against the base:
  per half, moved (its shared cells changed span or order) and edited (a body
  absent from the base). ``RESIDUAL`` is both halves moved AND edited.
- A change between two in-sync states that moved and edited shared cells is
  where the residual was at most possible (the work may have been split
  across the halves). The "moved" test is crude: a new id'd cell inserted
  between shared cells counts as a move, so this is an upper bound.

Commits only: uncommitted states between syncs are invisible, so the residual
count is a lower bound.

Usage::

    python scripts/scan_sync_history.py <course-repo> [--ext py|cpp|...] [--since 2026-05-30]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import Counter, defaultdict

HEAD_RE = re.compile(r"^(#|//) %%(.*)$")
ID_RE = re.compile(r'slide_id="([^"]+)"')
TAGS_RE = re.compile(r"tags=\[([^\]]*)\]")

Cells = list[tuple[str, str]]


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout


def parse(text: str) -> dict[str, Cells]:
    """Slide group -> the un-id'd shared cells as ``(span anchor, body)``."""
    cells: list[tuple[str, str]] = []
    head: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        m = HEAD_RE.match(line)
        if m:
            if head is not None:
                cells.append((head, "\n".join(body).strip()))
            head, body = m.group(2), []
        elif head is not None:
            body.append(line)
    if head is not None:
        cells.append((head, "\n".join(body).strip()))

    groups: dict[str, Cells] = defaultdict(list)
    group = anchor = "_pre"
    for h, b in cells:
        idm = ID_RE.search(h)
        if idm:
            tags = TAGS_RE.search(h)
            tagstr = tags.group(1) if tags else ""
            if '"slide"' in tagstr or '"subslide"' in tagstr:
                group = idm.group(1)
            anchor = idm.group(1)
        elif "lang=" not in h:  # localized cells are not shared
            groups[group].append((anchor, b))
    return groups


def classify(base: Cells, now: Cells) -> tuple[bool, bool]:
    """``(moved, edited)`` of one half's shared cells against the base."""
    base_bodies = Counter(b for _, b in base)
    now_bodies = Counter(b for _, b in now)
    edited = any(now_bodies[b] > base_bodies[b] for b in now_bodies)
    kept = [x for x in now if base_bodies[x[1]] > 0]
    kept_base = [x for x in base if now_bodies[x[1]] > 0]
    moved = len(base) == len(now) and [a for a, _ in base] != [a for a, _ in now]
    if [b for _, b in kept] != [b for _, b in kept_base]:
        moved = True
    if len(kept) == len(kept_base) and Counter(kept) != Counter(kept_base):
        moved = True
    return moved, edited


def scan(repo: str, ext: str, since: str) -> None:
    pattern = re.compile(rf"/slides_[^/]*\.(de|en)\.{re.escape(ext)}$")
    suffix = re.compile(rf"\.(de|en)\.{re.escape(ext)}$")
    commits = _git(
        repo,
        "log",
        "--reverse",
        "--first-parent",
        f"--since={since}",
        "--format=%H",
        "--",
        f"slides/**/slides_*.de.{ext}",
        f"slides/**/slides_*.en.{ext}",
    ).split()

    base: dict[tuple[str, str], Cells] = {}
    stats: Counter[str] = Counter()
    residual: list[tuple[str, str, str, Cells, Cells, Cells]] = []
    windows: list[tuple[str, str, str, Cells, Cells]] = []
    for c in commits:
        changed = _git(repo, "diff-tree", "--no-commit-id", "-r", "--name-only", f"{c}^", c)
        decks = {suffix.sub("", p) for p in changed.split() if pattern.search("/" + p)}
        for deck in decks:
            de_text = _git(repo, "show", f"{c}:{deck}.de.{ext}")
            en_text = _git(repo, "show", f"{c}:{deck}.en.{ext}")
            if not de_text or not en_text:
                continue
            de_g, en_g = parse(de_text), parse(en_text)
            for g in set(de_g) | set(en_g):
                d, e = de_g.get(g, []), en_g.get(g, [])
                key = (deck, g)
                if d == e:
                    prev = base.get(key)
                    if prev is not None and prev != d:
                        stats["in-sync -> in-sync changes"] += 1
                        moved, edited = classify(prev, d)
                        if moved and edited:
                            stats["  ... that moved AND edited shared cells"] += 1
                            windows.append((c[:8], deck, g, prev, d))
                    base[key] = d
                    continue
                if key not in base:
                    continue
                stats["diverged group states"] += 1
                dm, dx = classify(base[key], d)
                em, ex = classify(base[key], e)
                if dm and dx and em and ex:
                    stats["  RESIDUAL: both halves moved AND edited"] += 1
                    residual.append((c[:8], deck, g, base[key], d, e))
                elif (dm and dx) or (em and ex):
                    stats["  one half moved and edited"] += 1
                elif dx and ex:
                    stats["  both halves edited, no move"] += 1
                elif dm or em:
                    stats["  a move, at most one edit"] += 1
                else:
                    stats["  edits only"] += 1

    print(f"{len(commits)} commits touching deck pairs since {since}")
    order = [
        "diverged group states",
        "  RESIDUAL: both halves moved AND edited",
        "  one half moved and edited",
        "  both halves edited, no move",
        "  a move, at most one edit",
        "  edits only",
        "in-sync -> in-sync changes",
        "  ... that moved AND edited shared cells",
    ]
    for k in order:
        print(f"{stats[k]:6d}  {k}")

    def show(cells: Cells) -> str:
        return " | ".join(f"{a}:{x[:30]!r}" for a, x in cells)

    for c, deck, g, b, d, e in residual:
        print(
            f"\nRESIDUAL {c} {deck} [{g}]\n  base: {show(b)}\n  de:   {show(d)}\n  en:   {show(e)}"
        )
    for c, deck, g, b, d in windows[:10]:
        print(f"\nmoved+edited window {c} {deck} [{g}]\n  before: {show(b)}\n  after:  {show(d)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", help="path to the course repo")
    parser.add_argument("--ext", default="py", help="deck file extension (py, cpp, ...)")
    parser.add_argument("--since", default="2026-05-30", help="first commit date to scan")
    args = parser.parse_args()
    scan(args.repo, args.ext, args.since)


if __name__ == "__main__":
    main()
