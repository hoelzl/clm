#!/usr/bin/env python3
"""Report (and optionally remove) git worktrees whose branch is already merged.

Long-lived repos accumulate dozens of `git worktree` checkouts under
`.claude/worktrees/` from finished issues, releases, and agent sessions. This
script classifies every worktree against a base ref (default `origin/master`)
and, by default, **only reports** — it deletes nothing unless you pass
`--delete`, and even then it never force-removes a dirty or locked worktree.

Usage:
    # Report only (safe; the default). Review before pruning.
    python scripts/prune_merged_worktrees.py

    # Fetch first so "merged" is judged against the latest remote base.
    python scripts/prune_merged_worktrees.py --fetch

    # Judge against a different base ref.
    python scripts/prune_merged_worktrees.py --base origin/main

    # Actually remove the worktrees classified MERGED-CLEAN (and delete their
    # local branches with `git branch -d`, which itself refuses unmerged ones).
    python scripts/prune_merged_worktrees.py --delete

A worktree is only ever auto-removed when it is **all** of: merged into the base,
free of uncommitted changes, not locked, not the main worktree, and not the
worktree this command runs from. Everything else is reported and left alone.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Classification labels.
MERGED_CLEAN = "MERGED-CLEAN"  # safe to remove
MERGED_DIRTY = "MERGED-DIRTY"  # merged but has uncommitted changes -> keep
UNMERGED = "UNMERGED"  # commits not in base -> keep
LOCKED = "LOCKED"  # explicitly locked -> keep
DETACHED = "DETACHED"  # detached HEAD, no branch -> keep
PROTECTED = "PROTECTED"  # main worktree / current worktree / base branch -> keep


@dataclass
class Worktree:
    path: Path
    head: str = ""
    branch: str | None = None  # short name, e.g. "claude/foo"
    detached: bool = False
    locked: bool = False
    is_main: bool = False
    classification: str = ""
    note: str = field(default="")


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def _git_root() -> Path:
    cp = _run(["git", "rev-parse", "--show-toplevel"])
    if cp.returncode != 0:
        sys.exit("error: not inside a git repository")
    return Path(cp.stdout.strip())


def parse_worktrees() -> list[Worktree]:
    """Parse `git worktree list --porcelain` into Worktree records.

    The first block git emits is always the main worktree; we flag it so it is
    never a deletion candidate.
    """
    cp = _run(["git", "worktree", "list", "--porcelain"])
    if cp.returncode != 0:
        sys.exit(f"error: `git worktree list` failed: {cp.stderr.strip()}")

    worktrees: list[Worktree] = []
    current: Worktree | None = None
    for line in cp.stdout.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = Worktree(path=Path(line[len("worktree ") :]))
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD ") :]
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            current.branch = ref.removeprefix("refs/heads/")
        elif line == "detached":
            current.detached = True
        elif line.startswith("locked"):
            current.locked = True
        elif line.startswith("bare"):
            current.is_main = True
    if current is not None:
        worktrees.append(current)

    if worktrees:
        worktrees[0].is_main = True  # git lists the main worktree first
    return worktrees


def is_merged(head: str, base: str) -> bool:
    """True when `head` is an ancestor of `base` (i.e. fully merged into it)."""
    cp = _run(["git", "merge-base", "--is-ancestor", head, base])
    return cp.returncode == 0


def is_dirty(path: Path) -> bool:
    """True when the worktree has uncommitted changes (tracked or untracked)."""
    cp = _run(["git", "status", "--porcelain"], cwd=path)
    if cp.returncode != 0:
        return True  # cannot tell -> treat as unsafe
    return bool(cp.stdout.strip())


def base_branch_name(base: str) -> str:
    """The local branch name a base ref maps to, e.g. origin/master -> master."""
    return base.split("/", 1)[1] if "/" in base else base


def classify(wt: Worktree, base: str, cwd: Path, base_branch: str) -> None:
    if wt.is_main:
        wt.classification = PROTECTED
        wt.note = "main worktree"
        return
    try:
        if wt.path.resolve() == cwd.resolve():
            wt.classification = PROTECTED
            wt.note = "current worktree"
            return
    except OSError:
        pass
    if wt.locked:
        wt.classification = LOCKED
        wt.note = "locked"
        return
    if wt.detached or wt.branch is None:
        wt.classification = DETACHED
        wt.note = "detached HEAD"
        return
    if wt.branch == base_branch:
        wt.classification = PROTECTED
        wt.note = f"on base branch '{base_branch}'"
        return
    if not is_merged(wt.head, base):
        wt.classification = UNMERGED
        wt.note = f"commits not in {base}"
        return
    if is_dirty(wt.path):
        wt.classification = MERGED_DIRTY
        wt.note = "uncommitted changes"
        return
    wt.classification = MERGED_CLEAN
    wt.note = f"merged into {base}"


def remove_worktree(wt: Worktree) -> bool:
    """Remove a MERGED-CLEAN worktree and delete its branch. Never force."""
    cp = _run(["git", "worktree", "remove", str(wt.path)])
    if cp.returncode != 0:
        print(f"  ! failed to remove {wt.path}: {cp.stderr.strip()}")
        return False
    print(f"  - removed worktree {wt.path}")
    if wt.branch:
        # `git branch -d` is the safe delete: it refuses unmerged branches.
        bd = _run(["git", "branch", "-d", wt.branch])
        if bd.returncode == 0:
            print(f"    - deleted branch {wt.branch}")
        else:
            print(f"    ! kept branch {wt.branch}: {bd.stderr.strip()}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base",
        default="origin/master",
        help="Base ref to judge 'merged' against (default: origin/master).",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Run `git fetch` before classifying, so the base ref is current.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually remove MERGED-CLEAN worktrees (default is report-only).",
    )
    args = parser.parse_args()

    root = _git_root()
    cwd = Path.cwd()

    if args.fetch:
        print("Fetching…")
        fp = _run(["git", "fetch"], cwd=root)
        if fp.returncode != 0:
            print(f"warning: git fetch failed: {fp.stderr.strip()}", file=sys.stderr)

    # Verify the base ref resolves before we classify everything against it.
    if _run(["git", "rev-parse", "--verify", "--quiet", args.base]).returncode != 0:
        sys.exit(f"error: base ref '{args.base}' does not resolve (try --fetch).")

    base_branch = base_branch_name(args.base)
    worktrees = parse_worktrees()
    for wt in worktrees:
        classify(wt, args.base, cwd, base_branch)

    # Report, grouped by classification for scannability.
    order = [MERGED_CLEAN, MERGED_DIRTY, UNMERGED, LOCKED, DETACHED, PROTECTED]
    width = max((len(str(w.path)) for w in worktrees), default=0)
    for label in order:
        group = [w for w in worktrees if w.classification == label]
        if not group:
            continue
        print(f"\n{label}  ({len(group)})")
        for wt in group:
            branch = wt.branch or "(detached)"
            print(f"  {str(wt.path):<{width}}  {branch:<45}  {wt.note}")

    candidates = [w for w in worktrees if w.classification == MERGED_CLEAN]
    print(
        f"\nSummary: {len(candidates)} MERGED-CLEAN of {len(worktrees)} worktrees "
        f"(base {args.base})."
    )

    if not args.delete:
        if candidates:
            print("Report-only. Re-run with --delete to remove the MERGED-CLEAN worktrees.")
        return 0

    if not candidates:
        return 0

    print(f"\nRemoving {len(candidates)} MERGED-CLEAN worktree(s):")
    failures = 0
    for wt in candidates:
        if not remove_worktree(wt):
            failures += 1
    _run(["git", "worktree", "prune"], cwd=root)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
