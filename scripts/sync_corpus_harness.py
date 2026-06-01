#!/usr/bin/env python3
"""Read-only ``clm slides sync`` corpus harness for Issue #190 (Phase 0).

Two jobs over the real PythonCourses split decks (≈212 ``*.de.py`` / ``*.en.py``
pairs, ≈20k cells):

1. **No-op invariant** — the regression backstop every later #190 phase must
   preserve. For a pair that is already in *post-sync-clean* shape, a clean
   re-run of the sync engine must write **zero bytes** and make **zero LLM
   calls**. We establish "already synced" by seeding the structural watermark
   from the pair's *current* state, then run the real
   :func:`~clm.slides.sync_plan.build_sync_plan` + :func:`~clm.slides.sync_apply.apply_plan`
   over a **temp copy** with a **counting, no-LLM** translator/judge, and check
   the bytes are unchanged and no translate/judge call fired.

2. **Churn baseline** — the measurement Phases 2-3 are proven against. A static
   census of the cell populations the two *serious* #190 limitations expose:

   * **item 2** (a code-only change is not propagated): *language-neutral*
     cells, which produce no proposal when edited alone — so a sync silently
     drops the edit and leaves the split halves byte-divergent;
   * **item 3** (an unchanged localized code cell is re-translated): *id-less
     localized* cells, which a group rebuild re-translates because their
     ``("L", kind)`` structural signature cannot prove they are unchanged — plus
     the per-group *blast radius* (how many such cells one sibling-triggered
     rebuild re-translates).

This **never mutates the course repo**: every probe runs on a temp copy with a
temp watermark DB, and the translator/judge are mocked (no network).

Usage::

    python scripts/sync_corpus_harness.py [--corpus DIR] [--json] [--limit N]

The corpus dir defaults to ``$CLM_SYNC_CORPUS_DIR``, then the known PythonCourses
``slides`` path. Exits 0 when the no-op invariant holds for every no-op-shaped
pair; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Allow running both as a script (``python scripts/sync_corpus_harness.py``) and
# as an importable module from the pytest backstop.
if __package__ in (None, ""):  # pragma: no cover - import shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clm.infrastructure.llm.cache import SyncWatermarkCache  # noqa: E402
from clm.infrastructure.llm.ollama_client import SyncProposal  # noqa: E402
from clm.notebooks.slide_parser import Cell, parse_cells  # noqa: E402
from clm.slides.sync_apply import apply_plan  # noqa: E402
from clm.slides.sync_plan import build_sync_plan, watermark_rows  # noqa: E402
from clm.slides.sync_writeback import role_of  # noqa: E402

# Default corpus location (overridable via --corpus or $CLM_SYNC_CORPUS_DIR).
_DEFAULT_CORPUS = Path(r"C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides")


# ---------------------------------------------------------------------------
# Counting (no-LLM) translator / judge
# ---------------------------------------------------------------------------


@dataclass
class CountingTranslator:
    """Deterministic stand-in translator that records every call (no network).

    Returns the source body verbatim — enough to drive the apply path while we
    only care about *how many* translations a sync would trigger. A non-zero
    call count on a supposedly-no-op pair is exactly the item-3 churn we measure.
    """

    prompt_version: str = "counting"
    calls: list[tuple[str, str, str, str]] = field(default_factory=list)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append((role, source_lang, target_lang, source_body))
        return source_body


@dataclass
class CountingJudge:
    """Stand-in edit judge that records every call and never rewrites (no network)."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def propose(
        self, source_body: str, target_body: str, *, source_lang: str, target_lang: str
    ) -> SyncProposal:
        self.calls.append((source_lang, target_lang))
        return SyncProposal(verdict="in_sync", proposed_text=target_body)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_pairs(root: Path) -> list[tuple[Path, Path]]:
    """Return every ``(de_path, en_path)`` split pair under ``root`` (recursive).

    A pair is a ``*.de.py`` with a sibling ``*.en.py`` of the same stem. Files
    without a counterpart are skipped (an unmatched half is not a sync pair).
    Sorted for a stable, reproducible report.
    """
    pairs: list[tuple[Path, Path]] = []
    for de_path in sorted(root.rglob("*.de.py")):
        en_path = de_path.with_name(de_path.name[: -len(".de.py")] + ".en.py")
        if en_path.exists():
            pairs.append((de_path, en_path))
    return pairs


# ---------------------------------------------------------------------------
# No-op probe
# ---------------------------------------------------------------------------


@dataclass
class NoopResult:
    """Outcome of seeding a pair's watermark from its current state and re-running."""

    name: str
    plan_is_noop: bool
    de_bytes_changed: bool
    en_bytes_changed: bool
    translate_calls: int
    judge_calls: int
    applied: int
    deferred: int
    proposal_kinds: Counter  # kind -> count (why a pair is NOT no-op)
    issue_count: int

    @property
    def is_violation(self) -> bool:
        """A *no-op-shaped* pair that nonetheless wrote bytes or called the LLM.

        This is the pure engine-invariant breach the backstop guards: a plan
        with nothing to do must apply as a true no-op. (A pair that is simply
        not yet synced — ``plan_is_noop`` False — is a data condition, reported
        separately, never a violation.)
        """
        return self.plan_is_noop and (
            self.de_bytes_changed
            or self.en_bytes_changed
            or self.translate_calls > 0
            or self.judge_calls > 0
        )


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    """Record the pair's current state as the watermark baseline.

    Mirrors ``sync_apply._record_watermark``: the membership-widened de/en/shared
    partitions, so the seeded baseline matches what a real clean apply records.
    """
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def noop_probe(de_path: Path, en_path: Path) -> NoopResult:
    """Seed a pair's watermark from its current state and re-run sync on a copy.

    Operates entirely on a temp copy with a temp watermark DB, so the corpus is
    never touched. Returns the bytes-changed / LLM-call tallies plus the plan's
    shape (so non-no-op pairs can be explained without failing the invariant).
    """
    with tempfile.TemporaryDirectory(prefix="clm-sync-noop-") as tmp:
        tmp_dir = Path(tmp)
        de_tmp = tmp_dir / de_path.name
        en_tmp = tmp_dir / en_path.name
        shutil.copyfile(de_path, de_tmp)
        shutil.copyfile(en_path, en_tmp)
        de_before = de_tmp.read_bytes()
        en_before = en_tmp.read_bytes()

        cache = SyncWatermarkCache(tmp_dir / "clm-llm.sqlite")
        translator = CountingTranslator()
        judge = CountingJudge()
        try:
            _seed_watermark(cache, de_tmp, en_tmp)
            # No git fallback: the temp copy is not a repo, and we have a
            # watermark, so the baseline is unambiguous.
            plan = build_sync_plan(de_tmp, en_tmp, watermark_cache=cache, allow_git_fallback=False)
            result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
        finally:
            cache.close()

        return NoopResult(
            name=_pair_name(de_path),
            plan_is_noop=plan.is_noop,
            de_bytes_changed=de_tmp.read_bytes() != de_before,
            en_bytes_changed=en_tmp.read_bytes() != en_before,
            translate_calls=len(translator.calls),
            judge_calls=len(judge.calls),
            applied=result.applied,
            deferred=result.deferred,
            proposal_kinds=Counter(p.kind for p in plan.proposals),
            issue_count=len(plan.issues),
        )


# ---------------------------------------------------------------------------
# Static census (item-2 / item-3 exposure populations)
# ---------------------------------------------------------------------------


@dataclass
class Census:
    """Per-corpus static cell-population counts (summed across all deck files)."""

    deck_files: int = 0
    cells_total: int = 0
    md_total: int = 0
    code_total: int = 0
    j2_total: int = 0
    any_slide_id: int = 0

    # item-2 territory: language-neutral cells (no proposal when edited alone).
    code_neutral_idless: int = 0  # the ~6,700 shared `# %%` / tags=["keep"] cells
    code_neutral_idd: int = 0  # neutral code carrying a slide_id (the def-my-fun shape)
    md_neutral_structural: int = 0  # neutral markdown with no per-cell role (structural)

    # item-3 territory: id-less localized cells (re-translated on a group rebuild).
    code_localized_idless: int = 0  # the ~740 — the headline item-3 population
    code_localized_idd: int = 0  # localized code with a slide_id (reconciled per-cell)
    md_localized_idless: int = 0  # id-less localized markdown (also rebuilt-and-translated)

    @property
    def item2_population(self) -> int:
        """Cells a code-only edit would silently fail to propagate (neutral cells)."""
        return self.code_neutral_idless + self.code_neutral_idd + self.md_neutral_structural

    @property
    def item3_population(self) -> int:
        """Cells a group rebuild would needlessly re-translate (id-less localized)."""
        return self.code_localized_idless + self.md_localized_idless


def _is_structural_localized_idless(cell: Cell) -> bool:
    """Whether ``cell`` is the id-less localized kind a rebuild re-translates.

    Mirrors the ``elif meta.lang == source_lang:`` branch of
    :func:`clm.slides.sync_code._rebuild_region`: a cell the per-cell engine does
    not own (``role_of`` is ``None``) that nonetheless carries a language — so a
    rebuild translates it even when its content is byte-unchanged.
    """
    meta = cell.metadata
    return not meta.is_j2 and role_of(meta) is None and meta.lang is not None


def census_file(path: Path, census: Census) -> None:
    """Accumulate one deck file's cell populations into ``census``."""
    census.deck_files += 1
    for cell in parse_cells(path.read_text(encoding="utf-8")):
        meta = cell.metadata
        if meta.is_j2:
            census.j2_total += 1
            census.cells_total += 1
            continue
        census.cells_total += 1
        has_id = meta.slide_id is not None
        if has_id:
            census.any_slide_id += 1
        is_code = meta.cell_type == "code"
        if is_code:
            census.code_total += 1
            if meta.lang is None:
                if has_id:
                    census.code_neutral_idd += 1
                else:
                    census.code_neutral_idless += 1
            elif has_id:
                census.code_localized_idd += 1
            else:
                census.code_localized_idless += 1
        else:
            census.md_total += 1
            if role_of(meta) is None:
                # No per-cell sync role -> handled only structurally.
                if meta.lang is None:
                    census.md_neutral_structural += 1
                else:
                    census.md_localized_idless += 1


def item3_blast_radius(path: Path) -> list[int]:
    """Per-slide-group count of id-less localized cells in one deck file.

    Each value is how many cells a single rebuild of that group would
    re-translate today (item 3). A group is a slide/subslide cell plus the cells
    up to the next one; cells before the first slide form the head.
    """
    cells = parse_cells(path.read_text(encoding="utf-8"))
    groups: list[list[Cell]] = []
    current: list[Cell] = []
    groups.append(current)  # head
    for cell in cells:
        if cell.metadata.is_slide_start:
            current = [cell]
            groups.append(current)
        else:
            current.append(cell)
    return [sum(1 for c in g if _is_structural_localized_idless(c)) for g in groups]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class CorpusReport:
    corpus: str
    total_pairs: int
    noop_pairs: int
    non_noop_pairs: int
    violations: list[str]
    non_noop_reasons: Counter  # proposal-kind -> count across non-no-op pairs
    census: Census
    blast_groups_with_idless: int  # groups exposing >=1 item-3 cell
    blast_max_in_one_group: int
    blast_total_cells: int

    def to_dict(self) -> dict:
        c = self.census
        return {
            "corpus": self.corpus,
            "pairs": {
                "total": self.total_pairs,
                "noop": self.noop_pairs,
                "non_noop": self.non_noop_pairs,
                "non_noop_reasons": dict(self.non_noop_reasons),
                "violations": self.violations,
            },
            "census": {
                "deck_files": c.deck_files,
                "cells_total": c.cells_total,
                "md_total": c.md_total,
                "code_total": c.code_total,
                "j2_total": c.j2_total,
                "any_slide_id": c.any_slide_id,
                "item2_population": c.item2_population,
                "item2_breakdown": {
                    "code_neutral_idless": c.code_neutral_idless,
                    "code_neutral_idd": c.code_neutral_idd,
                    "md_neutral_structural": c.md_neutral_structural,
                },
                "item3_population": c.item3_population,
                "item3_breakdown": {
                    "code_localized_idless": c.code_localized_idless,
                    "md_localized_idless": c.md_localized_idless,
                    "code_localized_idd": c.code_localized_idd,
                },
            },
            "item3_blast_radius": {
                "groups_with_idless": self.blast_groups_with_idless,
                "max_in_one_group": self.blast_max_in_one_group,
                "total_cells": self.blast_total_cells,
            },
        }

    def render(self) -> str:
        c = self.census
        lines = [
            f"sync corpus harness — {self.corpus}",
            "",
            "NO-OP INVARIANT",
            f"  pairs                : {self.total_pairs}",
            f"  already-synced (noop): {self.noop_pairs}",
            f"  not-yet-synced       : {self.non_noop_pairs}"
            + (f"  reasons={dict(self.non_noop_reasons)}" if self.non_noop_reasons else ""),
            f"  invariant violations : {len(self.violations)}",
        ]
        for v in self.violations:
            lines.append(f"     ! {v}")
        lines += [
            "",
            "CENSUS (summed across all deck files)",
            f"  deck files           : {c.deck_files}",
            f"  cells (total)        : {c.cells_total}  (md={c.md_total} code={c.code_total} j2={c.j2_total})",
            f"  cells with slide_id  : {c.any_slide_id}",
            "",
            "ITEM 2 — code-only change not propagated (neutral cells, silent drop)",
            f"  exposure population  : {c.item2_population}",
            f"    code neutral idless: {c.code_neutral_idless}",
            f"    code neutral id'd  : {c.code_neutral_idd}",
            f"    md   neutral struct: {c.md_neutral_structural}",
            "",
            "ITEM 3 — unchanged localized code re-translated on rebuild",
            f"  exposure population  : {c.item3_population}",
            f"    code localized idl.: {c.code_localized_idless}",
            f"    md   localized idl.: {c.md_localized_idless}",
            f"    (code localized id'd, reconciled per-cell: {c.code_localized_idd})",
            f"  blast radius         : {self.blast_groups_with_idless} groups expose >=1 "
            f"(max {self.blast_max_in_one_group} in one group; {self.blast_total_cells} cells total)",
        ]
        return "\n".join(lines)


def run(root: Path, *, limit: int | None = None) -> CorpusReport:
    """Run the no-op probe + census over the corpus and aggregate a report."""
    pairs = discover_pairs(root)
    if limit is not None:
        pairs = pairs[:limit]

    noop_pairs = 0
    non_noop = 0
    violations: list[str] = []
    reasons: Counter = Counter()
    census = Census()
    blast_groups = 0
    blast_max = 0
    blast_total = 0

    for de_path, en_path in pairs:
        res = noop_probe(de_path, en_path)
        if res.plan_is_noop:
            noop_pairs += 1
        else:
            non_noop += 1
            reasons.update(res.proposal_kinds)
        if res.is_violation:
            violations.append(
                f"{res.name}: noop plan but de_changed={res.de_bytes_changed} "
                f"en_changed={res.en_bytes_changed} translate={res.translate_calls} "
                f"judge={res.judge_calls}"
            )
        for path in (de_path, en_path):
            census_file(path, census)
            for count in item3_blast_radius(path):
                if count > 0:
                    blast_groups += 1
                    blast_total += count
                    blast_max = max(blast_max, count)

    return CorpusReport(
        corpus=str(root),
        total_pairs=len(pairs),
        noop_pairs=noop_pairs,
        non_noop_pairs=non_noop,
        violations=violations,
        non_noop_reasons=reasons,
        census=census,
        blast_groups_with_idless=blast_groups,
        blast_max_in_one_group=blast_max,
        blast_total_cells=blast_total,
    )


def _pair_name(de_path: Path) -> str:
    return de_path.name[: -len(".de.py")]


def resolve_corpus(cli_value: str | None) -> Path:
    """Corpus dir: --corpus, then $CLM_SYNC_CORPUS_DIR, then the known default."""
    if cli_value:
        return Path(cli_value)
    env = os.environ.get("CLM_SYNC_CORPUS_DIR")
    if env:
        return Path(env)
    return _DEFAULT_CORPUS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help="corpus root (default: $CLM_SYNC_CORPUS_DIR or the PythonCourses slides path)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a human report")
    parser.add_argument(
        "--limit", type=int, default=None, help="probe only the first N pairs (quick run)"
    )
    args = parser.parse_args(argv)

    root = resolve_corpus(args.corpus)
    if not root.is_dir():
        print(f"corpus not found: {root}", file=sys.stderr)
        return 2

    report = run(root, limit=args.limit)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 1 if report.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
