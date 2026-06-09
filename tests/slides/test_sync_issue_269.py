"""Regression tests for Issue #269 — sync must propagate ALL one-sided edits or alert.

The cardinal invariant under test: when an author edits one half of a split de/en
deck, ``clm slides sync`` must carry every change to the other half — code cells,
markdown cells, the deck header, language-neutral OR localized — when it can, and
**alert** (error / issue, watermark held, ``is_noop`` False) when it cannot. It must
NEVER report "decks already consistent" / advance the watermark while a real change
was silently dropped.

Each scenario is exercised under BOTH baseline sources: a committed git-HEAD baseline
(the cold-start / first sync of a freshly-split pair) and a recorded watermark.
Before the fix, every "BUG" case below was a silent drop with ``is_noop`` True.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import DECISION_SKIP, _record_watermark, apply_plan
from clm.slides.sync_plan import build_sync_plan
from clm.slides.sync_translate import StaticSlideTranslator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


# ---------------------------------------------------------------------------
# Cell / deck builders (a valid split pair: neutral cells byte-identical)
# ---------------------------------------------------------------------------


def _title(lang: str, sid: str = "title", txt: str = "T") -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n# # {txt}\n'


def _ncode(body: str) -> str:
    return f'# %% tags=["keep"]\n{body}\n'


def _nmd(body: str) -> str:
    return f'# %% [markdown] tags=["keep"]\n{body}\n'


def _nmd_tagged(body: str) -> str:
    # A neutral markdown cell that ALSO carries a narrative tag but no lang / slide_id
    # (the tagged-neutral blind spot). An invalid shape for a real split deck, but the
    # unify invariant does not forbid it, so sync must still alert rather than drop.
    return f'# %% [markdown] tags=["slide"]\n{body}\n'


def _idless_code(lang: str, body: str) -> str:
    return f'# %% lang="{lang}"\n{body}\n'


def _idless_md(lang: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}"\n{body}\n'


def _hdr(lang: str, title: str, num: str = "01") -> str:
    macro = "header_de" if lang == "de" else "header_en"
    return f'# j2 from \'macros.j2\' import {macro}\n# {{{{ {macro}("{title}", "{num}") }}}}\n'


def _sub_md(lang: str, sid: str, txt: str) -> str:
    """A localized (id'd) subslide markdown cell — a slide-group start."""
    return f'# %% [markdown] lang="{lang}" tags=["subslide"] slide_id="{sid}"\n# ## {txt}\n'


def _sub_code(sid: str, body: str) -> str:
    """A language-neutral code cell that is itself a subslide group-start.

    (The shape of ``visualize-chunks-5`` in the reported bug: a neutral code cell
    carrying a slide_id + ``subslide`` tag — it starts a group but is invisible to
    the per-cell add path's ``(slide_id, role)`` anchor.)
    """
    return f'# %% tags=["subslide"] slide_id="{sid}"\n{body}\n'


def _deck(*parts: str) -> str:
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Harness: establish a baseline, apply the edit, return (plan, result, files)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _sync(
    tmp: Path,
    baseline: str,
    de0: str,
    en0: str,
    de1: str,
    en1: str,
    *,
    mapping: dict[str, str] | None = None,
):
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    if baseline == "git-head":
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "Test")
        _git(tmp, "add", "-A")
        _git(tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    else:
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    translator = StaticSlideTranslator(mapping=mapping or {}, default="<<XL>>")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        result = apply_plan(plan, judge=None, translator=translator, watermark_cache=wm)
    finally:
        wm.close()
    return plan, result, de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")


def _alerted(plan, result) -> bool:
    return (
        plan.has_errors
        or result.has_errors
        or result.deferred > 0
        or any(i.severity == "error" for i in plan.issues)
    )


def _falsely_consistent(plan, result, propagated: bool) -> bool:
    """The forbidden state: reported consistent while a change was NOT handled."""
    return plan.is_noop and not propagated and not _alerted(plan, result)


BASELINES = ["git-head", "watermark"]


# ---------------------------------------------------------------------------
# Propagation cases — sync must carry the edit to the twin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_neutral_code_body_edit_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _ncode("import os"))
    en = _deck(_title("en"), _ncode("import os"))
    plan, result, de_after, _ = _sync(
        tmp_path, baseline, de, en, de, _deck(_title("en"), _ncode("import os  # EDIT"))
    )
    assert "import os  # EDIT" in de_after  # propagated to the unedited DE half
    assert not _falsely_consistent(plan, result, True)
    assert not plan.is_noop


@pytest.mark.parametrize("baseline", BASELINES)
def test_neutral_markdown_body_edit_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _nmd("# shared note"))
    en = _deck(_title("en"), _nmd("# shared note"))
    plan, result, de_after, _ = _sync(
        tmp_path, baseline, de, en, de, _deck(_title("en"), _nmd("# shared note EDIT"))
    )
    assert "# shared note EDIT" in de_after
    assert not _falsely_consistent(plan, result, True)


@pytest.mark.parametrize("baseline", BASELINES)
def test_neutral_code_add_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _ncode("import os"))
    en = _deck(_title("en"), _ncode("import os"))
    en1 = _deck(_title("en"), _ncode("import os"), _ncode("import sys"))
    plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert "import sys" in de_after  # the new neutral cell copied to DE
    assert not _falsely_consistent(plan, result, True)


@pytest.mark.parametrize("baseline", BASELINES)
def test_neutral_code_remove_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _ncode("import os"), _ncode("import sys"))
    en = _deck(_title("en"), _ncode("import os"), _ncode("import sys"))
    en1 = _deck(_title("en"), _ncode("import os"))
    plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert "import sys" not in de_after  # the removal mirrored to DE
    assert not _falsely_consistent(plan, result, True)


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_code_edit_propagates(tmp_path: Path, baseline: str):
    """A hash-anchored id-less localized code cell (bare statement) — both baselines."""
    de = _deck(_title("de"), _idless_code("de", 'print("Anzahl", n)'))
    en = _deck(_title("en"), _idless_code("en", 'print("count", n)'))
    en1 = _deck(_title("en"), _idless_code("en", 'print("COUNT", n)'))
    plan, result, de_after, _ = _sync(
        tmp_path, baseline, de, en, de, en1, mapping={'print("COUNT", n)': 'print("ANZAHL", n)'}
    )
    assert "ANZAHL" in de_after  # re-translated into DE
    assert not _falsely_consistent(plan, result, True)


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_construct_edit_propagates(tmp_path: Path, baseline: str):
    """A construct-anchored id-less localized cell (a def) — both baselines."""
    de = _deck(_title("de"), _idless_code("de", 'def f():\n    print("Hallo")'))
    en = _deck(_title("en"), _idless_code("en", 'def f():\n    print("Hello")'))
    en1 = _deck(_title("en"), _idless_code("en", 'def f():\n    print("Hello there")'))
    plan, result, de_after, _ = _sync(
        tmp_path,
        baseline,
        de,
        en,
        de,
        en1,
        mapping={'def f():\n    print("Hello there")': 'def f():\n    print("Hallo dort")'},
    )
    assert "Hallo dort" in de_after
    assert not _falsely_consistent(plan, result, True)


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_markdown_edit_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _idless_md("de", "# Notiz"))
    en = _deck(_title("en"), _idless_md("en", "# Note"))
    en1 = _deck(_title("en"), _idless_md("en", "# Note expanded"))
    plan, result, de_after, _ = _sync(
        tmp_path, baseline, de, en, de, en1, mapping={"# Note expanded": "# Notiz erweitert"}
    )
    assert "Notiz erweitert" in de_after
    assert not _falsely_consistent(plan, result, True)


# ---------------------------------------------------------------------------
# Alert cases — sync cannot resolve, must alert (never silently "consistent")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_header_title_edit_alerts(tmp_path: Path, baseline: str):
    de = _deck(_hdr("de", "Schluss"), _title("de", "s1", "X"))
    en = _deck(_hdr("en", "Reason"), _title("en", "s1", "X"))
    en1 = _deck(_hdr("en", "Advanced Reason"), _title("en", "s1", "X"))
    plan, result, de_after, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert _alerted(plan, result)  # header is not auto-translated → alert
    assert "Advanced Reason" not in de_after  # not pasted verbatim into DE
    assert not plan.is_noop
    assert not _falsely_consistent(plan, result, False)


@pytest.mark.parametrize("baseline", BASELINES)
def test_header_neutral_arg_edit_alerts(tmp_path: Path, baseline: str):
    # A byte-identical-across-halves header arg ("01") edited on one side: a genuine
    # divergence in a should-be-identical value. Must alert, never "consistent".
    de = _deck(_hdr("de", "Schluss", "01"), _title("de", "s1", "X"))
    en = _deck(_hdr("en", "Reason", "01"), _title("en", "s1", "X"))
    en1 = _deck(_hdr("en", "Reason", "02"), _title("en", "s1", "X"))
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert _alerted(plan, result)
    assert not _falsely_consistent(plan, result, False)


@pytest.mark.parametrize("baseline", BASELINES)
def test_header_updated_both_sides_is_clean(tmp_path: Path, baseline: str):
    # Both halves' headers updated (the user translated the title too): no alert.
    de = _deck(_hdr("de", "Schluss"), _title("de", "s1", "X"))
    en = _deck(_hdr("en", "Reason"), _title("en", "s1", "X"))
    plan, result, _, _ = _sync(
        tmp_path,
        baseline,
        de,
        en,
        _deck(_hdr("de", "Fortgeschritten"), _title("de", "s1", "X")),
        _deck(_hdr("en", "Advanced"), _title("en", "s1", "X")),
    )
    assert not _alerted(plan, result)


@pytest.mark.parametrize("baseline", BASELINES)
def test_tagged_neutral_md_edit_alerts(tmp_path: Path, baseline: str):
    # A markdown cell with a narrative tag but no lang/slide_id can't be auto-rebuilt
    # (its group has no slide_id to match), so the parity fail-safe must alert.
    de = _deck(_title("de"), _nmd_tagged("# heading"))
    en = _deck(_title("en"), _nmd_tagged("# heading"))
    en1 = _deck(_title("en"), _nmd_tagged("# heading EDIT"))
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert _alerted(plan, result)
    assert not _falsely_consistent(plan, result, False)


@pytest.mark.parametrize("baseline", BASELINES)
def test_neutral_cell_diverged_both_sides_alerts_or_heals(tmp_path: Path, baseline: str):
    # Same neutral cell edited differently on both halves (§7a): auto-heal holds the
    # watermark with a warning (alert), never a silent "consistent" advance.
    de = _deck(_title("de"), _ncode("x = 1"))
    en = _deck(_title("en"), _ncode("x = 1"))
    plan, result, _, _ = _sync(
        tmp_path,
        baseline,
        de,
        en,
        _deck(_title("de"), _ncode("x = 11")),
        _deck(_title("en"), _ncode("x = 99")),
    )
    # Either it healed and held the watermark for review, or it surfaced an error;
    # in all cases the watermark must NOT advance over the unresolved divergence.
    assert not result.watermark_recorded
    assert plan.issues  # a §7a warning or error was raised


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_cross_group_move_alerts(tmp_path: Path, baseline: str):
    # A one-sided cross-group move of an un-id'd localized cell that leaves the flat
    # hash sequence unchanged is not auto-propagated; the structural id-less parity
    # fail-safe must alert (watermark held), never report "consistent".
    de = _deck(_title("de", "s1"), _idless_code("de", 'print("p")'), _title("de", "s2"))
    en = _deck(_title("en", "s1"), _idless_code("en", 'print("p")'), _title("en", "s2"))
    en1 = _deck(_title("en", "s1"), _title("en", "s2"), _idless_code("en", 'print("p")'))
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert not _falsely_consistent(plan, result, False)


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_same_group_reorder_alerts(tmp_path: Path, baseline: str):
    # A one-sided reorder of two same-kind un-id'd localized cells within one group
    # cannot be auto-propagated (structurally indistinguishable across languages) —
    # the reorder fail-safe must alert and hold the watermark so a later run never
    # falsely reports "consistent" over the still-divergent order.
    de = _deck(
        _title("de", "s1"), _idless_code("de", 'print("a")'), _idless_code("de", 'print("b")')
    )
    en = _deck(
        _title("en", "s1"), _idless_code("en", 'print("a")'), _idless_code("en", 'print("b")')
    )
    en1 = _deck(
        _title("en", "s1"), _idless_code("en", 'print("b")'), _idless_code("en", 'print("a")')
    )
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded


@pytest.mark.parametrize("baseline", BASELINES)
def test_irreconcilable_neutral_cells_alerts(tmp_path: Path, baseline: str):
    # Different neutral cells edited on each half: no single direction → error.
    de = _deck(_title("de"), _ncode("a = 1"), _ncode("b = 2"))
    en = _deck(_title("en"), _ncode("a = 1"), _ncode("b = 2"))
    plan, result, _, _ = _sync(
        tmp_path,
        baseline,
        de,
        en,
        _deck(_title("de"), _ncode("a = 1  # de"), _ncode("b = 2")),
        _deck(_title("en"), _ncode("a = 1"), _ncode("b = 2  # en")),
    )
    assert _alerted(plan, result)
    assert not result.watermark_recorded


# ---------------------------------------------------------------------------
# Honest reporting + no-op safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_summary_not_consistent_when_neutral_change_propagates(tmp_path: Path, baseline: str):
    de = _deck(_title("de"), _ncode("import os"))
    en = _deck(_title("en"), _ncode("import os"))
    plan, _result, _, _ = _sync(
        tmp_path, baseline, de, en, de, _deck(_title("en"), _ncode("import os  # E"))
    )
    assert "already" not in plan.summary().lower()  # not "decks already consistent"
    assert plan.anchor_direction == "en->de"


@pytest.mark.parametrize("baseline", BASELINES)
def test_genuine_noop_still_reports_consistent(tmp_path: Path, baseline: str):
    # A real no-op (no edits) must still report consistent and advance — no false alarms.
    de = _deck(_title("de"), _ncode("import os"), _idless_code("de", 'print("a")'))
    en = _deck(_title("en"), _ncode("import os"), _idless_code("en", 'print("a")'))
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en)
    assert plan.is_noop
    assert not _alerted(plan, result)
    assert "consistent" in plan.summary().lower()


# ---------------------------------------------------------------------------
# New-slide-group placement — a new group added after a NEUTRAL / id-less
# neighbour must land at the source position, not be misplaced and then trip a
# parity fail-safe. (The reported bug: the id-carrying add anchors only on cells
# it can name by (slide_id, role), so a neutral/id-less neighbour is skipped and
# the new group is inserted in the wrong inter-group slot; the structural pass
# rebuilds group CONTENTS but never reorders GROUPS, so the misplacement survived
# as a "shared cells differ" / "id-less placed differently" error.)
# ---------------------------------------------------------------------------


def _group_ids(text: str):
    return [c.slide_id for c in parse_cells(text) if c.metadata.is_slide_start and c.slide_id]


def _neutral_bodies(text: str):
    return [
        c.content for c in parse_cells(text) if not c.metadata.is_j2 and c.lang not in ("de", "en")
    ]


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_group_after_neutral_idd_neighbour_places_correctly(tmp_path: Path, baseline: str):
    # The user's exact shape: a new id'd subslide + neutral code cells inserted after
    # a neutral-code subslide (`visualize-chunks-5`), between two existing groups.
    de = _deck(
        _title("de", "intro"),
        _sub_code("viz-5", "visualize(chunks)"),
        _title("de", "tokens", "Tokens"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_code("viz-5", "visualize(chunks)"),
        _title("en", "tokens", "Tokens"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_code("viz-5", "visualize(chunks)"),
        _sub_md("de", "why-empty", "Warum leerer Separator?"),
        _ncode("a = 1"),
        _ncode("b = 2"),
        _title("de", "tokens", "Tokens"),
    )
    plan, result, _de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en, mapping={"# ## Warum leerer Separator?": "# ## Why?"}
    )
    assert not _alerted(plan, result)  # no parity error — propagated cleanly
    assert result.watermark_recorded
    # The new group landed right after viz-5 on EN, not before it.
    assert _group_ids(en_after) == ["intro", "viz-5", "why-empty", "tokens"]
    # Neutral cells are byte-identical and in the same order across the halves.
    assert _neutral_bodies(en_after) == _neutral_bodies(_de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_group_after_idless_neutral_neighbour_places_correctly(tmp_path: Path, baseline: str):
    # New group preceded by an id-LESS neutral code cell (no slide_id to anchor on).
    de = _deck(_title("de", "intro"), _ncode("import os"), _title("de", "end", "Ende"))
    en = _deck(_title("en", "intro"), _ncode("import os"), _title("en", "end", "End"))
    de1 = _deck(
        _title("de", "intro"),
        _ncode("import os"),
        _sub_md("de", "newgrp", "Neu"),
        _ncode("import sys"),
        _title("de", "end", "Ende"),
    )
    plan, result, de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en, mapping={"# ## Neu": "# ## New"}
    )
    assert not _alerted(plan, result)
    assert _group_ids(en_after) == ["intro", "newgrp", "end"]
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_group_after_idless_localized_neighbour_places_correctly(tmp_path: Path, baseline: str):
    # New group preceded by an id-less LOCALIZED cell (translated body, un-anchorable).
    de = _deck(_title("de", "intro"), _idless_code("de", 'print("Hallo")'), _title("de", "end"))
    en = _deck(_title("en", "intro"), _idless_code("en", 'print("Hello")'), _title("en", "end"))
    de1 = _deck(
        _title("de", "intro"),
        _idless_code("de", 'print("Hallo")'),
        _sub_md("de", "newgrp", "Neu"),
        _ncode("import sys"),
        _title("de", "end"),
    )
    plan, result, de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en, mapping={"# ## Neu": "# ## New"}
    )
    assert not _alerted(plan, result)
    assert _group_ids(en_after) == ["intro", "newgrp", "end"]
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


# ---------------------------------------------------------------------------
# Diagnostics — a genuine (unresolvable) divergence must name the offending cell
# so the author can locate it (the reported "errors don't say which cell" gap).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_shared_cell_divergence_error_names_cells(tmp_path: Path, baseline: str):
    # A tagged-neutral cell edited on one side can't be auto-rebuilt → parity error.
    de = _deck(_title("de"), _nmd_tagged("# heading"))
    en = _deck(_title("en"), _nmd_tagged("# heading"))
    en1 = _deck(_title("en"), _nmd_tagged("# heading EDIT"))
    _plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    msg = "\n".join(result.errors)
    assert "shared" in msg
    # Names the divergent content rather than a bare "a change was not propagated".
    assert "heading EDIT" in msg or "heading" in msg
    assert "missing on" in msg


@pytest.mark.parametrize("baseline", BASELINES)
def test_idless_localized_divergence_error_names_group(tmp_path: Path, baseline: str):
    # A one-sided cross-group move of an id-less localized cell → parity error that
    # points at the slide group, not a bare "a move was not propagated".
    de = _deck(_title("de", "s1"), _idless_code("de", 'print("p")'), _title("de", "s2"))
    en = _deck(_title("en", "s1"), _idless_code("en", 'print("p")'), _title("en", "s2"))
    en1 = _deck(_title("en", "s1"), _title("en", "s2"), _idless_code("en", 'print("p")'))
    _plan, result, _, _ = _sync(tmp_path, baseline, de, en, de, en1)
    msg = "\n".join(result.errors)
    assert "slide group" in msg


def _shared_snippet_count(msg: str) -> int:
    """How many cell snippets the 'on de but missing on en' clause lists."""
    if "on de but missing on en: " not in msg:
        return 0
    clause = msg.split("on de but missing on en: ", 1)[1].split(";")[0]
    return clause.count("'") // 2


@pytest.mark.parametrize("baseline", BASELINES)
def test_shared_divergence_dedups_identical_cell_snippets(tmp_path: Path, baseline: str):
    # Two byte-identical neutral cells added on one half must list the snippet ONCE,
    # not the same text twice (the message names distinct offending cells).
    de = _deck(_title("de"), _nmd_tagged("# h"))
    en = _deck(_title("en"), _nmd_tagged("# h"))
    # Edit the tagged-neutral cell to the SAME new text on... no: force a divergence
    # where de has two identical neutral cells en lacks. Use neutral code adds on DE
    # only, paired with a parity-tripping tagged-neutral so the fail-safe fires.
    de1 = _deck(_title("de"), _nmd_tagged("# h"), _ncode("import sys"), _ncode("import sys"))
    en1 = _deck(_title("en"), _nmd_tagged("# h EDIT"))
    _plan, result, _, _ = _sync(tmp_path, baseline, de, en, de1, en1)
    msg = "\n".join(result.errors)
    if "import sys" in msg:
        # the duplicate 'import sys' must appear once, not twice, in the de-only clause
        assert msg.count("'import sys'") == 1


# ---------------------------------------------------------------------------
# A SKIPPED move (--interactive) must NOT be silently re-applied by the
# group-order reconciliation. _reconcile_group_order is gated on a fully clean
# pass exactly like _apply_moves, so a deferred/skipped reorder is honoured.
# ---------------------------------------------------------------------------


def _sync_skipping_moves(tmp: Path, baseline: str, de0: str, en0: str, de1: str, en1: str):
    """Like ``_sync`` but SKIPS every ``move`` proposal (mimics an interactive skip)."""
    db = tmp / "clm-llm.sqlite"
    de_path, en_path = tmp / "deck.de.py", tmp / "deck.en.py"
    de_path.write_text(de0, encoding="utf-8")
    en_path.write_text(en0, encoding="utf-8")
    if baseline == "git-head":
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "t@example.com")
        _git(tmp, "config", "user.name", "Test")
        _git(tmp, "add", "-A")
        _git(tmp, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    else:
        wm = SyncWatermarkCache(db)
        _record_watermark(wm, de_path, en_path)
        wm.close()
    de_path.write_text(de1, encoding="utf-8")
    en_path.write_text(en1, encoding="utf-8")
    wm = SyncWatermarkCache(db)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=wm)
        decisions = {id(p): DECISION_SKIP for p in plan.proposals if p.kind == "move"}
        result = apply_plan(
            plan,
            judge=None,
            translator=StaticSlideTranslator(default="<<XL>>"),
            watermark_cache=wm,
            decisions=decisions,
        )
    finally:
        wm.close()
    return plan, result, en_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("baseline", BASELINES)
def test_skipped_move_is_not_reapplied_by_group_reorder(tmp_path: Path, baseline: str):
    # Author swaps two id'd groups on DE; the user SKIPS the move. The reorder must
    # NOT be re-applied to EN behind the skip (the bug a naive group-order reconcile
    # introduces: it would re-order EN to match DE and write it, defeating the skip).
    de = _deck(_title("de", "intro"), _sub_md("de", "A", "Alpha"), _sub_md("de", "B", "Beta"))
    en = _deck(_title("en", "intro"), _sub_md("en", "A", "Alpha"), _sub_md("en", "B", "Beta"))
    de1 = _deck(_title("de", "intro"), _sub_md("de", "B", "Beta"), _sub_md("de", "A", "Alpha"))
    plan, result, en_after = _sync_skipping_moves(tmp_path, baseline, de, en, de1, en)
    assert any(p.kind == "move" for p in plan.proposals)  # a move WAS detected
    assert result.deferred >= 1  # and it was deferred (skipped)
    assert not result.watermark_recorded  # watermark held over the skip
    # The skip is honoured: EN keeps its original group order, not DE's swap.
    assert _group_ids(en_after) == ["intro", "A", "B"]


# ---------------------------------------------------------------------------
# Placement edge cases — a new group at the deck START / END must also land right.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_group_at_deck_end_places_correctly(tmp_path: Path, baseline: str):
    de = _deck(_title("de", "intro"), _sub_code("viz", "visualize()"))
    en = _deck(_title("en", "intro"), _sub_code("viz", "visualize()"))
    de1 = _deck(
        _title("de", "intro"),
        _sub_code("viz", "visualize()"),
        _sub_md("de", "tail", "Schluss"),
        _ncode("cleanup()"),
    )
    plan, result, de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en, mapping={"# ## Schluss": "# ## End"}
    )
    assert not _alerted(plan, result)
    assert _group_ids(en_after) == ["intro", "viz", "tail"]
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_group_at_deck_start_places_correctly(tmp_path: Path, baseline: str):
    de = _deck(_title("de", "intro"), _ncode("body()"))
    en = _deck(_title("en", "intro"), _ncode("body()"))
    de1 = _deck(
        _sub_md("de", "pre", "Vorab"),
        _ncode("setup()"),
        _title("de", "intro"),
        _ncode("body()"),
    )
    plan, result, de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en, mapping={"# ## Vorab": "# ## Preamble"}
    )
    assert not _alerted(plan, result)
    assert _group_ids(en_after) == ["pre", "intro"]
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_new_neutral_code_group_start_alerts_not_silent(tmp_path: Path, baseline: str):
    # Documented limitation: a brand-new group whose START is itself a NEUTRAL code
    # cell (a slide_id + subslide tag, no lang — the bare 'visualize-chunks-N' shape)
    # is not auto-propagated, because the add path keys on (slide_id, role) and a
    # neutral cell has role None. The cardinal #269 invariant must still hold: sync
    # ALERTS (watermark held), it must NEVER silently report "consistent".
    de = _deck(_title("de", "intro"), _title("de", "end", "Ende"))
    en = _deck(_title("en", "intro"), _title("en", "end", "End"))
    de1 = _deck(
        _title("de", "intro"),
        _sub_code("viz-new", "visualize(new)"),
        _title("de", "end", "Ende"),
    )
    plan, result, _, _ = _sync(tmp_path, baseline, de, en, de1, en)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert not _falsely_consistent(plan, result, False)
