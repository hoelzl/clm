"""Regenerate ``tests/slides/sync_sweep_baseline.json`` (#1130).

Runs every case of the reorder x edit sweep and records the ones that do not
converge, with their failure kind. Run it after a sync change that makes
known failures converge (the ratchet test tells you to); review the diff, which
should only ever shrink.

Usage: python scripts/update_sync_sweep_baseline.py [--jobs N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.slides.sync_sweep import BASELINE_PATH, Case, cases, run_case  # noqa: E402


def _status(case: Case) -> tuple[str, str]:
    return case.id, run_case(case)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=None, help="worker processes")
    args = parser.parse_args()
    all_cases = [c for domain in ("four", "five") for c in cases(domain)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = dict(pool.map(_status, all_cases, chunksize=16))
    failures = {k: v for k, v in sorted(results.items()) if v != "converged"}
    BASELINE_PATH.write_text(json.dumps(failures, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(all_cases)} cases: {dict(Counter(results.values()))}")
    print(f"wrote {len(failures)} baseline entries to {BASELINE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
