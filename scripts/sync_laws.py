"""Check the sync laws on the real engine, or regenerate their baseline.

The laws (``tests/slides/sync_laws.py``) need no oracle: with every question
refused, an unchanged pair stays clean, a one-sided change lands on the other
half without a question, a report without a question converges, and a sync
that asked nothing conserves cells and does not depend on which half is
which. The engine violates them today; ``tests/slides/sync_laws_baseline.json``
records the violating cases of the ``ci`` scope as a ratchet
(``tests/slides/test_sync_laws.py``).

    python scripts/sync_laws.py update [--workers N] [--accept-worse]
    python scripts/sync_laws.py run OUT.json [--scope full] [--workers N] [--model]
    python scripts/sync_laws.py compare BASE.json NEW.json [--show N]
    python scripts/sync_laws.py trace "one-sided|A x B|A x B|B A x"

``update`` reruns the ``ci`` scope and rewrites the baseline. It refuses to
write when a case that held now violates a law, or when a visible violation
became a silent one; ``--accept-worse`` overrides that for a deliberate
trade, which the PR must name.

``run`` writes every case's outcome (``--scope full`` is the wider scope,
about a minute on 16 workers), ``compare`` sets two such files side by side,
and ``trace`` replays one case and prints each pass's rows and both halves.
``--model`` checks the reference model of a replacement structural core
(``scripts/sync_model.py``) on the same cases instead of the engine.

``run`` and ``update`` use whichever ``clm`` is importable (prepend another
``src/`` to PYTHONPATH to check it). Point ``TMP`` at a fast drive.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_model import BaseMember, Cell, merge  # noqa: E402
from tests.slides import sync_laws  # noqa: E402
from tests.slides.sync_laws import BASELINE_PATH, HOLDS, IDS, Case, Run, Word, check  # noqa: E402
from tests.slides.sync_probe import engine_file  # noqa: E402

#: Violations where the engine reports success and the result is wrong.
SILENT = (
    "misplaced",
    "lost_or_duplicated",
    "halves_disagree",
    "lang_or_kind_anomaly",
    "duplicated",
    "dropped_addition",
    "fewer_copies",
    "asymmetric:cells",
)


def rank(outcome: str) -> int:
    """0 the law holds, 1 a visible violation, 2 a silent one."""
    if outcome == HOLDS:
        return 0
    return 2 if any(part in SILENT for part in outcome.split()) else 1


def model_sync(base: Word, de: Word, en: Word) -> Run:
    """The reference model in the engine's place: the same deck, as the cells
    a sync engine sees, merged with every question refused."""

    def cell(token: str, lang: str) -> Cell:
        if token.startswith("S"):
            return Cell(f"s-{token}", "slide", f"{token} {lang}", True)
        if token in IDS:
            return Cell(f"vo-{token.lower()}", "vo", f"{token} {lang}", True)
        return Cell(None, "code", f"{token} = 1", False)

    def cells(word: Word, lang: str) -> list[Cell]:
        return [cell(t, lang) for t in ("S0", *word, "S1")]

    recorded = [
        BaseMember(d.sid, d.kind, d.localized, d.body, e.body)
        for d, e in zip(cells(base, "de"), cells(base, "en"), strict=True)
    ]
    out = merge(recorded, cells(de, "de"), cells(en, "en"), lambda _question: None)
    if out.open is not None:  # nothing is written before a question
        return Run(
            "asks", out.open.kind, tuple(sync_laws.labels(de)), tuple(sync_laws.labels(en)), ()
        )
    merged = tuple(out.labels())
    return Run("clean", "", merged, merged, ())


def _work(case: Case, model: bool = False) -> tuple[str, str]:
    try:
        return case.id, check(case, model_sync) if model else check(case)
    except Exception as exc:  # noqa: BLE001 - a crash is an outcome to report
        return case.id, f"harness_exception:{type(exc).__name__}"


def _engine(model: bool) -> str:
    return str(Path(__file__).with_name("sync_model.py")) if model else engine_file()


def _run_all(scope: str, workers: int | None, model: bool = False) -> dict[str, str]:
    print("engine:", _engine(model), flush=True)
    todo = sync_laws.cases(scope)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = dict(pool.map(partial(_work, model=model), todo, chunksize=16))
    print(f"{len(todo)} cases in {time.time() - t0:.0f}s", flush=True)
    return results


def summary(results: dict[str, str]) -> None:
    by_law: dict[str, Counter] = defaultdict(Counter)
    for cid, outcome in results.items():
        by_law[cid.split("|")[0]][outcome] += 1
    for law in ("unchanged", "one-sided", "two-sided"):
        counts = by_law.get(law)
        if not counts:
            continue
        total = sum(counts.values())
        print(f"\n{law}: {total} cases, {total - counts[HOLDS]} violate a law")
        for outcome, n in counts.most_common():
            print(f"  {n:7d}  {outcome}")


def run(out: Path, scope: str, workers: int | None, model: bool) -> int:
    results = _run_all(scope, workers, model)
    out.write_text(
        json.dumps({"engine": _engine(model), "scope": scope, "results": results}),
        encoding="utf-8",
    )
    summary(results)
    return 0


def compare(base_path: Path, new_path: Path, show: int) -> int:
    base_doc = json.loads(base_path.read_text(encoding="utf-8"))
    new_doc = json.loads(new_path.read_text(encoding="utf-8"))
    print("base engine:", base_doc["engine"])
    print("new engine: ", new_doc["engine"])
    base, new = base_doc["results"], new_doc["results"]
    if set(base) != set(new):
        print("the two runs checked different cases; rerun both with the same scope")
        return 2
    moved: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid in new:
        if base[cid] != new[cid]:
            moved[(base[cid], new[cid])].append(cid)
    better = sum(len(v) for (b, n), v in moved.items() if rank(n) < rank(b))
    worse = sum(len(v) for (b, n), v in moved.items() if rank(n) > rank(b))
    silent = sum(len(v) for (b, n), v in moved.items() if rank(n) == 2 > rank(b))
    print(f"\n{better} cases better, {worse} worse ({silent} now fail silently)")
    for (b, n), ids in sorted(moved.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{b} -> {n}: {len(ids)}")
        for cid in ids[:show]:
            print("  ", cid)
    return 1 if worse else 0


def update(workers: int | None, accept_worse: bool) -> int:
    results = _run_all("ci", workers)
    summary(results)
    old = sync_laws.load_baseline() if BASELINE_PATH.exists() else None
    if old is not None:
        worse = sorted(
            (cid, old.get(cid, HOLDS), outcome)
            for cid, outcome in results.items()
            if rank(outcome) > rank(old.get(cid, HOLDS))
        )
        if worse:
            print(f"\n{len(worse)} cases got worse than the committed baseline:")
            for cid, before, after in worse[:30]:
                print(f"  {cid}: {before} -> {after}")
            if not accept_worse:
                print("not written (pass --accept-worse for a deliberate trade)")
                return 1
    violations = {k: v for k, v in sorted(results.items()) if v != HOLDS}
    BASELINE_PATH.write_text(
        json.dumps(violations, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\nwrote {len(violations)} baseline entries to {BASELINE_PATH.relative_to(ROOT)}")
    return 0


def trace(case_id: str, model: bool) -> int:
    case = Case.parse(case_id)
    sync = model_sync if model else sync_laws.run
    orientations = [(case.de, case.en)]
    if case.law == "two-sided" and case.de != case.en:
        orientations.append((case.en, case.de))
    for de, en in orientations:
        result = sync(case.base, de, en)
        shown = (" ".join(w) or "-" for w in (case.base, de, en))
        print("recorded {}; DE {}; EN {}".format(*shown))
        for n, rows in enumerate(result.passes):
            print(f"  pass {n}:")
            for key, action in rows:
                print(f"    {key} {action}")
        print(f"  {result.outcome}")
        print(f"  -> DE: {' '.join(result.de)}")
        print(f"     EN: {' '.join(result.en)}")
    print(check(case, sync))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_update = sub.add_parser("update")
    p_update.add_argument("--accept-worse", action="store_true")
    p_run = sub.add_parser("run")
    p_run.add_argument("out", type=Path)
    p_run.add_argument("--scope", default="full", choices=sorted(sync_laws.SCOPES))
    for p in (p_update, p_run):
        p.add_argument("--workers", type=int, default=None, help="worker processes")
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("base", type=Path)
    p_cmp.add_argument("new", type=Path)
    p_cmp.add_argument("--show", type=int, default=3, help="example cases per group")
    p_trace = sub.add_parser("trace")
    p_trace.add_argument("case_id")
    for p in (p_run, p_trace):
        p.add_argument("--model", action="store_true", help="check the reference model instead")
    args = parser.parse_args()
    if args.command == "update":
        return update(args.workers, args.accept_worse)
    if args.command == "run":
        return run(args.out, args.scope, args.workers, args.model)
    if args.command == "compare":
        return compare(args.base, args.new, args.show)
    return trace(args.case_id, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
