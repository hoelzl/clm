"""Where does sync ceremony actually live? Measured from a course repo's history.

Companion to ``measure_positional_composition.py`` (which asks what a *cold*
deck costs). This one asks what *ongoing authoring* costs: it replays real
commits and classifies every changed cell by the row it would frame.

The distinction that matters, and that a naive count gets wrong: a cosmetic edit
to a **shared** cell already resolves mechanically (``propagate_shared_edit``),
and an identical edit to both halves is ``record_symmetric_edit``. Neither is
ceremony. Only a **localized** member changed on **exactly one** half frames
``translate_edit`` — a question. Counting all changed cells overstates the
addressable population by ~25x (5.1% vs 0.2% on the reference repo).

Three questions it answers:

1. **Ceremony profile** — how the framed rows split between ``translate_edit``
   (one half moved) and ``verify_translation`` (both moved apart).
2. **The normalizer-equivalence candidate** (review Q6b) — of the real
   ``translate_edit`` rows, how many are *cosmetic*: the source-side change is
   normalizer-equivalent, so the twin is provably unaffected and the row could
   resolve mechanically. v3 fingerprints are raw bytes, so unlike v1/v2 (which
   hashed through ``normalize_for_hash``, #429) a pure re-wrap does read as
   drift — the mechanism is real; this measures whether the population is.
3. **Batching pressure** — how often one deck frames several one-sided edits at
   once, and how often those all sit on the same side (what the
   ``uniform_drift_side`` observation fires on).

4. **Recovery rate** (``--recovery``, #773 phase 1's step-2 re-measurement) —
   against the repo's *live* working tree and committed ledgers: of the
   ``verify_translation`` / ``translate_edit`` rows the report frames right
   now, how many recover a committed base inside the walk cap (i.e. would
   ship ``base_ref``/``de_diff``/``en_diff``). This is a different lens from
   the commit replay above — the replay's "base" is a parent commit by
   construction, so measuring recovery there would be circular.

Usage::

    python scripts/measure_sync_ceremony.py <course-repo> [commit-limit] [--since DATE]
    python scripts/measure_sync_ceremony.py <course-repo> --recovery
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

from clm.notebooks.slide_parser import comment_token_for_path
from clm.slides.raw_cells import split_cells
from clm.slides.sync_writeback import normalize_for_hash

#: A deck frames this many one-sided edits before answering them row-by-row is
#: worth calling ceremony. Matches ``_UNIFORM_DRIFT_MIN`` in ``sync_diff``.
BATCH_THRESHOLD = 3


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout


def _cells(text: str, token: str) -> dict[str, tuple[str, bool]]:
    """``key -> (raw cell text, is_localized)``; id'd cells key by id, else by ordinal."""
    try:
        _preamble, cells = split_cells(text, token)
    except Exception:  # noqa: BLE001 - a mid-history parse failure just skips the file
        return {}
    out: dict[str, tuple[str, bool]] = {}
    for index, cell in enumerate(cells):
        sid = cell.metadata.slide_id
        key = f"id:{sid}" if sid else f"pos:{index}"
        out[key] = ("\n".join(cell.lines), 'lang="' in cell.lines[0])
    return out


def _body(cell_text: str) -> str:
    return "\n".join(cell_text.split("\n")[1:])


def main(repo: Path, limit: int, since: str) -> int:
    commits = _git(
        repo,
        "log",
        f"--since={since}",
        "--format=%H",
        "--",
        "slides/**/*.de.py",
        "slides/**/*.en.py",
    ).split()[:limit]

    stats: Counter = Counter()
    per_deck: list[tuple[int, bool]] = []
    cosmetic_samples: list[tuple[str, str]] = []

    for sha in commits:
        touched = [
            f
            for f in _git(
                repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha
            ).splitlines()
            if f.startswith("slides/") and f.endswith((".de.py", ".en.py"))
        ]
        for stem in sorted({f.rsplit(".", 2)[0] for f in touched}):
            de_path, en_path = f"{stem}.de.py", f"{stem}.en.py"
            token = comment_token_for_path(Path(de_path))
            before_de = _git(repo, "show", f"{sha}~1:{de_path}")
            after_de = _git(repo, "show", f"{sha}:{de_path}")
            before_en = _git(repo, "show", f"{sha}~1:{en_path}")
            after_en = _git(repo, "show", f"{sha}:{en_path}")
            if not all((before_de, after_de, before_en, after_en)):
                continue  # deck created or deleted in this commit

            a_de, b_de = _cells(before_de, token), _cells(after_de, token)
            a_en, b_en = _cells(before_en, token), _cells(after_en, token)
            edits = 0
            sides: set[str] = set()

            for key in set(a_de) & set(b_de) & set(a_en) & set(b_en):
                de_moved = a_de[key][0] != b_de[key][0]
                en_moved = a_en[key][0] != b_en[key][0]
                if not (de_moved or en_moved):
                    continue
                if not (b_de[key][1] and b_en[key][1]):
                    stats["shared member moved -> already mechanical"] += 1
                    continue
                if de_moved and en_moved:
                    stats["localized, BOTH halves moved -> verify_translation"] += 1
                    continue
                stats["localized, ONE half moved -> translate_edit"] += 1
                edits += 1
                sides.add("de" if de_moved else "en")
                old, new = (a_de, b_de) if de_moved else (a_en, b_en)
                if normalize_for_hash(_body(old[key][0]), token) == normalize_for_hash(
                    _body(new[key][0]), token
                ):
                    stats["  ...COSMETIC (normalizer-equivalent)"] += 1
                    if len(cosmetic_samples) < 5:
                        cosmetic_samples.append((Path(stem).name, key))
            if edits:
                per_deck.append((edits, len(sides) == 1))

    framed = (
        stats["localized, ONE half moved -> translate_edit"]
        + stats["localized, BOTH halves moved -> verify_translation"]
    )
    print(f"commits replayed : {len(commits)}  (since {since})")
    print(f"framed rows      : {framed}\n")
    for key, n in stats.most_common():
        share = f"{100 * n / max(framed, 1):5.1f}%" if not key.startswith(" ") else ""
        print(f"  {key:<50} {n:>6} {share}")

    edits = stats["localized, ONE half moved -> translate_edit"]
    cosmetic = stats["  ...COSMETIC (normalizer-equivalent)"]
    if edits:
        print(
            f"\nnormalizer-equivalence candidate: {cosmetic}/{edits} "
            f"= {100 * cosmetic / edits:.1f}% of translate_edit rows"
        )
    if per_deck:
        counts = [n for n, _ in per_deck]
        batched = [(n, uniform) for n, uniform in per_deck if n >= BATCH_THRESHOLD]
        print(
            f"\nbatching: median {statistics.median(counts):.0f} translate_edit rows per "
            f"(commit, deck), max {max(counts)}"
        )
        print(
            f"  pairs framing >= {BATCH_THRESHOLD}: {len(batched)} of {len(per_deck)}; "
            f"of those, {sum(u for _n, u in batched)} have every edit on ONE side "
            f"(what `uniform_drift_side` fires on)"
        )
    if cosmetic_samples:
        print("\ncosmetic samples:")
        for name, key in cosmetic_samples:
            print(f"  {name} {key}")
    return 0


def recovery_mode(repo: Path) -> int:
    """The live #773 measurement: how many framed rows recover a base today.

    Imports are local so the replay path stays importable without the full
    engine stack loaded up front.
    """
    from clm.slides.base_recovery import BASE_DIFF_ACTIONS, batch_observation, recover_base_diffs
    from clm.slides.doc_lenses import DocLensError, load_bundle
    from clm.slides.doc_report import diff_bundle
    from clm.slides.pairing import find_split_slide_files_recursive, iter_split_pairs

    pairs, _solos = iter_split_pairs(find_split_slide_files_recursive(repo / "slides"))
    stats: Counter = Counter()
    decks_with_rows = 0
    batches = 0
    for de, en in pairs:
        try:
            bundle = load_bundle(de, en)
        except DocLensError:
            stats["pairs skipped (load error)"] += 1
            continue
        diff = diff_bundle(bundle)
        # The same filter recover_base_diffs applies — a row it never attempts
        # (no recorded fp on either side) must not count as "NOT recovered".
        targets = [
            i
            for i in diff.items
            if i.action in BASE_DIFF_ACTIONS
            and i.base is not None
            and not (i.base.de_fp is None and i.base.en_fp is None)
        ]
        if not targets:
            continue
        decks_with_rows += 1
        recovered = recover_base_diffs(bundle, diff)
        for item in targets:
            verdict = "recovered" if item.key in recovered else "NOT recovered"
            stats[f"{item.action} {verdict}"] += 1
        if batch_observation(diff, recovered) is not None:
            batches += 1
    print(f"pairs scanned    : {len(pairs)}")
    print(f"decks with rows  : {decks_with_rows}")
    for key, n in sorted(stats.items()):
        print(f"  {key:<40} {n:>6}")
    for action in ("verify_translation", "translate_edit"):
        hit, miss = stats[f"{action} recovered"], stats[f"{action} NOT recovered"]
        if hit + miss:
            print(f"{action}: {hit}/{hit + miss} = {100 * hit / (hit + miss):.1f}% recovered")
    print(f"verify_translation_batch observations: {batches}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    argv = sys.argv[1:]
    if "--recovery" in argv:
        argv.remove("--recovery")
        if not argv:
            print(__doc__)
            raise SystemExit(2)
        raise SystemExit(recovery_mode(Path(argv[0])))
    since = "2025-08-01"
    if "--since" in argv:
        i = argv.index("--since")
        since = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    raise SystemExit(main(Path(argv[0]), int(argv[1]) if len(argv) > 1 else 200, since))
