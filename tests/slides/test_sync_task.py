"""``clm slides sync task`` — framed, model-free reconciliation tasks (epic #440).

``task`` emits everything a model needs to reconcile one tier-2/3 item (the system
prompt, the ready-to-send prompt, the inputs, the answer schema, and the validator
``accept`` will run) **without the engine calling a model**. These tests cover the
framed kinds — ``edit`` (the sync judge), ``add`` (the translator), ``realign`` (the
alignment recoverer), and ``mint`` / ``adopt`` / ``reconcile`` (the correspondence
verifier — cold-start positional pairs or the reconcile cross-product) — plus the stable
item-id contract both ``task`` and ``accept`` address items by, and the CLI surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.sync_prompts import SYNC_SYSTEM_PROMPT
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import Proposal, SyncPlan, build_sync_plan, watermark_rows
from clm.slides.sync_recover import CORRESPONDENCE_SYSTEM_PROMPT, RECOVERY_SYSTEM_PROMPT
from clm.slides.sync_report import (
    ReconciliationItem,
    ReconciliationReport,
    _assign_item_ids,
    build_report,
)
from clm.slides.sync_task import (
    CORRESPONDENCE_ANSWER_SCHEMA,
    EDIT_ANSWER_SCHEMA,
    TRANSLATION_ANSWER_SCHEMA,
    TaskUnavailable,
    build_task,
    build_tasks,
)
from clm.slides.sync_translate import OpenRouterSlideTranslator, build_translation_system_prompt

# ---------------------------------------------------------------------------
# Deck builders (mirroring the established sync-test shapes)
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _slide_idless(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"]\n{body}\n'


def _code_idd_neutral(sid: str, body: str) -> str:
    return f'# %% tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_localized(lang: str, sid: str, body: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _voiceover_idless(lang: str, body: str) -> str:
    """An id-less narrative (voiceover) companion — a slide_id-less prose edit source."""
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"]\n{body}\n'


def _idless_code(lang: str, body: str) -> str:
    """A hash-only id-less *localized* code cell (no slide_id, no nameable construct)."""
    return f'# %% lang="{lang}"\n{body}\n'


def _pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck_x.de.py"
    en_path = tmp_path / "deck_x.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _commit_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    """Write + commit a split pair so ``build_sync_plan`` resolves a git-HEAD baseline."""
    de_path, en_path = _pair(tmp_path, de, en)

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True, capture_output=True, text=True
        )

    _git("init", "-q")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "Test")
    _git("add", "-A")
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    return de_path, en_path


def _seeded_plan(tmp_path: Path, de0: str, en0: str, de1: str, en1: str) -> SyncPlan:
    """Seed a watermark at (de0, en0), write the post-edit (de1, en1), return the plan."""
    de_path, en_path = _pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    try:
        return build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
    finally:
        cache.close()


def _the_edit_item(plan: SyncPlan, direction: str | None = None) -> ReconciliationItem:
    """The single ``edit`` report item (optionally filtered by direction)."""
    edits = [
        it
        for it in build_report(plan, with_excerpts=True).assisted
        if it.kind == "edit" and (direction is None or it.direction == direction)
    ]
    assert len(edits) == 1, [(it.role, it.direction, it.slide_id) for it in edits]
    return edits[0]


def _real_plan(de_path: Path, en_path: Path, *proposals: Proposal) -> SyncPlan:
    plan = SyncPlan(de_path=de_path, en_path=en_path, baseline_source="watermark")
    plan.proposals = list(proposals)
    return plan


# ---------------------------------------------------------------------------
# Stable item ids (sync_report) — the address task/accept select an item by
# ---------------------------------------------------------------------------


def _item(**kw) -> ReconciliationItem:
    kw.setdefault("tier", "assisted")
    return ReconciliationItem(**kw)


class TestItemIds:
    def test_slide_id_keyed_and_positional_fallback(self):
        report = ReconciliationReport(
            de_path="a",
            en_path="b",
            baseline_source="git-head",
            in_sync=0,
            assisted=[
                _item(kind="edit", slide_id="s2", direction="de->en"),
                _item(kind="add", direction="de->en", source_position=0),
            ],
        )
        _assign_item_ids(report)
        assert [i.item for i in report.assisted] == ["edit-s2", "add-de-en-s0"]

    def test_collisions_get_a_numeric_suffix(self):
        report = ReconciliationReport(
            de_path="a",
            en_path="b",
            baseline_source="git-head",
            in_sync=0,
            ambiguity=[
                _item(
                    tier="ambiguity",
                    kind="conflict",
                    role="code",
                    source_position=0,
                    target_position=0,
                ),
                _item(
                    tier="ambiguity",
                    kind="conflict",
                    role="code",
                    source_position=0,
                    target_position=0,
                ),
            ],
        )
        _assign_item_ids(report)
        ids = [i.item for i in report.ambiguity]
        assert ids[0] != ids[1] and ids[1].endswith("-2")

    def test_ids_stable_across_two_report_builds(self, tmp_path: Path):
        de = "\n".join([_slide("de", "s1", "# ## eins"), _slide("de", "s2", "# ## zwei")])
        en = "\n".join([_slide("en", "s1", "# ## one"), _slide("en", "s2", "# ## two")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s2",
                source_position=1,
                target_position=1,
            ),
        )
        first = [i.item for i in build_report(plan, with_excerpts=True).assisted]
        second = [i.item for i in build_report(plan, with_excerpts=True).assisted]
        assert first == second == ["edit-s2"]


# ---------------------------------------------------------------------------
# build_task — per-kind framing
# ---------------------------------------------------------------------------


class TestBuildTask:
    def test_edit_frames_the_judge_task(self, tmp_path: Path):
        de = "\n".join([_slide("de", "s1", "# ## eins"), _slide("de", "s2", "# ## zwei NEU")])
        en = "\n".join([_slide("en", "s1", "# ## one"), _slide("en", "s2", "# ## two")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="slide",
                direction="de->en",
                slide_id="s2",
                source_position=1,
                target_position=1,
            ),
        )
        task = build_task(plan, "edit-s2")
        assert task.validator == "edit"
        assert task.instructions == SYNC_SYSTEM_PROMPT
        assert task.answer_schema == EDIT_ANSWER_SCHEMA
        assert "zwei NEU" in task.prompt and "two" in task.prompt  # both sides framed
        assert task.inputs["source_lang"] == "de" and task.inputs["target_lang"] == "en"

    def test_code_edit_frames_the_translation_task(self, tmp_path: Path):
        # A code edit is reconciled by re-translation, so it is framed as a translation
        # task (validator "translation", answer {translated_body}) — NOT the prose judge.
        de = "\n".join([_slide("de", "s1", "# ## eins"), _code_localized("de", "c1", 'x = "eins"')])
        en = "\n".join([_slide("en", "s1", "# ## one"), _code_localized("en", "c1", 'x = "one"')])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="edit",
                role="code",
                direction="de->en",
                slide_id="c1",
                source_position=1,
                target_position=1,
            ),
        )
        task = build_task(plan, "edit-c1")
        assert task.validator == "translation"
        assert task.answer_schema == TRANSLATION_ANSWER_SCHEMA
        assert task.inputs["role"] == "code"
        assert "English" in task.instructions  # the translation system prompt targets EN
        assert 'x = "eins"' in task.prompt  # the source code body IS the prompt

    def test_narrative_edit_frames_the_judge_task(self, tmp_path: Path):
        # A slide_id-less narrative (voiceover) edit is prose, so it frames as the judge
        # task — the drifted source narrative must reach the prompt (its excerpt resolves).
        plan = _seeded_plan(
            tmp_path,
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo Welt"),
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
            _slide("de", "a", "# ## A") + _voiceover_idless("de", "Hallo liebe Welt"),  # VO edit
            _slide("en", "a", "# ## A") + _voiceover_idless("en", "Hello world"),
        )
        item = _the_edit_item(plan)
        assert item.slide_id is None and item.role == "voiceover"
        task = build_task(plan, item.item)
        assert task.validator == "edit"  # prose → the sync judge
        assert task.answer_schema == EDIT_ANSWER_SCHEMA
        assert "Hallo liebe Welt" in task.prompt  # the drifted source narrative IS framed
        assert "Hello world" in task.prompt  # and the stale target counterpart

    def test_idless_localized_code_edit_frames_the_translation_task(self, tmp_path: Path):
        # A slide_id-less id-less-localized CODE edit re-translates, so it frames as a
        # translation task (validator "translation"), mirroring the engine's reconciliation.
        plan = _seeded_plan(
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
        item = _the_edit_item(plan, direction="de->en")
        assert item.slide_id is None and item.role == "localized-code"
        task = build_task(plan, item.item)
        assert task.validator == "translation"
        assert task.answer_schema == TRANSLATION_ANSWER_SCHEMA
        assert task.inputs["role"] == "code"
        assert "a = 1  # DE" in task.prompt  # the drifted source code body IS the prompt

    def test_add_frames_the_translation_task(self, tmp_path: Path):
        de = "\n".join([_slide("de", "s1", "# ## eins"), _slide("de", "s2", "# ## NEUE Folie")])
        en = "\n".join([_slide("en", "s1", "# ## one")])
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(
                kind="add",
                role="slide",
                direction="de->en",
                slide_id=None,
                source_position=1,
                target_position=None,
            ),
        )
        task = build_task(plan, "add-de-en-s1")
        assert task.validator == "translation"
        assert task.inputs["target_lang"] == "en"  # a new DE slide is translated to EN
        assert "NEUE Folie" in task.prompt  # the user prompt IS the source body
        assert "English" in task.instructions  # the translation system prompt targets EN

    def test_unknown_item_id_raises_keyerror(self, tmp_path: Path):
        de, en = _slide("de", "s1", "# ## eins"), _slide("en", "s1", "# ## one")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(de_path, en_path)
        with pytest.raises(KeyError):
            build_task(plan, "edit-nope")

    def test_conflict_is_unavailable_for_a_model_task(self, tmp_path: Path):
        de, en = _slide("de", "s1", "# ## eins"), _slide("en", "s1", "# ## one")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="conflict", role="slide", direction=None, slide_id="s1"),
        )
        with pytest.raises(TaskUnavailable, match="resolve it by hand|judgement|ambiguity"):
            build_task(plan, "conflict-s1")

    def test_mint_frames_the_correspondence_task(self, tmp_path: Path):
        # A both-id-less cold pair frames the deck-level correspondence task: every
        # aligned slide pair at once, answered by a {pair_index -> bool} verdict map.
        de = _slide_idless("de", "# ## Einleitung") + _slide_idless("de", "# ## Variablen")
        en = _slide_idless("en", "# ## Introduction") + _slide_idless("en", "# ## Variables")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        task = build_task(plan, "mint")
        assert task.kind == "mint"
        assert task.validator == "correspondence"
        assert task.instructions == CORRESPONDENCE_SYSTEM_PROMPT
        assert task.answer_schema == CORRESPONDENCE_ANSWER_SCHEMA
        assert len(task.inputs["pairs"]) == 2  # both aligned slide pairs ride in inputs
        assert "Einleitung" in task.prompt and "Introduction" in task.prompt  # both halves

    def test_adopt_frames_the_correspondence_task(self, tmp_path: Path):
        # A half-id'd cold pair (EN id'd, DE id-less) frames the same correspondence task.
        de = _slide_idless("de", "# ## A") + _slide_idless("de", "# ## B")
        en = _slide("en", "s1", "# ## A") + _slide("en", "s2", "# ## B")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = build_sync_plan(de_path, en_path, watermark_cache=None, provider_available=True)
        task = build_task(plan, "adopt-en-de")
        assert task.kind == "adopt" and task.validator == "correspondence"
        assert task.answer_schema == CORRESPONDENCE_ANSWER_SCHEMA
        assert len(task.inputs["pairs"]) == 2

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_reconcile_frames_the_correspondence_task(self, tmp_path: Path):
        # A committed mismatched-id pair (s1 shared, B id'd d1/e1) frames the DE×EN
        # suspect cross-product as a correspondence task (one task for the bucket).
        de_path, en_path = _commit_pair(
            tmp_path,
            _slide("de", "s1", "# ## A") + _slide("de", "d1", "# ## B"),
            _slide("en", "s1", "# ## A") + _slide("en", "e1", "# ## B"),
        )
        plan = build_sync_plan(de_path, en_path, provider_available=True)
        assert plan.count("reconcile") == 2
        recon = next(
            it for it in build_report(plan, with_excerpts=True).assisted if it.kind == "reconcile"
        )
        task = build_task(plan, recon.item)
        assert task.kind == "reconcile" and task.validator == "correspondence"
        assert task.answer_schema == CORRESPONDENCE_ANSWER_SCHEMA
        assert len(task.inputs["pairs"]) == 1  # the 1×1 DE×EN cross-product

    def test_one_sided_reconcile_is_unavailable(self, tmp_path: Path):
        # A degenerate ONE-DIRECTIONAL reconcile bucket has no cross-product to verify —
        # its suspects are one-sided slides, framed as adds, so the task is unavailable.
        de, en = _slide("de", "s1", "# ## eins"), _slide("en", "s1", "# ## one")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="reconcile", role="slide", direction="de->en", slide_id="s1"),
        )
        with pytest.raises(TaskUnavailable, match="one-sided|cross-product|adds"):
            build_task(plan, "reconcile-s1")


# ---------------------------------------------------------------------------
# build_tasks — partition framed vs hand-judged
# ---------------------------------------------------------------------------


def test_build_tasks_partitions_framed_and_unframed(tmp_path: Path):
    de = "\n".join([_slide("de", "s1", "# ## eins NEU"), _slide("de", "s2", "# ## NEUE")])
    en = "\n".join([_slide("en", "s1", "# ## one")])
    de_path, en_path = _pair(tmp_path, de, en)
    plan = _real_plan(
        de_path,
        en_path,
        Proposal(
            kind="edit",
            role="slide",
            direction="de->en",
            slide_id="s1",
            source_position=0,
            target_position=0,
        ),
        Proposal(
            kind="add",
            role="slide",
            direction="de->en",
            slide_id=None,
            source_position=1,
            target_position=None,
        ),
        Proposal(kind="conflict", role="slide", direction=None, slide_id="s9"),
    )
    tasks, unframed = build_tasks(plan)
    assert sorted(t.kind for t in tasks) == ["add", "edit"]
    assert [it.kind for it in unframed] == ["conflict"]


# ---------------------------------------------------------------------------
# realign — the alignment-recovery task (the agent-first `--llm-recover`)
# ---------------------------------------------------------------------------


def _seed(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


def _stuck_renamed_split(tmp_path: Path):
    """Seed a watermark, then split+rename the def on both decks → recovery territory."""
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
    return de_path, en_path, cache


def test_realign_frames_the_alignment_recovery_task(tmp_path: Path):
    de_path, en_path, cache = _stuck_renamed_split(tmp_path)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
    finally:
        cache.close()

    task = build_task(plan, "realign-def-my-fun")
    assert task.kind == "realign"
    assert task.validator == "alignment"
    assert task.instructions == RECOVERY_SYSTEM_PROMPT
    assert task.direction is None  # the region is symmetric across both halves
    # The body-free regions ride in inputs and in the prompt (the recoverer's view).
    assert task.inputs["base_region"] and task.inputs["current_region"]
    assert "BASE" in task.prompt and "CURRENT" in task.prompt
    # The answer is an index -> assignment map.
    assert task.answer_schema["additionalProperties"] == {"type": "string"}


# ---------------------------------------------------------------------------
# Translation-prompt refactor: the factored builder must match the translator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["slide", "code", "title"])
@pytest.mark.parametrize("prog_lang", ["python", "csharp"])
@pytest.mark.parametrize("guidance", ["", "Use 'Sie'. Dictionary -> Wörterbuch."])
def test_build_translation_system_prompt_matches_translator(role, prog_lang, guidance):
    translator = OpenRouterSlideTranslator(
        prog_lang=prog_lang, guidance_by_lang=({"en": guidance} if guidance else {})
    )
    factored = build_translation_system_prompt(
        role=role, source_lang="de", target_lang="en", prog_lang=prog_lang, guidance=guidance
    )
    assert factored == translator._system_message(role, "de", "en")


# ---------------------------------------------------------------------------
# CLI surface (`clm slides sync task`)
# ---------------------------------------------------------------------------


def _cli_pair(folder: Path) -> Path:
    neutral = '# %% tags=["code"]\nprint("hello")\n\n'
    de = neutral + '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n#\n# ## Titel\n'
    en = neutral + '# %% [markdown] lang="en" tags=["slide"] slide_id="a"\n#\n# ## Title\n'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "slides_x.de.py").write_text(de, encoding="utf-8")
    (folder / "slides_x.en.py").write_text(en, encoding="utf-8")
    return folder / "slides_x.de.py"


def _run(*args: str) -> tuple[int, str]:
    from clm.cli.commands.slides.sync import slides_sync_group

    res = CliRunner().invoke(slides_sync_group, list(args))
    return res.exit_code, res.output


class TestTaskCli:
    def test_emits_an_edit_task_after_a_watermark_edit(self, tmp_path: Path):
        cache = tmp_path / "cache"
        de_path = _cli_pair(tmp_path / "topic_010_intro")
        # Bless the consistent pair, then edit the DE slide so EN drifts behind it.
        code, out = _run("baseline", "bless", str(de_path), "--cache-dir", str(cache))
        assert code == 0, out
        de_path.write_text(
            de_path.read_text(encoding="utf-8").replace("## Titel", "## Titel (erweitert)"),
            encoding="utf-8",
        )

        code, out = _run(
            "task", str(de_path), "--use-watermark", "--cache-dir", str(cache), "--json"
        )
        assert code == 0, out
        payload = json.loads(out)
        kinds = {t["kind"] for t in payload["tasks"]}
        assert "edit" in kinds
        edit = next(t for t in payload["tasks"] if t["kind"] == "edit")
        assert edit["validator"] == "edit"
        assert "erweitert" in edit["prompt"]

    def test_clean_deck_has_no_tasks(self, tmp_path: Path):
        de_path = _cli_pair(tmp_path / "topic_010_intro")
        code, out = _run("task", str(de_path), "--json")
        assert code == 0, out
        payload = json.loads(out)
        assert payload["tasks"] == [] and payload["unframed"] == []

    def test_unknown_item_is_a_usage_error(self, tmp_path: Path):
        de_path = _cli_pair(tmp_path / "topic_010_intro")
        code, out = _run("task", str(de_path), "--item", "edit-nope", "--json")
        assert code == 2, out
        assert "no report item" in out.lower()

    def test_directory_is_rejected(self, tmp_path: Path):
        _cli_pair(tmp_path / "topic_010_intro")
        code, out = _run("task", str(tmp_path), "--json")
        assert code == 2, out
        assert "single deck pair" in out.lower()


class TestColdConsistencyCli:
    """Issue #438 over the verb CLI: a clean committed id-less deck is a no-op report
    (not the false `needs_agent` it used to raise), and a genuinely-new cold pair frames
    a mint task with no embedded key."""

    def test_committed_idless_deck_reports_clean_not_needs_agent(self, tmp_path: Path):
        # The dogfooding bug: a committed, in-sync, fully id-less deck used to report a
        # cold-pair refusal/mint on every run because it shares no slide_id. Byte-stable
        # vs HEAD it is consistent now — `report` is clean (exit 0), not `needs_agent`.
        de_path, _en = _commit_pair(
            tmp_path,
            _slide_idless("de", "# ## Einleitung") + _slide_idless("de", "# ## Variablen"),
            _slide_idless("en", "# ## Introduction") + _slide_idless("en", "# ## Variables"),
        )
        code, out = _run("report", str(de_path), "--json")
        assert code == 0, out
        payload = json.loads(out)
        report = payload.get("report", payload)
        assert report["baseline_source"] == "git-head"
        assert report["is_clean"] is True
        assert report["needs_agent"] is False
        assert report["needs_model"] is False

    def test_new_idless_pair_frames_a_mint_task_without_a_key(self, tmp_path: Path, monkeypatch):
        # Issue #438 (D1): the read surface no longer gates cold-pair candidacy on a key.
        # A genuinely-new (uncommitted) id-less pair frames a mint task with no key. Force
        # the key check to report FALSE (PR #442 review — `delenv` alone was inert): if a
        # regression re-added `provider_available=has_openrouter_api_key()` to `task`, the
        # mint task would vanish here and the test would fail. As shipped, `task` never
        # consults it, so the mint is framed regardless — the agent is the verifier.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(
            "clm.infrastructure.llm.openrouter_client.has_openrouter_api_key",
            lambda *a, **k: False,
        )
        de_path, _en = _pair(
            tmp_path,
            _slide_idless("de", "# ## Einleitung") + _slide_idless("de", "# ## Variablen"),
            _slide_idless("en", "# ## Introduction") + _slide_idless("en", "# ## Variables"),
        )
        code, out = _run("task", str(de_path), "--json")
        assert code == 0, out
        tasks = json.loads(out)["tasks"]
        mint = next(t for t in tasks if t["kind"] == "mint")
        assert mint["validator"] == "correspondence"

    def test_bare_deck_routes_to_read_only_report(self, tmp_path: Path):
        # PR #442 review (M1): the headline breaking change — a bare `clm slides sync DECK`
        # (no verb) is an alias for `report` and must NEVER write. Pin the default-verb
        # routing AND the read-only guarantee on a deck that genuinely has work pending
        # (a DE-only edit): a regression deleting `_DefaultVerbGroup.parse_args` would make
        # Click error "No such command '<deck>.de.py'" and fail here.
        de_path, en_path = _commit_pair(
            tmp_path,
            _slide("de", "a", "# ## A") + _slide("de", "b", "# ## B"),
            _slide("en", "a", "# ## A") + _slide("en", "b", "# ## B"),
        )
        de_path.write_text(
            _slide("de", "a", "# ## A EDIT") + _slide("de", "b", "# ## B"), encoding="utf-8"
        )
        en_before = en_path.read_text(encoding="utf-8")
        code, out = _run(str(de_path), "--json")  # bare deck, no verb → report
        assert code == 1, out  # work pending (the edit) → report exit 1, not a crash
        report = json.loads(out)["report"]
        assert report["needs_model"] is True  # the de->en edit is surfaced, not applied
        assert en_path.read_text(encoding="utf-8") == en_before  # read-only: EN untouched
