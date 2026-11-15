"""Open sync issues, end to end on the sequence core (``CLM_SYNC_CORE=sequence``).

Each test takes an open issue's own repro and drives it the way an agent
does: ``clm slides sync record``, the one-sided edit, ``report --json``,
answer the questions in a decision document, ``apply``, ``report`` clean. It
asserts convergence and the layout the author meant, or, where the issue's
expected outcome is a question, that question.

The positional engine is deleted after phase D of
``docs/claude/design/sync-sequence-core.md``; these issues close when the
sequence core becomes the default, not by patches to the positional engine.
``tests/slides/test_doc_core.py`` covers #1092, #1187 and #1216 at the core
level; these tests cover the pass and the CLI an agent uses.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.slides.sync import slides_sync_group
from tests.cli.test_sync_v3_cli import _json_payload, _stderr

HEADER = {
    "de": "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel\") }}\n",
    "en": "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title\") }}\n",
}
WORD = {"de": "Folie", "en": "Slide"}


def slide(lang: str, sid: str, tags: str = '["slide"]') -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags={tags} slide_id="{sid}"\n#\n# ## {WORD[lang]} {sid}\n'
    )


def note(lang: str, sid: str, tags: str | None = None, owner: str | None = None) -> str:
    """An id'd localized markdown cell (a voiceover when ``owner`` is set)."""
    tag = f" tags={tags}" if tags else ""
    for_slide = f' for_slide="{owner}"' if owner else ""
    return f'# %% [markdown] lang="{lang}"{tag} slide_id="{sid}"{for_slide}\n#\n# - {sid} {lang}\n'


def code(body: str, header: str = "") -> str:
    return f"# %%{(' ' + header) if header else ''}\n{body}\n"


def deck(lang: str, *cells: str | Callable[[str], str]) -> str:
    parts = [HEADER[lang]]
    parts.extend(c(lang) if callable(c) else c for c in cells)
    return "\n".join(parts)


def s(sid: str, tags: str = '["slide"]') -> Callable[[str], str]:
    return lambda lang: slide(lang, sid, tags)


def n(sid: str, **kw: str) -> Callable[[str], str]:
    return lambda lang: note(lang, sid, **kw)


class Pair:
    """A recorded deck pair, driven through ``clm slides sync``."""

    def __init__(
        self,
        tmp_path: Path,
        runner: CliRunner,
        de: str,
        en: str,
        de_vo: str | None = None,
        en_vo: str | None = None,
    ) -> None:
        self.tmp = tmp_path
        self.runner = runner
        self.de = tmp_path / "slides_t.de.py"
        self.en = tmp_path / "slides_t.en.py"
        self.de_vo = tmp_path / "voiceover" / "voiceover_t.de.py"
        self.en_vo = tmp_path / "voiceover" / "voiceover_t.en.py"
        self.write(de=de, en=en, de_vo=de_vo, en_vo=en_vo)
        result = runner.invoke(slides_sync_group, ["record", str(self.de)])
        assert result.exit_code == 0, result.output + _stderr(result)
        assert self.report()["is_clean"] is True

    def write(self, **texts: str | None) -> None:
        for name, text in texts.items():
            if text is None:
                continue
            path: Path = getattr(self, name)
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")

    def text(self, name: str) -> str:
        return getattr(self, name).read_text(encoding="utf-8")

    def report(self) -> dict:
        result = self.runner.invoke(slides_sync_group, ["report", str(self.de), "--json"])
        payload = _json_payload(result.output)
        assert len(payload["pairs"]) == 1, payload
        return payload["pairs"][0]

    def apply(self, *decisions: dict) -> dict:
        report_id = self.report()["report_id"]
        document = self.tmp / "decisions.json"
        document.write_text(
            json.dumps({"schema": 6, "report_id": report_id, "decisions": list(decisions)}),
            encoding="utf-8",
        )
        result = self.runner.invoke(
            slides_sync_group, ["apply", str(self.de), "--decisions", str(document), "--json"]
        )
        payload = _json_payload(result.output)
        payload["_exit"] = result.exit_code
        payload["_stderr"] = _stderr(result)
        return payload


def questions(report: dict) -> list[tuple[str, str, list[str]]]:
    return [(i["action"], i["key"], i["answers"]) for i in report["items"]]


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("CLM_SYNC_CORE", "sequence")
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


def converge(pair: Pair, answer: Callable[[dict], list[dict]] | None = None) -> list[dict]:
    """report → apply (with ``answer``'s decisions) → report, until clean.
    Returns every report taken; the last one is clean."""
    reports = [pair.report()]
    for _ in range(3):
        if reports[-1]["is_clean"]:
            return reports
        assert reports[-1]["refusal"] is None and reports[-1]["error"] is None, reports[-1]
        applied = pair.apply(*(answer(reports[-1]) if answer else []))
        assert applied["_exit"] == 0, applied
        reports.append(pair.report())
    assert reports[-1]["is_clean"], reports
    return reports


def bodies(text: dict[str, str]) -> Callable[[dict], list[dict]]:
    """Answer every ``body`` question from ``text`` (handle → body)."""
    return lambda report: [
        {"key": i["key"], "body": text[i["key"]]} for i in report["items"] if "body" in i["answers"]
    ]


def vo(sid: str) -> Callable[[str], str]:
    return n(f"vo-{sid}", tags='["voiceover"]')


def companion(lang: str, *owners: str) -> str:
    return "\n".join(
        note(lang, f"vo-{o}", tags='["voiceover"]', owner=o.split("-")[0]) for o in owners
    )


# ---------------------------------------------------------------------------
# #1216: an id-less shared cell promoted to `subslide`
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="#1216: the bundle parser refuses the id-less anchor (`idless_anchor`, "
    "'run normalize --stamp-ids') before the sequence core runs; the core itself "
    "merges it (test_doc_core::test_shared_cell_promoted_to_subslide_without_an_id)",
)
def test_1216_shared_cell_promoted_to_subslide_without_an_id(
    tmp_path: Path, runner: CliRunner
) -> None:
    def base(lang: str, tags: str = '["keep"]') -> str:
        return deck(lang, s("s0"), code("#include <string>", f"tags={tags}"), code("x = 1"))

    promoted = '["keep", "subslide"]'
    pair = Pair(tmp_path, runner, base("de"), base("en"))
    pair.write(en=base("en", promoted))
    reports = converge(pair)
    assert len(reports) == 2 and reports[0]["items"] == []
    assert pair.text("de") == base("de", promoted)
    assert pair.text("en") == base("en", promoted)


# ---------------------------------------------------------------------------
# #1092: promotion to subslide anchors + mid-pool insertion + a new anchor
# ---------------------------------------------------------------------------


def _streams(lang: str, edited: bool, extra_vo: bool = False) -> str:
    """#1092's three EN shapes in one deck, with voiceovers in groups the edit
    does not touch (the issue's full pass withheld those too)."""
    if not edited:
        return deck(
            lang,
            s("s0"),
            code("values_in = [1, 2]", 'tags=["keep"]'),
            code("sum(values_in)"),
            code("first = 1"),
            code("text_out = []"),
            code("print(text_out)"),
            vo("sum-while"),
            s("s1"),
            code("last = 1"),
            vo("eof"),
        )
    return deck(
        lang,
        s("s0"),
        # shape 1: an existing shared cell gains `subslide` and a slide_id
        code("values_in = [1, 2]", 'tags=["keep", "subslide"] slide_id="sum-while-demo"'),
        code("sum(values_in)"),
        code("first = 1"),
        # shape 2: three cells inserted mid-pool, the next cell promoted
        code("second = 2"),
        code("second += 1"),
        code("print(second)"),
        code("text_out = []", 'tags=["subslide"] slide_id="ostringstream-demo"'),
        code("print(text_out)"),
        vo("sum-while"),
        s("s1"),
        # shape 3: a new id'd anchor followed by new un-id'd cells
        code("scoped_in = []", 'tags=["subslide"] slide_id="sum-for-demo"'),
        code("for v in scoped_in: pass"),
        code("print(sum(scoped_in))"),
        code("last = 1"),
        *([vo("new")] if extra_vo else []),
        vo("eof"),
    )


def test_1092_promotions_and_mid_pool_insertion_converge_in_one_apply(
    tmp_path: Path, runner: CliRunner
) -> None:
    pair = Pair(tmp_path, runner, _streams("de", False), _streams("en", False))
    pair.write(en=_streams("en", True))
    reports = converge(pair)
    first = reports[0]
    # no question; the promoted cells are not framed as a copy plus a removal
    assert first["items"] == []
    changes = [(c["action"], c["key"]) for c in first["changes"]]
    assert ("copy", "id:sum-while-demo") not in changes
    assert ("copy", "id:ostringstream-demo") not in changes
    assert {c["side"] for c in first["changes"]} == {"de"}
    assert len(reports) == 2
    assert pair.text("de") == _streams("de", True)
    assert pair.text("en") == _streams("en", True)


def test_1092_promotions_with_a_translation_answered_in_the_same_pass(
    tmp_path: Path, runner: CliRunner
) -> None:
    """The issue's full pass also answered framed translations, and the gate
    withheld every row. Here a new EN voiceover is answered in the same pass."""
    pair = Pair(tmp_path, runner, _streams("de", False), _streams("en", False))
    pair.write(en=_streams("en", True, extra_vo=True))
    reports = converge(pair, bodies({"id:vo-new": "#\n# - vo-new de"}))
    assert [(a, k) for a, k, _ in questions(reports[0])] == [("translate_new", "id:vo-new")]
    assert len(reports) == 2
    assert pair.text("de") == _streams("de", True, extra_vo=True)
    assert pair.text("en") == _streams("en", True, extra_vo=True)


# ---------------------------------------------------------------------------
# #1187: a move past a cell retagged `subslide` in the same edit
# ---------------------------------------------------------------------------


def _moved(lang: str, edited: bool, extra_vo: bool = False) -> str:
    tail = [vo("new")] if extra_vo else []
    if not edited:
        return deck(lang, s("s0"), n("m1"), code("b = 2"), code("c = 3"), s("s1"))
    return deck(
        lang, s("s0"), code("b = 2"), n("m1", tags='["subslide"]'), code("c = 3"), s("s1"), *tail
    )


def test_1187_move_past_a_retagged_cell_needs_no_question(
    tmp_path: Path, runner: CliRunner
) -> None:
    pair = Pair(tmp_path, runner, _moved("de", False), _moved("en", False))
    pair.write(en=_moved("en", True))
    reports = converge(pair)
    assert reports[0]["items"] == [] and len(reports) == 2
    assert pair.text("de") == _moved("de", True)
    assert pair.text("en") == _moved("en", True)


def test_1187_a_partial_pass_cannot_strand_the_move(tmp_path: Path, runner: CliRunner) -> None:
    """The issue hit partial passes: an apply with a question unanswered landed
    the tag mirror and stranded the pending placement. The sequence core writes
    nothing while a question is open, so the next pass sees the same edit."""
    pair = Pair(tmp_path, runner, _moved("de", False), _moved("en", False))
    pair.write(en=_moved("en", True, extra_vo=True))
    first = pair.report()
    assert [(a, k) for a, k, _ in questions(first)] == [("translate_new", "id:vo-new")]
    held = pair.apply()
    assert held["_exit"] == 1 and held["wrote"] is False
    assert pair.text("de") == _moved("de", False)
    assert pair.report()["items"] == first["items"]
    reports = converge(pair, bodies({"id:vo-new": "#\n# - vo-new de"}))
    assert len(reports) == 2
    assert pair.text("de") == _moved("de", True, extra_vo=True)
    assert pair.text("en") == _moved("en", True, extra_vo=True)


# ---------------------------------------------------------------------------
# #1185: a new slide with an id-less code cell on one half
# ---------------------------------------------------------------------------


def test_1185_a_new_slide_with_an_idless_code_cell_converges_in_one_apply(
    tmp_path: Path, runner: CliRunner
) -> None:
    def text(lang: str, new: bool) -> str:
        cells = [s("nf"), code("# neu\nnew = 2")] if new else []
        return deck(lang, s("s0"), code("x = 1"), *cells, s("s1"))

    pair = Pair(tmp_path, runner, text("de", False), text("en", False))
    pair.write(de=text("de", True))
    reports = converge(pair, bodies({"id:nf": "#\n# ## Slide nf"}))
    # one question, for the slide's text; the code cell is copied with it
    assert questions(reports[0]) == [("translate_new", "id:nf", ["body"])]
    assert len(reports) == 2
    assert pair.text("de") == text("de", True)
    assert pair.text("en") == text("en", True)


# ---------------------------------------------------------------------------
# #1181: a shared cell forked on one half is not banked as clean
# ---------------------------------------------------------------------------


def _forked(lang: str, header: str, body: str) -> str:
    return deck(lang, s("s0"), code(body, header), code("y = 2"))


@pytest.mark.parametrize(
    ("en_header", "en_body"),
    [
        ('lang="en" slide_id="cc"', "# better prose comment\nx = 1"),
        ('lang="en" slide_id="cc"', "# prose comment\nx = 1"),
        ('lang="en" tags=["keep"] slide_id="cc"', "# better prose comment\nx = 1"),
    ],
    ids=["edited", "unedited", "edited-and-retagged"],
)
def test_1181_a_fork_on_one_half_asks_for_the_twin_s_text(
    tmp_path: Path, runner: CliRunner, en_header: str, en_body: str
) -> None:
    shared = "# prose comment\nx = 1"
    pair = Pair(
        tmp_path,
        runner,
        _forked("de", 'slide_id="cc"', shared),
        _forked("en", 'slide_id="cc"', shared),
    )
    pair.write(en=_forked("en", en_header, en_body))
    german = "# bessere Prosa\nx = 1"
    reports = converge(pair, bodies({"id:cc": german}))
    # the fork is a question for the twin's text, never banked as it stands
    assert questions(reports[0]) == [("fork", "id:cc", ["body"])]
    assert len(reports) == 2
    de_header = en_header.replace('lang="en"', 'lang="de"')
    assert pair.text("de") == _forked("de", de_header, german)
    assert pair.text("en") == _forked("en", en_header, en_body)


# ---------------------------------------------------------------------------
# #1179: a new slide inserted beside a same-pass id'd reorder
# ---------------------------------------------------------------------------

G = code("g = 0", 'tags=["keep", "subslide"] slide_id="g"')
H = code("h = 0", 'tags=["keep", "subslide"] slide_id="h"')


@pytest.mark.parametrize(
    ("base", "edited"),
    [
        (
            [s("s"), G, vo("u"), vo("p"), H, code("d0 = 0"), code("d1 = 1")],
            [s("s"), G, vo("p"), vo("u"), s("t"), H, code("d0 = 0"), code("d1 = 1")],
        ),
        (
            [s("s"), G, vo("u"), vo("p"), H, code("d0 = 0"), vo("w"), code("d1 = 1")],
            [s("s"), G, vo("p"), vo("u"), s("t"), H, code("d0 = 0"), code("d1 = 1"), vo("w")],
        ),
    ],
    ids=["no-question", "with-a-move-elsewhere"],
)
def test_1179_a_new_slide_beside_a_reorder_rehomes_nothing(
    tmp_path: Path, runner: CliRunner, base: list, edited: list
) -> None:
    pair = Pair(tmp_path, runner, deck("de", *base), deck("en", *base))
    pair.write(en=deck("en", *edited))
    reports = converge(pair, bodies({"id:t": "#\n# ## Folie t"}))
    assert [(a, k) for a, k, _ in questions(reports[0])] == [("translate_new", "id:t")]
    assert len(reports) == 2
    # both voiceovers stay in `g`, before the new slide, on both halves
    assert pair.text("en") == deck("en", *edited)
    assert pair.text("de") == deck("de", *edited)


# ---------------------------------------------------------------------------
# #1118: voiceover companion order follows the author
# ---------------------------------------------------------------------------


def _slides(lang: str, order: tuple[str, ...]) -> str:
    body = {"s0": "x = 1", "s1": "y = 1"}
    cells: list = []
    for sid in order:
        cells += [s(sid), code(body[sid])]
    return deck(lang, *cells)


@pytest.mark.parametrize(
    ("en_order", "en_companion"),
    [
        (("s1", "s0"), ("s1", "s0")),
        (("s0", "s1"), ("s1", "s0")),
        (("s1", "s0"), ("s1", "s1-new", "s0")),
    ],
    ids=["slides-and-companion-reordered", "companion-only-reordered", "reordered-plus-new"],
)
def test_1118_companion_order_follows_the_edited_half(
    tmp_path: Path, runner: CliRunner, en_order: tuple, en_companion: tuple
) -> None:
    pair = Pair(
        tmp_path,
        runner,
        _slides("de", ("s0", "s1")),
        _slides("en", ("s0", "s1")),
        de_vo=companion("de", "s0", "s1"),
        en_vo=companion("en", "s0", "s1"),
    )
    pair.write(en=_slides("en", en_order), en_vo=companion("en", *en_companion))
    reports = converge(pair, bodies({"id:vo-s1-new": "#\n# - vo-s1-new de"}))
    first = reports[0]
    if "s1-new" in en_companion:
        assert questions(first) == [("translate_new", "id:vo-s1-new", ["body"])]
    else:
        # the companion's order is a change the report shows, never silent
        assert first["items"] == []
        assert ("mirror_order", "the companion's cell order") in [
            (c["action"], c["detail"]) for c in first["changes"]
        ]
    assert len(reports) == 2
    assert pair.text("de") == _slides("de", en_order)
    assert pair.text("de_vo") == companion("de", *en_companion)
    assert pair.text("en_vo") == companion("en", *en_companion)
