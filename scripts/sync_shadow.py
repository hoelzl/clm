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

Voiceover companion files are a second part. The prototype merges both parts
at once, with a cell's part as one more aspect of it (a cell can move between
the deck and its companion); the engine reads the bundle as it does in a
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
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from attrs import evolve  # noqa: E402

from clm.core.voiceover_companions import (  # noqa: E402
    COMPANION_SUBDIR,
    companion_name,
    resolve_companion,
)
from clm.slides import doc_apply  # noqa: E402
from clm.slides.bilingual_doc import SideCell  # noqa: E402
from clm.slides.doc_core import (  # noqa: E402
    PARTS,
    SIDES,
    BaseMember,
    Cell,
    Question,
    base_of,
    cell_of,
    merge,
    other,
)
from clm.slides.doc_lenses import _read_source, parse_bundle, read_file  # noqa: E402
from clm.slides.doc_render import RenderError, render  # noqa: E402
from clm.slides.doc_sequence import canonical, deck_sequence, is_localized  # noqa: E402
from clm.slides.doc_text import (  # noqa: E402
    TextPlan,
    TextRow,
    owners_of,
    plan_text,
    recorded_by_half,
    text_key,
    text_of,
)

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


def to_cell(sc: SideCell) -> Cell:
    return cell_of(sc)


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

    The rule and the sequence are :func:`clm.slides.doc_sequence.deck_sequence`,
    the one the ledger records (structural-core step 4), with the content as
    text instead of a fingerprint. The prototype merges the parts at once, the
    deck first.
    """
    if (de_companion is None) != (en_companion is None):
        return "one_companion"
    outcome = parse_bundle(
        de_text, en_text, de_companion, en_companion, comment_token=comment_token
    )
    if outcome.deck is None:
        reasons = outcome.refusal.reasons if outcome.refusal is not None else []
        return "refused:" + (reasons[0].code if reasons else "?")
    sequence = deck_sequence(outcome.deck, content=canonical)
    if isinstance(sequence, str):
        return sequence
    out: dict[str, list[BaseMember]] = {part: [] for part in PARTS}
    for member in base_of(sequence):
        out[member.part].append(member)
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
    #: the model only: the rendered files against the next in-sync state,
    #: the translator's text taken from it (match | text_differs | follower_edited |
    #: localized_separator | differs | leader_changed | text_held |
    #: render_error), and where they first differ
    bytes: str = ""
    bytes_detail: str = ""
    #: the model only: the text layer's rows, ``kind`` or ``kind=answer``
    text_rows: list[str] = field(default_factory=list)
    #: the model only: the identity law on the base state (rendering an
    #: unchanged deck from its own merge gives its bytes back): holds | fails
    identity: str = ""


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

    # One merge over both parts: a cell's part is an aspect, like its tags, so
    # a cell that moves between the deck and its companion stays one cell.
    cells = {
        lang: [
            c for part in PARTS for c in part_cells(case.now(lang, part), lang, part, case.token)
        ]
        for lang in SIDES
    }
    out = merge(base["deck"] + base["companion"], cells["de"], cells["en"], answer)
    opened = {q.kind for q in out.opened}
    got = {
        (lang, part): [c for c in out.halves[lang] if c.part == part]
        for lang in SIDES
        for part in PARTS
    }
    res = Result("clean", answered=dict(answered))
    res.identity = _identity(case, base["deck"] + base["companion"])
    if opened:
        res.status, res.detail = "asks", "+".join(sorted(opened))
        return res
    res.verdict, res.detail = _verdict(case, got)
    res.bytes, res.bytes_detail = _byte_verdict(case, out.halves, res)
    return res


def _identity(case: Case, base: list[BaseMember]) -> str:
    """Design note §6.3: merge the base state with itself and render it."""
    preambles: dict[tuple[str, str], tuple[str, ...] | None] = {}
    inputs: dict[str, list[SideCell]] = {lang: [] for lang in SIDES}
    for lang in SIDES:
        for part in PARTS:
            text = case.base[_slot(lang, part)]
            if text is None:
                preambles[(lang, part)] = None
                continue
            pre, cells = read_file(text, lang, part, case.token)
            preambles[(lang, part)] = pre
            inputs[lang].extend(cells)
    out = merge(
        base,
        [cell_of(c) for c in inputs["de"]],
        [cell_of(c) for c in inputs["en"]],
        lambda _q: None,
    )
    if out.opened:
        return "fails:asks"

    def no_text(_source: SideCell, _lang: str) -> str:
        raise RenderError("the identity pass asked for a translation")

    try:
        files = render(out.halves, preambles, inputs, no_text, case.token)
    except RenderError:
        return "fails:render_error"
    for lang in SIDES:
        for part in PARTS:
            if files[(lang, part)] != case.base[_slot(lang, part)]:
                return f"fails:{lang}-{part}"
    return "holds"


def _translator(case: Case):
    """The translator's text from the next in-sync state: the cell with the
    same id on the target half, else the parser's member of the same key."""
    parsed = parse_bundle(*case.next[:2], *case.next[2:], comment_token=case.token).deck
    by_id: dict[tuple[str, str], SideCell] = {}
    for m in parsed.members() if parsed is not None else []:
        for lang in SIDES:
            sc = m.side(lang)
            if sc is not None:
                by_id[(lang, m.key.render())] = sc
                if sc.slide_id is not None:
                    by_id.setdefault((lang, "sid:" + sc.slide_id.rstrip("!")), sc)

    _, next_cells = _read_state(case, case.expected)
    for lang in SIDES:
        for sc in next_cells[lang]:
            if sc.slide_id is None and text_key(sc) is not None:
                by_id.setdefault((lang, "sid:" + str(text_key(sc))), sc)

    def translate(source: SideCell, lang: str) -> str:
        sid = text_key(source)
        got = by_id.get((lang, "sid:" + sid)) if sid else None
        if got is None:
            raise RenderError(f"no text for {sid or source.cell_type} on {lang}")
        return got.decision_body

    return translate


def _masked(
    text: str | None,
    lang: str,
    part: str,
    token: str,
    *,
    separators: bool = True,
    headers: frozenset[str] = frozenset(),
) -> list[str] | None:
    """A file's lines with each localized cell's text masked: its header
    and (with ``separators``) its trailing blank lines stay (a ``j2`` macro's
    header is its text). The header of a cell whose id is in ``headers`` is
    masked too."""
    if text is None:
        return None
    preamble, cells = read_file(text, lang, part, token)
    out = list(preamble)
    for sc in cells:
        if sc.slide_id is not None and sc.slide_id.rstrip("!") in headers:
            sc = evolve(sc, lines=("<header>", *sc.lines[1:]))
        if not is_localized(sc):
            out.extend(sc.lines)
            continue
        body = list(sc.lines[1:])
        trailing = 0
        while body and body[-1] == "":
            body.pop()
            trailing += 1
        out.append("<macro>" if sc.is_macro and sc.header != "<header>" else sc.header)
        out.append("<text>")
        out.extend([""] * trailing if separators else ["<sep>"])
    return out


def _read_state(
    case: Case, text_of_file
) -> tuple[dict[tuple[str, str], tuple[str, ...] | None], dict[str, list[SideCell]]]:
    """Each file's preamble and each half's cells, ``text_of_file(lang,
    part)`` giving a file's text."""
    preambles: dict[tuple[str, str], tuple[str, ...] | None] = {}
    cells: dict[str, list[SideCell]] = {lang: [] for lang in SIDES}
    for lang in SIDES:
        for part in PARTS:
            text = text_of_file(lang, part)
            if text is None:
                preambles[(lang, part)] = None
                continue
            pre, got = read_file(text, lang, part, case.token)
            preambles[(lang, part)] = pre
            cells[lang].extend(got)
    return preambles, cells


def _text_judge(case: Case, now: dict[str, list[SideCell]], answered: list[str]):
    """Answer the text layer's rows as the author did: from the next
    in-sync state."""
    next_pre, next_cells = _read_state(case, case.expected)
    now_pre, _ = _read_state(case, case.now)
    by_id = {(lang, k): sc for lang in SIDES for sc in now[lang] if (k := text_key(sc))}
    want = {(lang, k): sc for lang in SIDES for sc in next_cells[lang] if (k := text_key(sc))}

    def judge(row: TextRow) -> str | None:
        answer: str | None = None
        sid = row.handle.removeprefix("id:")
        if row.kind == "translate_edit":
            twin = other(row.side or "de")
            have, then = by_id.get((twin, sid)), want.get((twin, sid))
            if have is not None and then is not None:
                answer = "keep_twin" if text_of(have) == text_of(then) else "body"
        elif row.kind in ("verify_translation", "verify_cold"):
            answer = "confirm"
        elif row.kind == "conflict_owner":
            then = want.get(("de", sid)) or want.get(("en", sid))
            for lang in SIDES:
                have = by_id.get((lang, sid))
                if have is not None and then is not None and owners_of(have) == owners_of(then):
                    answer = lang
        elif row.kind == "conflict_preamble":
            part = row.handle.removeprefix("preamble:")
            for lang in SIDES:
                if now_pre.get((lang, part)) == next_pre.get((lang, part)):
                    answer = lang
        answered.append(f"{row.kind}={answer}")
        return answer

    return judge


def _attributes(header: str) -> list[str]:
    """A cell header's attributes, in no particular order."""
    return sorted(re.findall(r'\w+=(?:"[^"]*"|\[[^\]]*\])', header))


def _follower_edits(
    case: Case,
    follow: str,
    plan: TextPlan,
    base_cells: dict[str, list[SideCell]],
    base_preambles: dict[tuple[str, str], tuple[str, ...] | None],
) -> list[str]:
    """What the author changed on the follower between the base and the
    next state that the text layer had no reason to change: the text of a
    localized cell it did not retext, a header the author re-formatted, an
    owner attribute the plan left as it was, a preamble it did not
    propagate."""
    next_preambles, next_cells = _read_state(case, case.expected)
    then = {k: text_of(sc) for sc in base_cells[follow] if is_localized(sc) and (k := text_key(sc))}
    out = []
    for sc in next_cells[follow]:
        sid = text_key(sc)
        if sid is None or not is_localized(sc) or (follow, sid) in plan.retext:
            continue
        if sid in then and then[sid] != text_of(sc):
            out.append(f"id:{sid}")
    # A header the author re-formatted (attribute order, spacing) with the
    # same attributes: the normalizer's work, which a sync does not mirror.
    # An owner attribute the author changed by hand where the plan kept the
    # follower's value (a fingerprint anchor re-computed, say).
    cells = {sc.slide_id.rstrip("!"): sc for sc in base_cells[follow] if sc.slide_id is not None}
    for sc in next_cells[follow]:
        sid = sc.slide_id.rstrip("!") if sc.slide_id is not None else None
        before = cells.get(sid) if sid is not None else None
        if before is None or before.header == sc.header:
            continue
        if _attributes(before.header) == _attributes(sc.header):
            out.append(f"format:{sid}")
        else:
            kept = plan.owners.get((follow, str(sid)))
            then, now = owners_of(before), owners_of(sc)
            if kept is not None and any(now[i] != then[i] and kept[i] == then[i] for i in range(2)):
                out.append(f"owner:{sid}")
    for part in PARTS:
        key = (follow, part)
        before, after = base_preambles.get(key), next_preambles.get(key)
        if (
            before is not None
            and after is not None
            and before != after
            and key not in plan.preambles
        ):
            out.append(f"preamble:{part}")
    return out


def _byte_verdict(case: Case, halves: dict[str, list[Cell]], res: Result) -> tuple[str, str]:
    """Render the model's result with the text layer and judge it against
    the next in-sync state byte by byte: the leader's files unchanged, the
    follower's equal to the next state's, the translator's text taken from
    it. Where they differ, the comparison with localized text masked tells a
    difference of text from one of structure."""
    preambles, inputs = _read_state(case, case.now)
    base_preambles, base_cells = _read_state(case, lambda lang, part: case.base[_slot(lang, part)])
    answered: list[str] = []
    plan = plan_text(
        halves,
        recorded_by_half(base_cells),
        preambles,
        base_preambles,
        _text_judge(case, inputs, answered),
    )
    res.text_rows = answered + [r.kind for r in plan.mechanical]
    if plan.held:
        return "text_held", "+".join(sorted({r.kind for r in plan.held}))
    try:
        files = render(halves, preambles, inputs, _translator(case), case.token, plan)
    except RenderError as exc:
        return "render_error", str(exc)[:200]
    lead, follow = case.leader, other(case.leader)
    for part in PARTS:
        if files[(lead, part)] != case.now(lead, part):
            return "leader_changed", part
    if all(files[(follow, part)] == case.expected(follow, part) for part in PARTS):
        return "match", ""
    # The author also edited the follower where the leader's change asked
    # for nothing (text, a header's format or owner, a preamble): no sync
    # produces that, so the replay is not one-sided in its text.
    edited = _follower_edits(case, follow, plan, base_cells, base_preambles)
    by_hand = frozenset(e.split(":", 1)[1] for e in edited if e.startswith(("format:", "owner:")))
    # Structure first: with localized text, separators and the headers the
    # author edited by hand masked, any difference is the core's or the
    # renderer's.
    for part in PARTS:
        loose = [
            _masked(t, follow, part, case.token, separators=False, headers=by_hand)
            for t in (files[(follow, part)], case.expected(follow, part))
        ]
        if loose[0] != loose[1]:
            return "differs", f"{part}: {_first_line_difference(*loose)}"
    if edited:
        return "follower_edited", ", ".join(edited[:3])
    for part in PARTS:
        have = _masked(files[(follow, part)], follow, part, case.token)
        want = _masked(case.expected(follow, part), follow, part, case.token)
        if have != want:
            return "localized_separator", f"{part}: {_first_line_difference(have, want)}"
    for part in PARTS:
        got, exp = files[(follow, part)], case.expected(follow, part)
        if got != exp:
            have = got.split("\n") if got is not None else None
            want = exp.split("\n") if exp is not None else None
            return "text_differs", f"{part}: {_first_line_difference(have, want)}"
    return "match", ""


def _first_line_difference(have: list[str] | None, want: list[str] | None) -> str:
    if have is None or want is None:
        return f"file {'absent' if have is None else 'present'}, want the other"
    for i, (h, w) in enumerate(zip(have, want, strict=False)):
        if h != w:
            at = next((k for k, (a, b) in enumerate(zip(h, w, strict=False)) if a != b), 0)
            at = max(0, min(at, len(h), len(w)) - 20)
            return f"line {i + 1}: got {h[at : at + 70]!r} want {w[at : at + 70]!r}"
    return f"{len(have)} lines, want {len(want)}"


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


def run_case(case: Case, model_only: bool = False) -> dict:
    return {
        "id": case.id,
        "model": asdict(run_model(case)),
        "engine": asdict(Result("skipped") if model_only else run_engine(case)),
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
        work = partial(run_case, model_only=args.model_only)
        results = list(pool.map(work, cases, chunksize=4))
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
    identity = Counter(r["model"].get("identity") or "-" for r in results)
    print("\nmodel, identity law on the base state:")
    for k, v in identity.most_common():
        print(f"  {v:6d}  {k}")
    rows = Counter(k for r in results for k in r["model"].get("text_rows") or [])
    print("\nmodel, text layer rows:")
    for k, v in rows.most_common():
        print(f"  {v:6d}  {k}")
    rendered = Counter(r["model"].get("bytes") or "-" for r in results)
    print("\nmodel, rendered files (localized text masked):")
    for k, v in rendered.most_common():
        print(f"  {v:6d}  {k}")
    if show:
        off = [r for r in results if r["model"].get("bytes") not in ("match", "", None)]
        print(f"\nfirst {min(show, len(off))} replays whose rendered files differ:")
        for r in off[:show]:
            print(f"  {r['id']}\n    {r['model']['bytes']}: {r['model']['bytes_detail']}")
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
    p.add_argument(
        "--model-only",
        action="store_true",
        help="skip the engine (fast; engine rows read 'skipped')",
    )
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
