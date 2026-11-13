"""Shadow the prototype sync core beside the engine on real course history.

Step 4 of the path in ``docs/claude/sync-structural-core-investigation.md``
(§9): run the prototype core (``scripts/sync_model.py``) on real decks, next to
the engine, and judge both against what the author committed.

**The cases.** For every deck pair in a course repository, the committed
states in which both halves are in sync (they parse, every shared cell is
byte-identical on both halves, every localized cell has both sides) are taken
in commit order. Each change from one in-sync state ``B`` to the next ``N`` is
replayed twice, as a one-sided edit: the German half of ``N`` beside the
English half of ``B`` ("de-led"), and the other way round. The edit is a real
author's edit; that it is replayed on one half is the probe's own shape (one
half untouched, §2.2 of the doc). ``N`` is the oracle: a sync that ends must
leave the half that led as it was and turn the other half into ``N``'s, up to
the text of localized cells, which a translator writes.

**The runs.** Both get the same judge. Questions about translation are
answered from ``N`` (a perfect translator), including the one a fork raises (a
shared cell the leading half turned into its own language); a localized cell
the leading half removed is removed, and a cell it made shared again takes its
content. Every other question is refused, as in the laws (§2.5): a one-sided
edit should need no other answer.

- The engine records ``B``, gets the two halves, and runs ``report`` and
  ``apply`` up to six passes (``tests/slides/sync_laws.py``'s loop).
- The prototype aligns each half to ``B``'s sequence and merges once. Localized
  text is not modelled; its translation answer leaves a placeholder, which the
  comparison does not read.

Voiceover companion files are a second part: the prototype merges each part
alone (cells never cross parts), the engine reads the bundle as it does in a
course repository.

**Not covered (limits of this shadow):** the preamble (the lines before the
first cell),
the order of a cell's tags (compared as a set), header attributes other than
``slide_id`` and ``tags`` (compared as part of the cell's content), and edits
an author made on both halves before syncing (each replay has one half lead).

Usage::

    python scripts/sync_shadow.py run REPO OUT.json [--ext py] [--since DATE] [--workers N] [--limit N]
    python scripts/sync_shadow.py summary OUT.json [--show N]
    python scripts/sync_shadow.py trace REPO DECK B N {de,en} [--ext py]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_model import SIDES, BaseMember, Cell, Question, merge, other  # noqa: E402

from clm.core.voiceover_companions import (  # noqa: E402
    COMPANION_SUBDIR,
    companion_name,
    resolve_companion,
)
from clm.slides import doc_apply  # noqa: E402
from clm.slides.bilingual_doc import SideCell  # noqa: E402
from clm.slides.doc_lenses import _bare, _read_source, parse_bundle  # noqa: E402

MAX_PASSES = 6
#: Questions the judge answers. Everything else is refused.
TRANSLATION = ("translate_new", "translate_edit")
REMOVAL = ("remove_localized_side",)
#: A cell turned per-language on one half (the engine marks the twin, then
#: asks for its text as a ``translate_edit``), or made shared again.
FORK = ("fork_pending_twin",)
UNIFY = ("unify_choose_body",)
#: The prototype's questions the judge answers, with the answer.
MODEL_ANSWERS = {
    "translate_new": "body",
    "remove_localized_side": "remove",
    "fork": "body",
    "unify": "accept",
}

_ID_ATTR_RE = re.compile(r'\s*slide_id="[^"]*"')
_TAGS_ATTR_RE = re.compile(r"\s*tags=\[[^\]]*\]")


# ---------------------------------------------------------------------------
# The adapter: a real deck half as the prototype's cells
# ---------------------------------------------------------------------------


def canonical(sc: SideCell) -> str:
    """A cell's content as the prototype compares it: every byte the file holds
    for it, except the ``slide_id`` and ``tags`` attributes, which the
    prototype carries as identity and as an aspect of their own."""
    header = _TAGS_ATTR_RE.sub("", _ID_ATTR_RE.sub("", sc.lines[0]))
    return "\n".join((header, *sc.lines[1:]))


def is_localized(sc: SideCell) -> bool:
    """A cell each half holds in its own language. A ``j2`` cell counts: the
    header macros differ per half by design (``header_de`` / ``header_en``)."""
    return sc.lang_attr is not None or sc.cell_type == "j2"


def to_cell(sc: SideCell) -> Cell:
    return Cell(
        _bare(sc.slide_id), sc.cell_type, canonical(sc), is_localized(sc), sc, frozenset(sc.tags)
    )


PARTS = ("deck", "companion")


def part_cells(
    text: str | None, lang: str, part: str = "deck", comment_token: str = "#"
) -> list[Cell]:
    """One file of one half, in file order; an absent file holds no cells."""
    if text is None:
        return []
    return [to_cell(sc) for sc in _read_source(text, lang, part, comment_token).cells]


def half_cells(text: str, lang: str, comment_token: str = "#") -> list[Cell]:
    """One deck half, in file order."""
    return part_cells(text, lang, "deck", comment_token)


def base_members(
    de_text: str,
    en_text: str,
    comment_token: str = "#",
    de_companion: str | None = None,
    en_companion: str | None = None,
) -> dict[str, list[BaseMember]] | str:
    """The recorded sequence of each part of an in-sync pair, or why the pair
    is not in sync.

    In sync means: the bundle parses with no refusal and no observation, every
    member has both sides, the two halves hold each part's cells in one order,
    and a cell's tags are the same on both halves. Each part (the deck, the
    voiceover companion) is then one sequence of members, which is what the
    prototype records. Cells never cross parts, so each part is merged alone.
    """
    if (de_companion is None) != (en_companion is None):
        return "one_companion"
    outcome = parse_bundle(
        de_text, en_text, de_companion, en_companion, comment_token=comment_token
    )
    deck = outcome.deck
    if deck is None:
        reasons = outcome.refusal.reasons if outcome.refusal is not None else []
        return "refused:" + (reasons[0].code if reasons else "?")
    if deck.observations:
        return "observation:" + deck.observations[0].kind
    out: dict[str, list[BaseMember]] = {}
    for part in PARTS:
        members = [m for m in deck.members() if m.de is not None and m.de.part == part]
        if any(m.en is None or m.en.part != part for m in members):
            return "one_sided_member"
        if sum(1 for m in deck.members() if m.en is not None and m.en.part == part) != len(members):
            return "one_sided_member"
        by_de = sorted(members, key=lambda m: m.de.index)  # type: ignore[union-attr]
        by_en = sorted(members, key=lambda m: m.en.index)  # type: ignore[union-attr]
        if [id(m) for m in by_de] != [id(m) for m in by_en]:
            return "order_differs"
        seq: list[BaseMember] = []
        for m in by_de:
            de, en = m.de, m.en
            assert de is not None and en is not None
            if frozenset(de.tags) != frozenset(en.tags):
                return "tags_differ"
            if not is_localized(de) and canonical(de) != canonical(en):
                return "shared_differs"
            seq.append(
                BaseMember(
                    _bare(de.slide_id) or _bare(en.slide_id),
                    de.cell_type,
                    is_localized(de),
                    canonical(de),
                    canonical(en),
                    de,
                    frozenset(de.tags),
                )
            )
        out[part] = seq
    return out


#: One cell as a sync result is judged: identity, kind, tags, and content
#: where the content is not a translator's.
Shape = tuple[str | None, str, tuple[str, ...], str | None]


def shape(cell: Cell, *, text: bool) -> Shape:
    body = cell.body if text or not cell.localized else None
    return (cell.sid, cell.kind, tuple(sorted(cell.tags)), body)


def shapes(cells: list[Cell], *, text: bool) -> list[Shape]:
    return [shape(c, text=text) for c in cells]


# ---------------------------------------------------------------------------
# Cases from history
# ---------------------------------------------------------------------------

#: One committed state of a deck: de, en, de companion, en companion
#: (``None`` = no companion file).
State = tuple[str, str, str | None, str | None]


@dataclass(frozen=True)
class Case:
    deck: str  # repo-relative path without ``.de.<ext>``
    ext: str
    b: str  # commit of the base state
    n: str  # commit of the next in-sync state
    leader: str  # de | en
    base: State
    next: State

    @property
    def id(self) -> str:
        return f"{self.deck}@{self.b[:8]}..{self.n[:8]}:{self.leader}"

    @property
    def token(self) -> str:
        return "//" if self.ext in ("cpp", "cs", "java", "ts") else "#"

    def now(self, lang: str, part: str) -> str | None:
        """What half ``lang`` holds for ``part`` when the sync starts."""
        state = self.next if lang == self.leader else self.base
        return state[_slot(lang, part)]

    def expected(self, lang: str, part: str) -> str | None:
        return self.next[_slot(lang, part)]


def _slot(lang: str, part: str) -> int:
    return (0 if lang == "de" else 1) + (2 if part == "companion" else 0)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout


def _show(repo: str, commit: str, path: str) -> str | None:
    """A file's text at a commit, ``None`` when the commit has no such file."""
    proc = subprocess.run(
        ["git", "-C", repo, "show", f"{commit}:{path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _companion(repo: str, commit: str, deck: str, lang: str, ext: str) -> str | None:
    """The companion's text at a commit, by the read rule of
    :func:`clm.core.voiceover_companions.resolve_companion` (the ``voiceover/``
    subdirectory before the sibling)."""
    half = Path(f"{deck}.{lang}.{ext}")
    name = companion_name(half)
    folder = half.parent.as_posix()
    for path in (f"{folder}/{COMPANION_SUBDIR}/{name}", f"{folder}/{name}"):
        text = _show(repo, commit, path.removeprefix("./"))
        if text is not None:
            return text
    return None


def _decks_touched(changed: list[str], ext: str) -> set[str]:
    """The decks a commit touched: through a half or through a companion."""
    suffix = re.compile(rf"\.(de|en)\.{re.escape(ext)}$")
    out = set()
    for p in changed:
        if not suffix.search(p):
            continue
        folder, _, name = p.rpartition("/")
        stem = suffix.sub("", name)
        if name.startswith("slides_"):
            out.add(f"{folder}/{stem}" if folder else stem)
        elif name.startswith("voiceover_"):
            if folder.endswith(f"/{COMPANION_SUBDIR}") or folder == COMPANION_SUBDIR:
                folder = folder[: -len(COMPANION_SUBDIR)].rstrip("/")
            deck = "slides_" + stem.removeprefix("voiceover_")
            out.add(f"{folder}/{deck}" if folder else deck)
    return out


def history_cases(repo: str, ext: str, since: str, stats: Counter) -> Iterator[Case]:
    """Every change between two consecutive in-sync states of a deck, both ways."""
    commits = _git(
        repo,
        "log",
        "--reverse",
        "--first-parent",
        f"--since={since}",
        "--format=%H",
        "--",
        f"*.de.{ext}",
        f"*.en.{ext}",
    ).split()
    token = "//" if ext in ("cpp", "cs", "java", "ts") else "#"
    last: dict[str, tuple[str, State]] = {}  # deck -> its last in-sync state
    for c in commits:
        # a merge lists its files only against its first parent (``-m``)
        changed = _git(
            repo,
            "diff-tree",
            "--no-commit-id",
            "-r",
            "--name-only",
            "--root",
            "-m",
            "--first-parent",
            c,
        )
        for deck in sorted(_decks_touched(changed.split("\n"), ext)):
            de = _show(repo, c, f"{deck}.de.{ext}")
            en = _show(repo, c, f"{deck}.en.{ext}")
            if not de or not en:
                continue
            state: State = (
                de,
                en,
                _companion(repo, c, deck, "de", ext),
                _companion(repo, c, deck, "en", ext),
            )
            stats["states"] += 1
            base = base_members(de, en, token, state[2], state[3])
            if isinstance(base, str):
                stats[f"not in sync: {base}"] += 1
                continue
            stats["in-sync states"] += 1
            prev = last.get(deck)
            last[deck] = (c, state)
            if prev is None or prev[1] == state:
                continue
            stats["changes"] += 1
            if state[2] is not None:
                stats["changes with a companion"] += 1
            for leader in SIDES:
                slots = (0, 2) if leader == "de" else (1, 3)
                if all(state[i] == prev[1][i] for i in slots):
                    stats["replays skipped: that half did not change"] += 1
                    continue
                yield Case(deck, ext, prev[0], c, leader, prev[1], state)


# ---------------------------------------------------------------------------
# The two runs
# ---------------------------------------------------------------------------


@dataclass
class Result:
    status: str  # clean | asks | stalls | livelock | error | not_in_sync
    detail: str = ""
    #: questions the judge answered, by kind
    answered: dict[str, int] = field(default_factory=dict)
    #: when the run ended clean: does it match the next in-sync state?
    verdict: str = ""  # match | leader_changed | differs
    #: the engine's passes: per pass, ``action key`` of every framed item
    passes: list[list[str]] = field(default_factory=list)


def _verdict(case: Case, got: dict[tuple[str, str], list[Cell]]) -> tuple[str, str]:
    """Judge a finished sync against the next in-sync state, part by part."""
    lead, follow = case.leader, other(case.leader)
    for part in PARTS:
        now = part_cells(case.now(lead, part), lead, part, case.token)
        if shapes(got[(lead, part)], text=True) != shapes(now, text=True):
            return "leader_changed", part
    for part in PARTS:
        want = shapes(part_cells(case.expected(follow, part), follow, part, case.token), text=False)
        have = shapes(got[(follow, part)], text=False)
        if have != want:
            return "differs", f"{part}: {_first_difference(have, want)}"
    return "match", ""


def _first_difference(have: list[Shape], want: list[Shape]) -> str:
    for i, (h, w) in enumerate(zip(have, want, strict=False)):
        if h != w:
            return f"cell {i}: got {_brief(h)} want {_brief(w)}"
    return f"{len(have)} cells, want {len(want)}"


def _brief(s: Shape) -> str:
    body = (s[3] or "").split("\n")
    return f"{s[0] or '-'}/{s[1]}/{','.join(s[2])}/{body[1][:30] if len(body) > 1 else ''!r}"


def run_model(case: Case) -> Result:
    base = base_members(*case.base[:2], case.token, *case.base[2:])
    if isinstance(base, str):
        return Result("not_in_sync", base)
    answered: Counter = Counter()

    def answer(q: Question) -> str | None:
        got = MODEL_ANSWERS.get(q.kind)
        if got is not None:
            answered[q.kind] += 1
        return got

    got: dict[tuple[str, str], list[Cell]] = {}
    opened: set[str] = set()
    for part in PARTS:
        cells = {lang: part_cells(case.now(lang, part), lang, part, case.token) for lang in SIDES}
        out = merge(base[part], cells["de"], cells["en"], answer)
        opened |= {q.kind for q in out.opened}
        for lang in SIDES:
            got[(lang, part)] = out.halves[lang]
    res = Result("clean", answered=dict(answered))
    if opened:
        res.status, res.detail = "asks", "+".join(sorted(opened))
        return res
    res.verdict, res.detail = _verdict(case, got)
    return res


def _apply(de_path: Path, en_path: Path, decisions: dict[str, doc_apply.Decision]):
    """``tests/slides/sync_probe.py``'s apply, with the deck's own comment token
    for the verify gate (the probe's decks are all Python, and it passes
    ``#``; a C++ deck then fails the gate and nothing is recorded)."""
    from clm.slides import doc_ledger
    from clm.slides.doc_lenses import load_bundle
    from clm.slides.sync_diff import diff_outcome
    from clm.slides.sync_verify import apply_verify_gate

    bundle = load_bundle(de_path, en_path)
    assert bundle.outcome.deck is not None
    ledger_path = doc_ledger.ledger_path_for(de_path)
    ledger = doc_ledger.load(ledger_path)
    deck_key = doc_ledger.deck_key_for(de_path)
    deck_ledger = ledger.decks.get(deck_key)
    base = doc_ledger.baseline_from_ledger(deck_ledger) if deck_ledger else None
    outcome = doc_apply.apply_deck(
        bundle,
        bundle.outcome.deck,
        diff_outcome(bundle.outcome, base),
        ledger,
        deck_key,
        decisions=decisions,
        verify_gate=apply_verify_gate(de_path, en_path, bundle.comment_token),
    )
    if outcome.error is None and outcome.ledger_changed:
        doc_ledger.save(ledger, ledger_path)
    return outcome


def run_engine(case: Case) -> Result:
    from tests.slides.sync_probe import _diff, _record

    follow = other(case.leader)
    # The translator's text, by member key, from the next in-sync state. The
    # parser names members (the title macro is ``id:title`` without carrying
    # a ``slide_id``), so the keys are taken from it, as the engine's are.
    parsed = parse_bundle(*case.next[:2], *case.next[2:], comment_token=case.token).deck
    expected = {
        m.key.render(): cell
        for m in (parsed.members() if parsed is not None else [])
        if (cell := m.side(follow)) is not None
    }
    answered: Counter = Counter()
    passes: list[list[str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        paths = {(lang, "deck"): Path(tmp) / f"slides_t.{lang}.{case.ext}" for lang in SIDES}
        for lang in SIDES:
            paths[(lang, "companion")] = Path(tmp) / companion_name(paths[(lang, "deck")])

        def write(text_of) -> None:
            for key, path in paths.items():
                text = text_of(*key)
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(text, encoding="utf-8", newline="")

        de_path, en_path = paths[("de", "deck")], paths[("en", "deck")]
        write(lambda lang, part: case.base[_slot(lang, part)])
        try:
            _record(de_path, en_path)
        except Exception as exc:  # noqa: BLE001 - an outcome to report
            return Result("error", f"record: {type(exc).__name__}")
        write(case.now)

        def done(status: str, detail: str = "") -> Result:
            res = Result(status, detail, dict(answered), passes=passes)
            if status == "clean":
                got = {}
                for lang in SIDES:
                    deck_path = paths[(lang, "deck")]
                    # read as sync does: a companion the engine created goes
                    # into the ``voiceover/`` subdirectory, not beside the deck
                    comp = resolve_companion(deck_path)
                    for part, path in (("deck", deck_path), ("companion", comp)):
                        text = path.read_text(encoding="utf-8") if path is not None else None
                        got[(lang, part)] = part_cells(text, lang, part, case.token)
                res.verdict, res.detail = _verdict(case, got)
            return res

        for _ in range(MAX_PASSES):
            try:
                _, diff = _diff(de_path, en_path)
            except Exception as exc:  # noqa: BLE001
                return done("error", f"report: {type(exc).__name__}")
            if diff.is_clean:
                return done("clean")
            passes.append([f"{i.action} {i.key}" for i in diff.items])
            decisions: dict[str, doc_apply.Decision] = {}
            refused: set[str] = set()
            stalls: set[str] = set()
            for item in diff.items:
                answers = doc_apply.item_answers(item)
                if item.action in TRANSLATION:
                    sc = expected.get(item.key)
                    if sc is not None and "body" in answers:
                        answered[item.action] += 1
                        decisions[item.key] = doc_apply.Decision(
                            key=item.key, choice="body", body=sc.decision_body
                        )
                        continue
                if item.action in FORK and "mark_twin" in answers:
                    answered[item.action] += 1
                    decisions[item.key] = doc_apply.Decision(key=item.key, choice="mark_twin")
                    continue
                if item.action in UNIFY and case.leader in answers:
                    answered[item.action] += 1
                    decisions[item.key] = doc_apply.Decision(key=item.key, choice=case.leader)
                    continue
                if item.action in REMOVAL and "remove" in answers:
                    answered[item.action] += 1
                    decisions[item.key] = doc_apply.Decision(key=item.key, choice="remove")
                    continue
                if answers:
                    refused.add(item.action)
                elif doc_apply.decision_vocabulary(item.action):
                    stalls.add(item.action)
            if refused:
                return done("asks", "+".join(sorted(refused)))
            if stalls:
                return done("stalls", "+".join(sorted(stalls)))
            try:
                outcome = _apply(de_path, en_path, decisions)
            except Exception as exc:  # noqa: BLE001
                return done("error", f"apply: {type(exc).__name__}")
            if outcome.error is not None:
                return done("error", "apply_error")
        return done("livelock")


def run_case(case: Case) -> dict:
    return {
        "id": case.id,
        "model": asdict(run_model(case)),
        "engine": asdict(run_engine(case)),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def outcome(r: dict) -> str:
    if r["status"] == "clean":
        return f"clean:{r['verdict']}"
    if r["status"] in ("asks", "stalls"):
        return f"{r['status']}:{r['detail']}"
    return r["status"]


def cmd_run(args: argparse.Namespace) -> int:
    stats: Counter = Counter()
    cases = list(history_cases(args.repo, args.ext, args.since, stats))
    if args.limit:
        cases = cases[: args.limit]
    print(f"{len(cases)} replays", flush=True)
    for k, v in sorted(stats.items()):
        print(f"  {v:6d}  {k}")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_case, cases, chunksize=4))
    Path(args.out).write_text(
        json.dumps({"repo": Path(args.repo).name, "stats": stats, "results": results}, indent=1),
        encoding="utf-8",
    )
    summarize(results, show=0)
    return 0


def summarize(results: list[dict], show: int) -> None:
    for who in ("engine", "model"):
        counts = Counter(outcome(r[who]) for r in results)
        print(f"\n{who}: {len(results)} replays")
        for k, v in counts.most_common():
            print(f"  {v:6d}  {k}")
    pair = Counter(
        (outcome(r["engine"]).split(":")[0] + ":" + r["engine"]["verdict"]).rstrip(":")
        + "  |  "
        + (outcome(r["model"]).split(":")[0] + ":" + r["model"]["verdict"]).rstrip(":")
        for r in results
    )
    print("\nengine  |  model")
    for k, v in pair.most_common():
        print(f"  {v:6d}  {k}")
    if show:
        bad = [
            r
            for r in results
            if r["model"]["status"] != "clean" or r["model"]["verdict"] != "match"
        ]
        print(f"\nfirst {min(show, len(bad))} replays the model did not match:")
        for r in bad[:show]:
            print(f"  {r['id']}\n    model:  {outcome(r['model'])} {r['model']['detail']}")
            print(f"    engine: {outcome(r['engine'])} {r['engine']['detail']}")


def cmd_summary(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.out).read_text(encoding="utf-8"))
    for k, v in sorted(data["stats"].items()):
        print(f"  {v:6d}  {k}")
    summarize(data["results"], show=args.show)
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    def state(commit: str) -> State:
        de, en = (
            _show(args.repo, commit, f"{args.deck}.{lang}.{args.ext}") or "" for lang in SIDES
        )
        de_c, en_c = (_companion(args.repo, commit, args.deck, lang, args.ext) for lang in SIDES)
        return de, en, de_c, en_c

    case = Case(args.deck, args.ext, args.b, args.n, args.leader, state(args.b), state(args.n))
    print(json.dumps(run_case(case), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("repo")
    p.add_argument("out")
    p.add_argument("--ext", default="py")
    p.add_argument("--since", default="2026-05-30")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("summary")
    p.add_argument("out")
    p.add_argument("--show", type=int, default=10)
    p.set_defaults(func=cmd_summary)
    p = sub.add_parser("trace")
    p.add_argument("repo")
    p.add_argument("deck")
    p.add_argument("b")
    p.add_argument("n")
    p.add_argument("leader", choices=SIDES)
    p.add_argument("--ext", default="py")
    p.set_defaults(func=cmd_trace)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
