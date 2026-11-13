"""The shadow of the prototype sync core on real decks (``scripts/sync_shadow.py``).

Step 4 of ``docs/claude/sync-structural-core-investigation.md`` (§9) runs the
prototype core beside the engine on real course history. These tests pin the
adapter (a real deck half as the prototype's cells, an in-sync pair as its
recorded sequence) and one replay through both runs, so the script that
produced the doc's numbers keeps working.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shadow = _load("sync_shadow")

TITLE = {"de": "Einführung", "en": "Introduction"}
NEXT = {"de": "Weiter", "en": "Next"}


def deck(lang: str, *code: str, tags: str = '["slide"]') -> str:
    cells = [
        f'# %% [markdown] lang="{lang}" tags={tags} slide_id="intro"\n# # {TITLE[lang]}\n',
        *(f"# %%\n{line}\n" for line in code),
        f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="next"\n# # {NEXT[lang]}\n',
    ]
    return "\n".join(cells)


def test_an_in_sync_pair_is_one_recorded_sequence() -> None:
    parts = shadow.base_members(deck("de", "x = 1", "y = 2"), deck("en", "x = 1", "y = 2"))
    assert not isinstance(parts, str), parts
    assert parts["companion"] == []
    base = parts["deck"]
    assert [(m.sid, m.kind, m.localized) for m in base] == [
        ("intro", "markdown", True),
        (None, "code", False),
        (None, "code", False),
        ("next", "markdown", True),
    ]
    assert base[0].tags == frozenset({"slide"})
    # the content leaves out the id and the tags, which the core carries apart
    assert "slide_id" not in (base[0].de or "") and "tags=" not in (base[0].de or "")


def test_a_pair_whose_shared_cells_differ_is_not_a_base() -> None:
    # the parser already observes it; base_members keeps its own check behind that
    for de, en in ((("x = 1",), ("x = 2",)), (("x = 1", "y = 2"), ("y = 2", "x = 1"))):
        reason = shadow.base_members(deck("de", *de), deck("en", *en))
        assert reason in ("observation:shared_divergence", "shared_differs"), reason


def _case(leader: str, b: tuple[str, str], n: tuple[str, str]) -> object:
    return shadow.Case(
        "slides/t/slides_t", "py", "b" * 40, "n" * 40, leader, (*b, None, None), (*n, None, None)
    )


def test_a_one_sided_edit_replays_to_the_next_state_in_both_runs() -> None:
    b = (deck("de", "x = 1", "y = 2"), deck("en", "x = 1", "y = 2"))
    n = (deck("de", "y = 2", "z = 3", "x = 1"), deck("en", "y = 2", "z = 3", "x = 1"))
    case = _case("de", b, n)
    model = shadow.run_model(case)
    assert (model.status, model.verdict) == ("clean", "match"), model
    engine = shadow.run_engine(case)
    assert engine.status in ("clean", "asks", "stalls"), engine
    if engine.status == "clean":
        assert engine.verdict == "match", engine


def test_a_tag_change_on_one_half_is_carried_to_the_other() -> None:
    b = (deck("de", "x = 1"), deck("en", "x = 1"))
    n = (deck("de", "x = 1", tags='["subslide"]'), deck("en", "x = 1", tags='["subslide"]'))
    model = shadow.run_model(_case("en", b, n))
    assert (model.status, model.verdict) == ("clean", "match"), model


def test_the_verdict_catches_a_result_that_differs_from_the_next_state() -> None:
    b = (deck("de", "x = 1"), deck("en", "x = 1"))
    n = (deck("de", "x = 2"), deck("en", "x = 3"))  # the follower's code is not derivable
    model = shadow.run_model(_case("de", b, n))
    assert (model.status, model.verdict) == ("clean", "differs"), model


def companion(lang: str, *narration: str) -> str:
    return "\n".join(
        f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-{i}" for_slide="intro"\n'
        f"# {text}\n"
        for i, text in enumerate(narration)
    )


def test_a_companion_is_its_own_part() -> None:
    parts = shadow.base_members(
        deck("de", "x = 1"),
        deck("en", "x = 1"),
        "#",
        companion("de", "Eins"),
        companion("en", "One"),
    )
    assert not isinstance(parts, str), parts
    assert [m.sid for m in parts["companion"]] == ["vo-0"]
    assert [m.sid for m in parts["deck"]] == ["intro", None, "next"]


def test_narration_added_on_one_half_is_a_translation_in_both_runs() -> None:
    b = (deck("de", "x = 1"), deck("en", "x = 1"), companion("de", "Eins"), companion("en", "One"))
    n = (
        deck("de", "x = 1"),
        deck("en", "x = 1"),
        companion("de", "Eins", "Zwei"),
        companion("en", "One", "Two"),
    )
    case = shadow.Case("slides/t/slides_t", "py", "b" * 40, "n" * 40, "de", b, n)
    model = shadow.run_model(case)
    assert (model.status, model.verdict, model.answered) == (
        "clean",
        "match",
        {"translate_new": 1},
    ), model
    engine = shadow.run_engine(case)
    assert (engine.status, engine.verdict) == ("clean", "match"), engine


def test_narration_moved_into_the_companion_keeps_the_other_halfs_text() -> None:
    """A voiceover cell one half moves from the deck into its companion is one
    cell that changed part, not a removal and a new cell: the other half's
    narration moves with it and nothing is translated again."""
    vo = {"de": "Sprechertext", "en": "Narration"}

    def inline(lang: str) -> str:
        cell = f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-x"\n# {vo[lang]}\n'
        return deck(lang, "x = 1").replace("# %%\nx = 1\n", f"# %%\nx = 1\n\n{cell}")

    def moved(lang: str) -> str:
        cell = (
            f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="vo-x" for_slide="intro"\n'
            f"# {vo[lang]}\n"
        )
        return cell

    b = (inline("de"), inline("en"), None, None)
    n = (deck("de", "x = 1"), deck("en", "x = 1"), moved("de"), moved("en"))
    assert not isinstance(shadow.base_members(*b[:2]), str)
    case = shadow.Case("slides/t/slides_t", "py", "b" * 40, "n" * 40, "de", b, n)
    model = shadow.run_model(case)
    assert (model.status, model.verdict, model.answered) == ("clean", "match", {}), model
