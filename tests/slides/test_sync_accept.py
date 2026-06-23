"""``clm slides sync accept`` — validated, model-free write-back (epic #440).

``accept`` takes the answer an agent produced for a framed ``task``, runs it through
the deterministic validator the task named, and writes it to **both** split halves
iff it passes — never calling a model. These tests cover the accepted kinds — ``add``
(a translated new slide), ``realign`` (a drifted-id region re-identified from the
agent's alignment map), and ``edit`` (a drifted localized cell reconciled: markdown /
narrative via the judge verdict, code via the re-translated body) — including the
slide_id-less edits (a narrative companion #403, an id-less localized cell #365) applied
in the engine's scoped mode so a co-drifted sibling is left untouched — plus the
rejection / unavailable paths (which must write nothing) and the CLI surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_accept import (
    AcceptRejected,
    AcceptUnavailable,
    accept_answer,
)
from clm.slides.sync_plan import build_sync_plan, watermark_rows
from clm.slides.sync_report import ReconciliationItem, build_report

# ---------------------------------------------------------------------------
# Deck builders (mirroring the established sync-test shapes)
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


def _code_idd_neutral(sid: str, body: str) -> str:
    return f'# %% tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    # Resolve so the watermark keys (str(path)) match the CLI's path.resolve().
    de_path = (tmp_path / "deck_x.de.py").resolve()
    en_path = (tmp_path / "deck_x.en.py").resolve()
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def _code_localized(lang: str, sid: str, body: str) -> str:
    """A keyed *localized* code cell (lang-tagged, so comments differ per half)."""
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _voiceover_idless(lang: str, body: str) -> str:
    """An id-less narrative (voiceover) companion — a slide_id-less prose edit source."""
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"]\n{body}\n'


def _idless_code(lang: str, body: str) -> str:
    """A hash-only id-less *localized* code cell (no slide_id, no nameable construct)."""
    return f'# %% lang="{lang}"\n{body}\n'


def _idless_md(lang: str, body: str) -> str:
    """A hash-only id-less *localized* markdown cell (no slide_id, no narrative role)."""
    return f'# %% [markdown] lang="{lang}"\n# {body}\n'


def _edit_by_direction(plan, direction: str) -> ReconciliationItem:  # noqa: ANN001
    """The single ``edit`` report item flowing ``direction`` (asserts exactly one)."""
    report = build_report(plan, with_excerpts=True)
    edits = [
        it
        for it in (*report.assisted, *report.ambiguity)
        if it.kind == "edit" and it.direction == direction
    ]
    assert len(edits) == 1, [(it.kind, it.role, it.direction, it.slide_id) for it in edits]
    return edits[0]


def _cell_by_id(path: Path, slide_id: str):  # noqa: ANN202
    for c in parse_cells(path.read_text(encoding="utf-8")):
        if c.metadata.slide_id == slide_id:
            return c
    return None


def _seeded_edit_plan(tmp_path: Path, de0: str, en0: str, de1: str, en1: str):  # noqa: ANN202
    """Seed a watermark at (de0, en0), write the post-edit (de1, en1), return the plan."""
    de_path, en_path = _pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
    cache.close()
    return de_path, en_path, plan


def _the_edit(plan) -> ReconciliationItem:  # noqa: ANN001
    """The single ``edit`` report item (asserts there is exactly one)."""
    report = build_report(plan, with_excerpts=True)
    edits = [it for it in (*report.assisted, *report.ambiguity) if it.kind == "edit"]
    assert len(edits) == 1, [(it.kind, it.role, it.slide_id) for it in edits]
    return edits[0]


# ---------------------------------------------------------------------------
# Fixtures: an `add` plan and a stuck `realign` plan, both watermark-baselined
# ---------------------------------------------------------------------------


def _add_plan(tmp_path: Path):
    """A consistent pair with a brand-new id-less DE slide appended → one `add`."""
    de_path, en_path = _pair(tmp_path, _slide("de", "a", "# ## A"), _slide("en", "a", "# ## A"))
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    de_path.write_text(
        _slide("de", "a", "# ## A") + _slide_idless("de", "# ## Neues Thema"), encoding="utf-8"
    )
    plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
    cache.close()
    return de_path, en_path, plan


def _realign_plan(tmp_path: Path):
    """Seed a watermark, then split+RENAME the def on both decks → realign residue.

    Identical to the Phase-5 ``--llm-recover`` fixture: the deterministic id-migration
    is stuck (the def was renamed), so the region surfaces as a ``realign`` item.
    """
    base_def = 'def my_fun():\n    print("foo")'
    de0 = _slide("de", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    en0 = _slide("en", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    de_path, en_path = _pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    renamed = 'def my_function():\n    time.sleep(1)\n    print("foo")'
    de_path.write_text(
        _slide("de", "g", "# ## G erweitert")  # de narrative edit -> direction de->en
        + _code_idd_neutral("def-my-fun", "import time")
        + _code_shared(renamed),
        encoding="utf-8",
    )
    en_path.write_text(
        _slide("en", "g", "# ## G")
        + _code_idd_neutral("def-my-fun", "import time")
        + _code_shared(renamed),
        encoding="utf-8",
    )
    plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
    cache.close()
    return de_path, en_path, plan


# ---------------------------------------------------------------------------
# accept_answer — add
# ---------------------------------------------------------------------------


class TestAcceptAdd:
    def test_accepts_a_translated_new_slide_on_both_halves(self, tmp_path: Path):
        de_path, en_path, plan = _add_plan(tmp_path)
        assert plan.count("add") == 1
        result = accept_answer(plan, "add-de-en-s1", {"translated_body": "# ## New Topic"})

        assert result.applied and result.kind == "add" and result.changed == 1
        # EN-authority: the id is slugged from the translated heading, stamped on BOTH.
        de_new, en_new = _cell_by_id(de_path, "new-topic"), _cell_by_id(en_path, "new-topic")
        assert de_new is not None and en_new is not None  # de_id == en_id
        assert "Neues Thema" in de_new.content  # source body unchanged, just stamped
        assert "New Topic" in en_new.content  # the agent's translated counterpart

    def test_rejects_a_non_conforming_translation_answer(self, tmp_path: Path):
        de_path, en_path, plan = _add_plan(tmp_path)
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        with pytest.raises(AcceptRejected, match="translated_body"):
            accept_answer(plan, "add-de-en-s1", {"wrong_key": "x"})
        # A rejected answer writes NOTHING.
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before


# ---------------------------------------------------------------------------
# accept_answer — realign (the agent-first `--llm-recover` write-back)
# ---------------------------------------------------------------------------


class TestAcceptRealign:
    def test_accepts_the_alignment_map_on_both_halves(self, tmp_path: Path):
        de_path, en_path, plan = _realign_plan(tmp_path)
        # The agent's map: the import is genuinely new; the renamed def is the
        # def-my-fun continuation (the same map the Phase-5 recoverer returns).
        result = accept_answer(plan, "realign-def-my-fun", {"0": "new", "1": "def-my-fun"})

        assert result.applied and result.kind == "realign"
        assert result.changed == 4  # 2 cells re-identified on each of 2 decks
        for path in (de_path, en_path):
            ids = {
                c.metadata.slide_id: c
                for c in parse_cells(path.read_text(encoding="utf-8"))
                if c.metadata.slide_id
            }
            assert "def my_function" in ids["def-my-fun"].content  # id followed the rename
            assert "import time" in ids["import-time"].content  # orphan got a content slug

    def test_rejects_an_invalid_alignment_map_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path, plan = _realign_plan(tmp_path)
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        # "not-a-base-id" is not a base slide_id → validate_alignment rejects it.
        with pytest.raises(AcceptRejected, match="rejected"):
            accept_answer(plan, "realign-def-my-fun", {"0": "not-a-base-id", "1": "def-my-fun"})
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before


# ---------------------------------------------------------------------------
# accept_answer — edit (keyed: markdown via judge, code via re-translation)
# ---------------------------------------------------------------------------


class TestAcceptEdit:
    def test_keyed_markdown_update_writes_target(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),  # DE markdown edit
            _slide("en", "a", "# ## A"),
        )
        item = _the_edit(plan)
        assert item.slide_id == "a"
        result = accept_answer(
            plan, item.item, {"verdict": "update", "proposed_text": "# ## A more"}
        )

        assert result.applied and result.kind == "edit" and result.changed == 1
        assert "A more" in _cell_by_id(en_path, "a").content  # the agent's text reached EN
        assert "A erweitert" in _cell_by_id(de_path, "a").content  # source half untouched

    def test_keyed_markdown_in_sync_is_a_no_op(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),
            _slide("en", "a", "# ## A"),
        )
        en_before = en_path.read_text(encoding="utf-8")
        item = _the_edit(plan)
        result = accept_answer(plan, item.item, {"verdict": "in_sync", "proposed_text": ""})

        assert result.applied and result.changed == 0  # accepted, nothing written
        assert en_path.read_text(encoding="utf-8") == en_before

    def test_keyed_code_update_retranslates(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "g", "# ## G") + _code_localized("de", "c1", 'print("eins")'),
            _slide("en", "g", "# ## G") + _code_localized("en", "c1", 'print("one")'),
            _slide("de", "g", "# ## G") + _code_localized("de", "c1", 'print("zwei")'),  # DE edit
            _slide("en", "g", "# ## G") + _code_localized("en", "c1", 'print("one")'),
        )
        item = _the_edit(plan)
        assert item.slide_id == "c1"
        # A code edit takes the re-translated body (validator "translation"), not a verdict.
        result = accept_answer(plan, item.item, {"translated_body": 'print("two")'})

        assert result.applied and result.changed == 1
        assert 'print("two")' in _cell_by_id(en_path, "c1").content
        assert 'print("zwei")' in _cell_by_id(de_path, "c1").content  # source untouched

    def test_rejects_a_bad_verdict_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),
            _slide("en", "a", "# ## A"),
        )
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        item = _the_edit(plan)
        with pytest.raises(AcceptRejected, match="verdict"):
            accept_answer(plan, item.item, {"proposed_text": "# ## A more"})  # no verdict
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before

    def test_rejects_an_update_with_empty_text(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),
            _slide("en", "a", "# ## A"),
        )
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        item = _the_edit(plan)
        with pytest.raises(AcceptRejected, match="proposed_text"):
            accept_answer(plan, item.item, {"verdict": "update", "proposed_text": "   "})
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before


# ---------------------------------------------------------------------------
# accept_answer — slide_id-less edit (narrative #403 + id-less localized #365)
# ---------------------------------------------------------------------------


class TestAcceptSlideIdLessEdit:
    """A drifted cell with no ``slide_id`` is accepted via the engine's scoped mode.

    A narrative (``voiceover`` / ``notes``) cell is reconciled by the judge; an id-less
    localized code cell by re-translation. ``accept`` prunes the plan to that one edit and
    applies it with no structural pass, so a *co-drifted* sibling in the same group is
    never touched (the regression these tests pin).
    """

    def test_narrative_voiceover_update_writes_target(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo liebe Welt"),  # VO edit
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
        )
        item = _the_edit(plan)
        assert item.slide_id is None and item.role == "voiceover"  # narrative is anchor-keyed
        result = accept_answer(
            plan, item.item, {"verdict": "update", "proposed_text": "Hello dear world"}
        )

        assert result.applied and result.kind == "edit" and result.changed == 1
        en_text = en_path.read_text(encoding="utf-8")
        assert "Hello dear world" in en_text  # the agent's reconciled body reached EN
        assert "Hallo liebe Welt" in de_path.read_text(encoding="utf-8")  # source untouched

    def test_narrative_in_sync_is_a_no_op(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo liebe Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
        )
        en_before = en_path.read_text(encoding="utf-8")
        item = _the_edit(plan)
        result = accept_answer(plan, item.item, {"verdict": "in_sync", "proposed_text": ""})

        assert result.applied and result.changed == 0  # accepted, nothing written
        assert en_path.read_text(encoding="utf-8") == en_before

    def test_idless_localized_code_retranslates_only_the_targeted_cell(self, tmp_path: Path):
        # Two id-less localized code cells; DE edits cell 0, EN edits cell 1 (each a
        # one-sided winner → two resolvable localized-code edits). Accept ONLY cell 0's
        # de->en edit: the targeted EN cell is re-translated, while the co-drifted cell 1
        # (an en->de edit still pending) is left byte-for-byte on BOTH halves — proving
        # the scoped apply skips the structural pass that would otherwise re-translate it.
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "g", "# ## G") + _idless_code("de", "a = 1") + _idless_code("de", "b = 2"),
            _slide("en", "g", "# ## G") + _idless_code("en", "a = 1") + _idless_code("en", "b = 2"),
            _slide("de", "g", "# ## G")
            + _idless_code("de", "a = 1  # DE")
            + _idless_code("de", "b = 2"),
            _slide("en", "g", "# ## G")
            + _idless_code("en", "a = 1")
            + _idless_code("en", "b = 2  # EN"),
        )
        item = _edit_by_direction(plan, "de->en")
        assert item.slide_id is None and item.role == "localized-code"
        result = accept_answer(plan, item.item, {"translated_body": "a = 1  # XL"})

        assert result.applied and result.changed == 1
        en_text, de_text = (
            en_path.read_text(encoding="utf-8"),
            de_path.read_text(encoding="utf-8"),
        )
        assert "a = 1  # XL" in en_text  # cell 0 EN twin re-translated from the answer
        assert "a = 1  # DE" in de_text  # cell 0 DE source untouched
        # The co-drifted sibling (cell 1, en->de) must be untouched on both halves:
        assert "b = 2  # EN" in en_text  # EN's own edit to cell 1 survives verbatim
        assert "b = 2  # EN" not in de_text  # NOT propagated onto DE (no structural pass)
        assert "b = 2\n" in de_text  # DE cell 1 still its baseline body

    def test_idless_localized_markdown_update_via_judge(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "g", "# ## G") + _idless_md("de", "Eins") + _idless_md("de", "Zwei"),
            _slide("en", "g", "# ## G") + _idless_md("en", "One") + _idless_md("en", "Two"),
            _slide("de", "g", "# ## G")
            + _idless_md("de", "Eins geaendert")
            + _idless_md("de", "Zwei"),
            _slide("en", "g", "# ## G") + _idless_md("en", "One") + _idless_md("en", "Two changed"),
        )
        item = _edit_by_direction(plan, "de->en")
        assert item.slide_id is None and item.role == "localized-markdown"
        result = accept_answer(
            plan, item.item, {"verdict": "update", "proposed_text": "# One changed"}
        )

        assert result.applied and result.changed == 1
        assert "# One changed" in en_path.read_text(encoding="utf-8")

    def test_rejects_a_bad_narrative_verdict_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo liebe Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
        )
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        item = _the_edit(plan)
        with pytest.raises(AcceptRejected, match="verdict"):
            accept_answer(plan, item.item, {"proposed_text": "Hi there"})  # no verdict
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before


# ---------------------------------------------------------------------------
# accept_answer — unavailable kinds (honest hand-off, no write)
# ---------------------------------------------------------------------------


class TestAcceptUnavailable:
    def test_both_sided_conflict_is_unavailable(self, tmp_path: Path):
        # A both-sided id-less localized edit is a tier-3 conflict (no clear winner) —
        # accept refuses it (resolve-a-side is the agent's call), writing nothing.
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "g", "# ## G") + _idless_code("de", "x = 1"),
            _slide("en", "g", "# ## G") + _idless_code("en", "x = 1"),
            _slide("de", "g", "# ## G") + _idless_code("de", "x = 1  # DE"),
            _slide("en", "g", "# ## G") + _idless_code("en", "x = 1  # EN"),
        )
        report = build_report(plan, with_excerpts=True)
        conflicts = [it for it in report.ambiguity if it.kind == "conflict"]
        assert conflicts, [(it.kind, it.tier) for it in report.ambiguity]
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        with pytest.raises(AcceptUnavailable):
            accept_answer(plan, conflicts[0].item, {"verdict": "update", "proposed_text": "x"})
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before

    def test_unknown_item_raises_keyerror(self, tmp_path: Path):
        _de, _en, plan = _add_plan(tmp_path)
        with pytest.raises(KeyError):
            accept_answer(plan, "realign-nope", {"0": "new"})


# ---------------------------------------------------------------------------
# CLI surface (`clm slides sync accept`)
# ---------------------------------------------------------------------------


def _run(*args: str, stdin: str | None = None) -> tuple[int, str]:
    from clm.cli.commands.slides.sync import slides_sync_group

    res = CliRunner().invoke(slides_sync_group, list(args), input=stdin)
    return res.exit_code, res.output


class TestAcceptCli:
    def test_realign_happy_path_via_stdin(self, tmp_path: Path):
        de_path, en_path, _plan = _realign_plan(tmp_path)
        answer = json.dumps({"0": "new", "1": "def-my-fun"})
        code, out = _run(
            "accept",
            str(de_path),
            "--item",
            "realign-def-my-fun",
            "--answer",
            "-",
            "--use-watermark",
            "--cache-dir",
            str(tmp_path),
            stdin=answer,
        )
        assert code == 0, out
        assert "accepted realign-def-my-fun" in out
        de_ids = {
            c.metadata.slide_id: c
            for c in parse_cells(de_path.read_text(encoding="utf-8"))
            if c.metadata.slide_id
        }
        assert "def my_function" in de_ids["def-my-fun"].content

    def test_edit_markdown_happy_path_via_stdin(self, tmp_path: Path):
        de_path, en_path, plan = _seeded_edit_plan(
            tmp_path,
            _slide("de", "a", "# ## A"),
            _slide("en", "a", "# ## A"),
            _slide("de", "a", "# ## A erweitert"),
            _slide("en", "a", "# ## A"),
        )
        item_id = _the_edit(plan).item
        answer = json.dumps({"verdict": "update", "proposed_text": "# ## A more"})
        code, out = _run(
            "accept",
            str(de_path),
            "--item",
            item_id,
            "--answer",
            "-",
            "--use-watermark",
            "--cache-dir",
            str(tmp_path),
            stdin=answer,
        )
        assert code == 0, out
        assert f"accepted {item_id}" in out
        assert "A more" in _cell_by_id(en_path, "a").content

    def test_rejected_map_exits_2_and_writes_nothing(self, tmp_path: Path):
        de_path, en_path, _plan = _realign_plan(tmp_path)
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        answer = json.dumps({"0": "not-a-base-id", "1": "def-my-fun"})
        code, out = _run(
            "accept",
            str(de_path),
            "--item",
            "realign-def-my-fun",
            "--answer",
            "-",
            "--use-watermark",
            "--cache-dir",
            str(tmp_path),
            stdin=answer,
        )
        assert code == 2, out
        assert "not accepted (rejected)" in out
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before

    def test_unknown_item_is_a_usage_error(self, tmp_path: Path):
        de_path, _en, _plan = _realign_plan(tmp_path)
        code, out = _run("accept", str(de_path), "--item", "edit-nope", "--answer", "-", stdin="{}")
        assert code == 2, out
        assert "no report item" in out.lower()

    def test_directory_is_rejected(self, tmp_path: Path):
        _realign_plan(tmp_path)
        code, out = _run("accept", str(tmp_path), "--item", "x", "--answer", "-", stdin="{}")
        assert code == 2, out
        assert "single deck pair" in out.lower()
