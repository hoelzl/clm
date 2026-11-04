"""Brute-force sync probe: run it, compare two runs, or gate a change (#1130).

The probe (``tests/slides/sync_probe.py``) drives ``report -> apply`` through
the real pre-write verify over about 47,000 deck shapes (``sync_probe.py`` plus ``sync_probe_ext.py``), wider than the
ratcheted sweep in ``tests/slides/sync_sweep.py``. Too slow for CI, so it is
run by hand before merging a sync-engine change:

    python scripts/sync_probe.py gate [--base origin/master] [--workers 12]

``gate`` extracts the base ref's ``src/`` into a temporary directory, runs
the probe on it and on the working tree, and compares the two per case. A
case gets WORSE when it moves down this ladder:

    converged  >  a visible failure (livelock, question, stall, error)
               >  a silent failure (misplaced, lost or duplicated, ...)

It exits 1 if any case got worse. ``--allow-visible`` only fails on cases
that became silent failures. Use it when a change deliberately trades a
convergence that went through a lossy pass for a visible stop, and say so in
the PR.

The other subcommands:

    python scripts/sync_probe.py run OUT.json [--families a,b] [--workers N]
    python scripts/sync_probe.py compare BASE.json NEW.json [--show N]

``run`` uses whichever ``clm`` is importable (prepend another ``src/`` to
PYTHONPATH to probe it).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.slides import sync_probe, sync_probe_ext  # noqa: E402
from tests.slides.sync_probe import SILENT, engine_file, rank  # noqa: E402

#: Every family: the base probe's and the extended ones (#1144 review).
FAMILIES = {**sync_probe.FAMILIES, **sync_probe_ext.FAMILIES}


def _work(case: object) -> tuple[str, str, str, str, list, dict, float]:
    t = time.time()
    try:
        r = sync_probe_ext.run_any(case)
        return case.id, case.family, r.status, r.detail, r.passes, r.questions, time.time() - t
    except Exception as exc:  # noqa: BLE001 - a crash is an outcome to report
        return case.id, case.family, "harness_exception", repr(exc)[:300], [], {}, time.time() - t


def run(out: Path, families: list[str], workers: int) -> int:
    print("engine:", engine_file(), flush=True)
    cases: list = []
    for name in families:
        found = list(dict.fromkeys(FAMILIES[name]()))
        print(f"{name}: {len(found)} cases", flush=True)
        cases.extend(found)
    t0 = time.time()
    results: dict[str, dict] = {}
    with Pool(workers) as pool:
        for cid, fam, status, detail, passes, questions, dt in pool.imap_unordered(
            _work, cases, chunksize=8
        ):
            results[cid] = {
                "family": fam,
                "status": status,
                "detail": detail,
                "passes": passes,
                "questions": questions,
                "t": round(dt, 3),
            }
    out.write_text(json.dumps({"engine": engine_file(), "results": results}), encoding="utf-8")
    counts = Counter(v["status"] for v in results.values())
    print(f"{len(results)} cases in {time.time() - t0:.0f}s: {dict(counts)}")
    return 0


def compare(base_path: Path, new_path: Path, show: int, allow_visible: bool) -> int:
    base_doc = json.loads(base_path.read_text(encoding="utf-8"))
    new_doc = json.loads(new_path.read_text(encoding="utf-8"))
    print("base engine:", base_doc["engine"])
    print("new engine: ", new_doc["engine"])
    base, new = base_doc["results"], new_doc["results"]
    if set(base) != set(new):
        print("the two runs probed different cases; rerun both with the same families")
        return 2
    for fam in sorted({v["family"] for v in new.values()}):
        ids = [k for k in new if new[k]["family"] == fam]
        cb = Counter(base[k]["status"] for k in ids)
        cn = Counter(new[k]["status"] for k in ids)
        print(f"\n### {fam} ({len(ids)} cases)\n\n| outcome | base | new |\n|---|---:|---:|")
        for status in sorted(set(cb) | set(cn)):
            print(f"| {status} | {cb[status]} | {cn[status]} |")
    worse: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    better = 0
    for k in new:
        b, n = base[k]["status"], new[k]["status"]
        if rank(n) > rank(b):
            worse[(new[k]["family"], b, n)].append(k)
        elif rank(n) < rank(b):
            better += 1
    silent_worse = sum(len(v) for (_f, _b, n), v in worse.items() if n.split(":")[-1] in SILENT)
    total_worse = sum(len(v) for v in worse.values())
    print(f"\n## {better} cases better, {total_worse} worse ({silent_worse} now fail silently)")
    for (fam, b, n), ids in sorted(worse.items()):
        print(f"\n{fam}: {b} -> {n}: {len(ids)}")
        for k in ids[:show]:
            print("  CASE", k)
            if new[k]["detail"]:
                print("   detail:", new[k]["detail"][:400])
            for i, rows in enumerate(new[k]["passes"][:3]):
                print(f"   pass{i}:", rows)
    if silent_worse or (total_worse and not allow_visible):
        return 1
    return 0


def gate(base_ref: str, workers: int, show: int, allow_visible: bool, keep: Path | None) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "base.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(archive), base_ref, "src"],
            cwd=ROOT,
            check=True,
        )
        with tarfile.open(archive) as tar:
            tar.extractall(tmpdir / "base", filter="data")
        out_dir = keep or tmpdir
        base_json, new_json = out_dir / "probe_base.json", out_dir / "probe_new.json"
        script = str(Path(__file__).resolve())
        families = ["--workers", str(workers)]
        base_env = dict(os.environ)
        base_env["PYTHONPATH"] = os.pathsep.join(
            [str(tmpdir / "base" / "src"), base_env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        print(f"probing {base_ref} ...", flush=True)
        subprocess.run(
            [sys.executable, script, "run", str(base_json), *families], env=base_env, check=True
        )
        print("probing the working tree ...", flush=True)
        subprocess.run([sys.executable, script, "run", str(new_json), *families], check=True)
        return compare(base_json, new_json, show, allow_visible)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("out", type=Path)
    p_run.add_argument("--families", default=",".join(FAMILIES))
    p_run.add_argument("--workers", type=int, default=6)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("base", type=Path)
    p_cmp.add_argument("new", type=Path)
    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--base", default="origin/master")
    p_gate.add_argument("--workers", type=int, default=6)
    p_gate.add_argument(
        "--keep", type=Path, default=None, help="directory to keep both runs' JSON in"
    )
    for p in (p_cmp, p_gate):
        p.add_argument("--show", type=int, default=3, help="example cases per worsened group")
        p.add_argument(
            "--allow-visible", action="store_true", help="fail only on new silent failures"
        )
    args = parser.parse_args()
    if args.command == "run":
        return run(args.out, args.families.split(","), args.workers)
    if args.command == "compare":
        return compare(args.base, args.new, args.show, args.allow_visible)
    return gate(args.base, args.workers, args.show, args.allow_visible, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
