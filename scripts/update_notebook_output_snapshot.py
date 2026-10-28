"""Record the notebook-output epoch decision and refresh its snapshot (issue #1109).

The guard test ``tests/core/operations/test_notebook_output_epoch.py`` fails
when a source listed in ``NOTEBOOK_OUTPUT_SOURCES`` changes. Notebook caches
survive clm upgrades until ``NOTEBOOK_OUTPUT_EPOCH`` is bumped, so this is a
real decision with lopsided costs:

* a needless bump costs every course one full notebook re-execution;
* a missed bump ships stale notebook output **silently**.

So **bump unless you can say why the change cannot affect executed or
rendered notebook output** (pure refactors, logging, typing, docstrings and
comments are the usual keep cases)::

    python scripts/update_notebook_output_snapshot.py --bump
    python scripts/update_notebook_output_snapshot.py --keep --reason "logging only"

The decision and its reason are written into the snapshot, so they show up in
the diff for review. Commit the snapshot (and, after ``--bump``, the epoch
change) together with the code change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
EPOCH_MODULE = SRC_ROOT / "clm" / "core" / "operations" / "process_notebook.py"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "core" / "operations" / "notebook_output_snapshot.json"
_EPOCH_RE = re.compile(r"^NOTEBOOK_OUTPUT_EPOCH = (\d+)$", re.MULTILINE)


def current_source_hashes(src_root: Path = SRC_ROOT) -> dict[str, str]:
    """sha256 of every guarded source, newline-normalized, keyed by POSIX path."""
    from clm.core.operations.process_notebook import NOTEBOOK_OUTPUT_SOURCES

    hashes: dict[str, str] = {}
    for pattern in NOTEBOOK_OUTPUT_SOURCES:
        for path in sorted(src_root.glob(pattern)):
            if not path.is_file():
                continue
            data = path.read_bytes().replace(b"\r\n", b"\n")
            hashes[path.relative_to(src_root).as_posix()] = hashlib.sha256(data).hexdigest()
    return dict(sorted(hashes.items()))


def read_epoch() -> int:
    """The epoch as written in the source (not the possibly stale imported value)."""
    match = _EPOCH_RE.search(EPOCH_MODULE.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"Cannot find 'NOTEBOOK_OUTPUT_EPOCH = <int>' in {EPOCH_MODULE}")
    return int(match.group(1))


def bump_epoch() -> int:
    """Increment the epoch in place, preserving the file's line endings."""
    raw = EPOCH_MODULE.read_bytes().decode("utf-8")
    new_epoch = read_epoch() + 1
    updated = _EPOCH_RE.sub(f"NOTEBOOK_OUTPUT_EPOCH = {new_epoch}", raw, count=1)
    EPOCH_MODULE.write_bytes(updated.encode("utf-8"))
    return new_epoch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument(
        "--bump",
        action="store_true",
        help="The change can alter notebook output: increment NOTEBOOK_OUTPUT_EPOCH.",
    )
    decision.add_argument(
        "--keep",
        action="store_true",
        help="The change cannot alter notebook output: keep the epoch (needs --reason).",
    )
    parser.add_argument(
        "--reason", default="", help="Why the change is output-neutral (required with --keep)."
    )
    args = parser.parse_args(argv)
    args.reason = args.reason.strip()
    if args.keep and not args.reason:
        parser.error("--keep requires --reason: say why the change cannot affect notebook output")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sys.path.insert(0, str(SRC_ROOT))
    epoch = bump_epoch() if args.bump else read_epoch()
    snapshot = {
        "epoch": epoch,
        "decision": {"action": "bump" if args.bump else "keep", "reason": args.reason},
        "sources": current_source_hashes(),
    }
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    action = f"bumped the epoch to {epoch}" if args.bump else f"kept epoch {epoch}"
    print(f"{action}; wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
