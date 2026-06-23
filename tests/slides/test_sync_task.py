"""``clm slides sync task`` — framed, model-free reconciliation tasks (epic #440).

``task`` emits everything a model needs to reconcile one tier-2/3 item (the system
prompt, the ready-to-send prompt, the inputs, the answer schema, and the validator
``accept`` will run) **without the engine calling a model**. These tests cover the
three framed kinds — ``edit`` (the sync judge), ``add`` (the translator), and
``realign`` (the alignment recoverer) — plus the stable item-id contract both ``task``
and ``accept`` address items by, and the CLI surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.sync_prompts import SYNC_SYSTEM_PROMPT
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_plan import Proposal, SyncPlan, build_sync_plan, watermark_rows
from clm.slides.sync_recover import RECOVERY_SYSTEM_PROMPT
from clm.slides.sync_report import ReconciliationItem, ReconciliationReport, _assign_item_ids
from clm.slides.sync_task import (
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


def _code_idd_neutral(sid: str, body: str) -> str:
    return f'# %% tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_localized(lang: str, sid: str, body: str) -> str:
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_shared(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck_x.de.py"
    en_path = tmp_path / "deck_x.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


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
        from clm.slides.sync_report import build_report

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

    def test_cold_start_mint_points_at_autopilot(self, tmp_path: Path):
        de, en = _slide("de", "s1", "# ## eins"), _slide("en", "s1", "# ## one")
        de_path, en_path = _pair(tmp_path, de, en)
        plan = _real_plan(
            de_path,
            en_path,
            Proposal(kind="mint", role="slide", direction=None, slide_id="s1"),
        )
        with pytest.raises(TaskUnavailable, match="autopilot|assign-ids"):
            build_task(plan, "mint-s1")


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
