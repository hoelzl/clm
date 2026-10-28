"""Refresh the notebook-output epoch snapshot (issue #1109).

The guard test ``tests/core/operations/test_notebook_output_epoch.py`` fails
when a source listed in ``NOTEBOOK_OUTPUT_SOURCES`` changes. Decide first:

* the change can alter executed/rendered notebook output → bump
  ``NOTEBOOK_OUTPUT_EPOCH`` in ``src/clm/core/operations/process_notebook.py``;
* it cannot (refactor, logging, typing, docstrings) → leave the epoch alone.

Then run this script and commit the refreshed snapshot together with the
change::

    python scripts/update_notebook_output_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "core" / "operations" / "notebook_output_snapshot.json"


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


def build_snapshot() -> dict[str, object]:
    from clm.core.operations.process_notebook import NOTEBOOK_OUTPUT_EPOCH

    return {"epoch": NOTEBOOK_OUTPUT_EPOCH, "sources": current_source_hashes()}


def main() -> int:
    sys.path.insert(0, str(SRC_ROOT))
    snapshot = build_snapshot()
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {SNAPSHOT_PATH.relative_to(REPO_ROOT)} "
        f"(epoch {snapshot['epoch']}, {len(snapshot['sources'])} sources)"  # type: ignore[arg-type]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
