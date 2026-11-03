"""Regenerate ``tests/slides/sync_sweep_baseline.json`` (#1130).

Runs every case of the reorder x edit sweep and records the ones that do not
converge, with their failure kind. Run it after a sync change that makes
known failures converge (the ratchet test tells you to).

It refuses to write when a case got WORSE than the committed baseline:
converged -> a failure, or a visible failure -> a silent one (``misplaced``,
``lost_or_duplicated``). Regenerating used to absorb such regressions. Pass
``--accept-worse`` only for a deliberate trade, and name the cases in the PR.

Usage:
    python scripts/update_sync_sweep_baseline.py [--jobs N] [--accept-worse]
    python scripts/update_sync_sweep_baseline.py --summary [--jobs N]
    python scripts/update_sync_sweep_baseline.py --trace "five|en|a b x c1 c2|x"

``--summary`` groups the failures by kind and by the actions of their last
pass (what a fix has to change), without writing. ``--trace`` replays one
case and prints each pass's rows, what the pre-write verify withheld, and
both halves' cell order after the pass.
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

from tests.slides.sync_probe import rank  # noqa: E402
from tests.slides.sync_sweep import (  # noqa: E402
    BASELINE_PATH,
    Case,
    cases,
    final_order,
    load_baseline,
    run_case,
)


def _status(case: Case) -> tuple[str, str, str]:
    status, passes = run_case(case)
    last = " | ".join(sorted({action for _key, action in passes[-1]})) if passes else ""
    return case.id, status, last


def _parse(case_id: str) -> Case:
    domain, side, order, edits = case_id.split("|")
    return Case(domain, side, tuple(order.split()), tuple(e for e in edits.split(",") if e))


def trace(case_id: str) -> int:
    case = _parse(case_id)

    def show(index: int, rows: list, payload: dict, de: str, en: str) -> None:
        print(f"pass {index}:")
        for key, action in rows:
            print(f"  {key} {action}")
        withheld = payload.get("verify_withheld") or []
        if withheld:
            print(f"  withheld: {', '.join(withheld)}")
            for message in payload.get("verify_violations") or []:
                print(f"    {message}")
        print(f"  -> DE: {' '.join(final_order(de, case))}")
        print(f"     EN: {' '.join(final_order(en, case))}")

    status, _passes = run_case(case, trace=show)
    print(f"{status} (EN's intended order: {' '.join(case.order)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--jobs", type=int, default=None, help="worker processes")
    parser.add_argument("--accept-worse", action="store_true", help="write despite regressions")
    parser.add_argument("--summary", action="store_true", help="group failures; write nothing")
    parser.add_argument("--trace", metavar="CASE_ID", help="replay one case pass by pass")
    args = parser.parse_args()
    if args.trace:
        return trace(args.trace)
    all_cases = [c for domain in ("four", "five") for c in cases(domain)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(_status, all_cases, chunksize=16))
    results = {cid: status for cid, status, _last in rows}
    print(f"{len(all_cases)} cases: {dict(Counter(results.values()))}")
    if args.summary:
        groups = Counter((status, last) for _cid, status, last in rows if status != "converged")
        for (status, last), n in groups.most_common():
            print(f"  {n:5d}  {status}: {last}")
        return 0
    old = load_baseline()
    worse = sorted(
        (cid, old.get(cid, "converged"), status)
        for cid, status in results.items()
        if rank(status) > rank(old.get(cid, "converged"))
    )
    if worse:
        print(f"{len(worse)} cases got worse than the committed baseline:")
        for cid, before, after in worse[:30]:
            print(f"  {cid}: {before} -> {after}")
        if not args.accept_worse:
            print("not written (pass --accept-worse for a deliberate trade)")
            return 1
    failures = {k: v for k, v in sorted(results.items()) if v != "converged"}
    BASELINE_PATH.write_text(json.dumps(failures, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(failures)} baseline entries to {BASELINE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
