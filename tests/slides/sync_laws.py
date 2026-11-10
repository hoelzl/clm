"""Laws of bilingual sync that need no oracle, checked on the real engine.

The sweep (``sync_sweep.py``) and the probe (``sync_probe.py``) need an
oracle: every case states what the author intended, and a harness answers the
questions "as a careful agent would". The laws here hold for *every* input and
are checked with every question refused, so every small deck can be tried and
nobody has to say what was intended.

A deck is a *word*, wrapped in two slides: ``A`` and ``B`` are id'd voiceover
cells (localized), ``x``, ``y`` and ``z`` are un-id'd shared code cells with
that content. A content may repeat, so byte-identical cells are covered. A
case records a base word on both halves, rewrites one or both halves to
another word over the same id'd cells, and drives ``report -> apply`` through
the real pre-write verify until the report is clean, a row needs an answer,
or :data:`MAX_PASSES` runs out.

The laws:

1. **unchanged**   nothing changed since the record: the report is clean.
2. **one-sided**   only one half changed: no question, and both halves end as
                   the changed half, cell for cell. Which half is primary
                   plays no part when the other one is exactly what the last
                   sync left.
3. **progress**    a report without a question converges: applying it, and
                   what follows, ends in a clean report.
4. **conserving**  a sync that asked nothing leaves the two halves equal,
                   holds no more copies of a cell than a half held, no fewer
                   than both halves held, and drops no cell a half added.
5. **symmetric**   swapping the two halves changes neither whether the engine
                   asks nor the cells of the result. (Their order may differ:
                   ties between unrelated additions are broken German first.)

Laws 3 to 5 are checked on cases where both halves changed.

The engine does not satisfy these today. ``sync_laws_baseline.json`` records
the cases that violate one, as a ratchet (``test_sync_laws.py``); the full
scope is run by hand with ``scripts/sync_laws.py``. The reference model of a
replacement structural core satisfies all five on every one of these inputs
(``scripts/sync_laws.py run --model``; background in
``docs/claude/sync-structural-core-investigation.md``).
"""

from __future__ import annotations

import itertools
import json
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from clm.slides import doc_apply
from tests.slides.sync_probe import HEADER, _apply, _diff, _record, parse_labels
from tests.slides.sync_probe import cell_text as _probe_cell

BASELINE_PATH = Path(__file__).with_name("sync_laws_baseline.json")
MAX_PASSES = 6

IDS = ("A", "B")
BODIES = ("x", "y", "z")
#: Contents a recorded deck is built from. A half may also hold ``z``, a
#: content the record never had.
BASE_BODIES = ("x", "y")
Word = tuple[str, ...]

HOLDS = "holds"


def cell_text(token: str, lang: str) -> str:
    if token in IDS:
        return (
            f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-{token.lower()}"\n'
            f"#\n# - {token} {lang}\n\n"
        )
    return f"# %%\n{token} = 1\n\n"


def deck_text(lang: str, word: Word) -> str:
    none: frozenset[str] = frozenset()
    cells = "".join(cell_text(t, lang) for t in word)
    text = HEADER[lang] + _probe_cell("S0", lang, none) + cells + _probe_cell("S1", lang, none)
    return text.rstrip("\n") + "\n"


def labels(word: Word) -> list[str]:
    """What :func:`tests.slides.sync_probe.parse_labels` reads back for ``word``."""
    inner = [f"id:vo-{t.lower()}" if t in IDS else f"body:{t} = 1" for t in word]
    return ["id:s-S0", *inner, "id:s-S1"]


def words(max_len: int, ids: Word, bodies: Word = BODIES) -> Iterator[Word]:
    """Every word holding each of ``ids`` once, plus any un-id'd cells."""
    for n in range(len(ids), max_len + 1):
        for places in itertools.permutations(range(n), len(ids)):
            for fill in itertools.product(bodies, repeat=n - len(ids)):
                word: list[str | None] = [None] * n
                for t, p in zip(ids, places, strict=True):
                    word[p] = t
                rest = iter(fill)
                yield tuple(w if w is not None else next(rest) for w in word)


def bases(max_len: int) -> list[Word]:
    """Every recorded deck of up to ``max_len`` cells, with none, one or both
    of the id'd cells."""
    out: set[Word] = set()
    for k in range(len(IDS) + 1):
        for ids in itertools.combinations(IDS, k):
            out.update(words(max_len, ids, BASE_BODIES))
    return sorted(out)


def halves(base: Word, max_len: int) -> list[Word]:
    """Every half of up to ``max_len`` cells that keeps ``base``'s id'd cells
    (adding or removing an id'd cell asks a question by design) and differs
    from it."""
    ids = tuple(t for t in base if t in IDS)
    return sorted(w for w in set(words(max_len, ids)) if w != base)


# ---------------------------------------------------------------------------
# One run of the engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    #: ``clean`` | ``asks`` | ``stalls`` | ``livelock_withheld`` |
    #: ``livelock_stalled`` | ``apply_error`` | ``apply_exception`` |
    #: ``lang_or_kind_anomaly`` (clean, with a cell in the wrong language)
    status: str
    #: for ``asks`` / ``stalls``: the actions that need an answer
    detail: str
    de: tuple[str, ...]
    en: tuple[str, ...]
    #: per pass, the report's ``(key, action)`` rows
    passes: tuple[tuple[tuple[str, str], ...], ...]

    @property
    def outcome(self) -> str:
        """``status`` with the questions' actions, for a baseline entry."""
        return f"{self.status}:{self.detail}" if self.status in ("asks", "stalls") else self.status


def run(base: Word, de: Word, en: Word) -> Run:
    """Record ``base`` on both halves, rewrite them to ``de`` / ``en``, and
    sync with every question refused."""
    passes: list[tuple[tuple[str, str], ...]] = []
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"

        def write(path: Path, lang: str, word: Word) -> None:
            # LF on every platform, so a baseline made on Windows holds in CI
            path.write_text(deck_text(lang, word), encoding="utf-8", newline="\n")

        write(de_path, "de", base)
        write(en_path, "en", base)
        _record(de_path, en_path)
        write(de_path, "de", de)
        write(en_path, "en", en)

        def done(status: str, detail: str = "") -> Run:
            got = []
            for lang, path in (("de", de_path), ("en", en_path)):
                found, anomalies = parse_labels(path.read_text(encoding="utf-8"), lang)
                got.append(tuple(found))
                if anomalies and status == "clean":  # e.g. a cell in the wrong language
                    status = "lang_or_kind_anomaly"
            return Run(status, detail, got[0], got[1], tuple(passes))

        withheld = False
        for _ in range(MAX_PASSES):
            _, diff = _diff(de_path, en_path)
            if diff.is_clean:
                return done("clean")
            passes.append(tuple((i.key, i.action) for i in diff.items))
            asks = sorted({i.action for i in diff.items if doc_apply.item_answers(i)})
            stalls = sorted(
                {
                    i.action
                    for i in diff.items
                    if doc_apply.decision_vocabulary(i.action) and not doc_apply.item_answers(i)
                }
            )
            if asks:
                return done("asks", "+".join(asks))
            if stalls:  # a question nothing can answer
                return done("stalls", "+".join(stalls))
            try:
                outcome = _apply(de_path, en_path, {})
            except Exception as exc:  # noqa: BLE001 - a crash is an outcome to report
                return done("apply_exception", type(exc).__name__)
            if outcome.error is not None:
                return done("apply_error")
            withheld = bool(outcome.to_payload().get("verify_withheld"))
        return done("livelock_withheld" if withheld else "livelock_stalled")


#: ``(base, de, en) -> Run``: one sync with every question refused.
Syncer = Callable[[Word, Word, Word], Run]


# ---------------------------------------------------------------------------
# The laws
# ---------------------------------------------------------------------------


def _conserving(result: Run, base: Word, de: Word, en: Word) -> str | None:
    """Law 4 on one clean run; ``None`` when it holds."""
    if result.de != result.en:
        return "halves_disagree"
    held = Counter(result.de)
    cb, cd, ce = Counter(labels(base)), Counter(labels(de)), Counter(labels(en))
    for cell in set(cb) | set(cd) | set(ce) | set(held):
        if held[cell] > max(cd[cell], ce[cell]):
            return "duplicated"
        if max(cd[cell], ce[cell]) > cb[cell] and held[cell] == 0:
            return "dropped_addition"
        if held[cell] < min(cd[cell], ce[cell]):
            return "fewer_copies"
    return None


@dataclass(frozen=True)
class Case:
    law: str  # unchanged | one-sided | two-sided
    base: Word
    de: Word
    en: Word

    @property
    def id(self) -> str:
        return "|".join((self.law, *(" ".join(w) or "-" for w in (self.base, self.de, self.en))))

    @classmethod
    def parse(cls, case_id: str) -> Case:
        law, *rest = case_id.split("|")
        base, de, en = (tuple(w.split()) if w != "-" else () for w in rest)
        return cls(law, base, de, en)


def check(case: Case, sync: Syncer = run) -> str:
    """:data:`HOLDS`, or what the engine did instead (the baseline's value).

    ``sync`` is the engine under test: :func:`run`, or a stand-in with the
    same signature (``scripts/sync_laws.py --model`` passes the reference
    model).
    """
    first = sync(case.base, case.de, case.en)
    if case.law == "unchanged":
        return HOLDS if first.status == "clean" and not first.passes else first.outcome
    if case.law == "one-sided":
        want = tuple(labels(case.de if case.de != case.base else case.en))
        if first.status != "clean":
            return first.outcome
        if first.de == want and first.en == want:
            return HOLDS
        if sorted(first.de) != sorted(want) or sorted(first.en) != sorted(want):
            return "lost_or_duplicated"
        return "halves_disagree" if first.de != first.en else "misplaced"
    # two-sided: progress, conservation, and symmetry under swapping the halves
    runs = [first] if case.de == case.en else [first, sync(case.base, case.en, case.de)]
    found: list[str] = []
    for result, (de, en) in zip(runs, ((case.de, case.en), (case.en, case.de)), strict=False):
        if result.status in ("asks", "stalls"):
            continue  # a question is no violation: laws 3 and 4 are about the rest
        if result.status != "clean":
            found.append(result.status)
        elif (bad := _conserving(result, case.base, de, en)) is not None:
            found.append(bad)
    if len(runs) == 2:
        a, b = runs
        if (a.status == "clean") != (b.status == "clean"):
            found.append("asymmetric:" + "/".join(sorted((a.status, b.status))))
        elif a.status == "clean" and Counter(a.de) != Counter(b.de):
            found.append("asymmetric:cells")
    return HOLDS if not found else " ".join(dict.fromkeys(found))


def cases(scope: str) -> list[Case]:
    """``ci``: the scope the ratchet runs. ``full``: the scope run by hand."""
    base_len, one_len, two_base_len, two_len = SCOPES[scope]
    out: list[Case] = []
    for b in bases(base_len):
        out.append(Case("unchanged", b, b, b))
        for d in halves(b, one_len):
            out.append(Case("one-sided", b, d, b))
            out.append(Case("one-sided", b, b, d))
        if len(b) <= two_base_len:
            hs = halves(b, two_len)
            for i, d in enumerate(hs):
                out.extend(Case("two-sided", b, d, e) for e in hs[i:])
    return out


#: scope -> (recorded cells, cells of the changed half, recorded cells and
#: cells per half where both halves changed). Every count is a maximum. The
#: two-sided space is quadratic in the halves, hence its smaller bounds.
SCOPES = {
    "ci": (3, 3, 2, 2),
    "full": (3, 4, 2, 3),
}


def load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return baseline
