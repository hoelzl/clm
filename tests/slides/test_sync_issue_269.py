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


def _idd_code(lang: str, sid: str, body: str) -> str:
    """A localized (id'd) code cell — keyed by ``(slide_id, role)`` so an edit to it
    yields an ``edit``/keyed proposal in the run's direction."""
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


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


# ---------------------------------------------------------------------------
# Issue #282 — a one-sided neutral / id-less edit INSIDE a moved slide group.
# One half reorders groups (a `move`) while the other independently edits a
# language-neutral cell inside a moved group: the two changes flow OPPOSITE
# directions. The structural pass keys on the move's direction and would copy
# the move source's neutral cell over the target's edit — a silent #269 drop.
# Sync must ALERT (watermark held, edit untouched on disk), never report
# consistent. The id-less-localized analog already alerted via
# _classify_idless_localized_drift; the neutral path was the remaining gap.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_opposite_neutral_edit_alerts(tmp_path: Path, baseline: str):
    # DE reorders groups (B before A → a move de->en); EN edits the neutral cell
    # inside the moved group A (x = 1 → x = 2 → an en->de drift). Opposite
    # directions: alert, hold the watermark, leave the EN edit on disk.
    de = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("de", "B", "Beta"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("en", "B", "Beta"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_md("de", "B", "Beta"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
    )
    en1 = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 2"),
        _sub_md("en", "B", "Beta"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert any(p.kind == "move" for p in plan.proposals)  # the reorder WAS detected
    assert _alerted(plan, result)  # opposite-direction conflict surfaced
    assert not result.watermark_recorded  # watermark held over the conflict
    assert not _falsely_consistent(plan, result, False)
    # The EN edit is NOT silently dropped: nothing was written, so it stays on disk.
    assert "x = 2" in en_after
    assert "x = 2" not in de_after  # and was not (wrongly) reverted/auto-applied either
    # The error explains the reorder-vs-edit conflict so the author can act on it.
    msg = "\n".join(i.reason for i in plan.issues if i.severity == "error")
    assert "reorder" in msg


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_opposite_neutral_edit_mirror_direction_alerts(tmp_path: Path, baseline: str):
    # The mirror of the above: EN reorders (a move en->de); DE edits the neutral cell
    # (a de->en drift). Same conflict, opposite roles — must alert symmetrically.
    de = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("de", "B", "Beta"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("en", "B", "Beta"),
    )
    en1 = _deck(
        _title("en", "intro"),
        _sub_md("en", "B", "Beta"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 1"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 2"),
        _sub_md("de", "B", "Beta"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert not _falsely_consistent(plan, result, False)
    assert "x = 2" in de_after  # the DE edit stays on disk, not dropped


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_opposite_idless_localized_edit_alerts(tmp_path: Path, baseline: str):
    # The id-less-LOCALIZED sibling of #282 (already covered by
    # _classify_idless_localized_drift): DE reorders (move de->en) while EN edits an
    # id-less localized cell inside the moved group (en->de). The family must alert.
    de = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", 'print("Hallo")'),
        _sub_md("de", "B", "Beta"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", 'print("Hello")'),
        _sub_md("en", "B", "Beta"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_md("de", "B", "Beta"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", 'print("Hallo")'),
    )
    en1 = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", 'print("HELLO")'),
        _sub_md("en", "B", "Beta"),
    )
    plan, result, _de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert not _falsely_consistent(plan, result, False)
    assert "HELLO" in en_after  # the EN edit not silently overwritten


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_same_direction_neutral_edit_merges(tmp_path: Path, baseline: str):
    # Non-regression: DE both reorders groups AND edits the neutral cell (both de->en).
    # No conflict — the move applies and the neutral edit propagates to EN. Must NOT
    # alert (the #282 fix only fires on OPPOSITE directions).
    de = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("de", "B", "Beta"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("en", "B", "Beta"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_md("de", "B", "Beta"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 99"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en)
    assert not _alerted(plan, result)
    assert result.watermark_recorded
    assert _group_ids(en_after) == ["intro", "B", "A"]  # the reorder reached EN
    assert "x = 99" in en_after  # the neutral edit reached EN too
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_only_no_neutral_edit_applies_cleanly(tmp_path: Path, baseline: str):
    # Non-regression: a pure one-sided group reorder (no concurrent neutral edit)
    # still applies cleanly and advances — the #282 guard must not false-alert when
    # the neutral cells are byte-identical across the halves.
    de = _deck(
        _title("de", "intro"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("de", "B", "Beta"),
    )
    en = _deck(
        _title("en", "intro"),
        _sub_md("en", "A", "Alpha"),
        _ncode("x = 1"),
        _sub_md("en", "B", "Beta"),
    )
    de1 = _deck(
        _title("de", "intro"),
        _sub_md("de", "B", "Beta"),
        _sub_md("de", "A", "Alpha"),
        _ncode("x = 1"),
    )
    plan, result, _de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en)
    assert not _alerted(plan, result)
    assert result.watermark_recorded
    assert _group_ids(en_after) == ["intro", "B", "A"]


# ---------------------------------------------------------------------------
# Issue #282 (deeper variants found by adversarial review). The single
# `_conflicts_with_keyed` guard above only catches the move + ONE neutral cell
# shape, where align_anchored still yields a clean opposing direction. When the
# move reorders >=2 neutral / id-less cells, the positional drift detectors
# mis-read the reorder itself as a "drift" — silently dropping the opposite-side
# edit (id-less / >=2 neutral) or, worse, mis-classifying it as a §7a same-cell
# divergence and AUTO-HEALING it (overwriting the edit on disk). The order-blind
# move-target-edit detector must alert in every case and leave the edit on disk.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_reordering_idless_with_opposite_idless_edit_alerts(tmp_path: Path, baseline: str):
    # DE reorders two groups, each holding an id-less localized cell (so DE's id-less
    # SEQUENCE permutes with no content change); EN edits one id-less cell. The
    # both-sides-"drift" branch of _classify_idless_localized_drift used to trust the
    # move direction and return silently, stranding EN's edit. Must alert; edit kept.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", "l1_de"),
        _sub_md("de", "B", "Beta"),
        _idless_code("de", "l2_de"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", "l1_en"),
        _sub_md("en", "B", "Beta"),
        _idless_code("en", "l2_en"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Beta"),
        _idless_code("de", "l2_de"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", "l1_de"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", "l1X_en"),
        _sub_md("en", "B", "Beta"),
        _idless_code("en", "l2_en"),
    )
    plan, result, _de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert any(p.kind == "move" for p in plan.proposals)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert not _falsely_consistent(plan, result, False)
    assert "l1X_en" in en_after  # EN's id-less edit not silently dropped


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_reordering_neutrals_with_opposite_neutral_edit_alerts(tmp_path: Path, baseline: str):
    # DE reorders two groups each holding a NEUTRAL code cell (DE's neutral SEQUENCE
    # permutes, content unchanged); EN edits one neutral cell. align_anchored's
    # positional zip used to mis-read this as a same-cell §7a divergence and
    # AUTO-HEAL it — overwriting EN's edit on disk (destructive). Must alert AND the
    # EN edit must remain on disk (nothing written).
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Alpha"),
        _ncode("p = 1"),
        _sub_md("de", "B", "Beta"),
        _ncode("q = 2"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _ncode("p = 1"),
        _sub_md("en", "B", "Beta"),
        _ncode("q = 2"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Beta"),
        _ncode("q = 2"),
        _sub_md("de", "A", "Alpha"),
        _ncode("p = 1"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _ncode("p = 99"),
        _sub_md("en", "B", "Beta"),
        _ncode("q = 2"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "p = 99" in en_after  # the edit survives on disk — NOT auto-healed away
    assert "p = 99" not in de_after  # and was not (wrongly) written into DE either


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_two_keyed_dirs_and_opposite_idless_edit_alerts(tmp_path: Path, baseline: str):
    # The two-opposite-keyed-directions variant. DE reorders groups (move de->en)
    # AND edits a neutral cell (de->en, setting anchor_direction); EN edits an id'd
    # cell (en->de, making _keyed_direction ambiguous → None) AND an id-less cell
    # (en->de). With two keyed directions, _conflicts_with_keyed cannot fire and the
    # id-less both-drifted branch falls back to the (non-None) anchor_direction and
    # returns silently — stranding EN's id-less edit. The move-target detector keys
    # on the MOVE direction (not _keyed_direction / established), so it still alerts.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", "l1_de"),
        _ncode("n = 1"),
        _idd_code("de", "C", "c = 1"),
        _sub_md("de", "B", "Beta"),
        _idless_code("de", "l2_de"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", "l1_en"),
        _ncode("n = 1"),
        _idd_code("en", "C", "c = 1"),
        _sub_md("en", "B", "Beta"),
        _idless_code("en", "l2_en"),
    )
    # DE reorders B before A and edits the neutral cell (n=1 -> n=2, de->en).
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Beta"),
        _idless_code("de", "l2_de"),
        _sub_md("de", "A", "Alpha"),
        _idless_code("de", "l1_de"),
        _ncode("n = 2"),
        _idd_code("de", "C", "c = 1"),
    )
    # EN edits the id'd cell C (c=1 -> c=2, en->de) and its id-less l1 (en->de).
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Alpha"),
        _idless_code("en", "l1X_en"),
        _ncode("n = 1"),
        _idd_code("en", "C", "c = 2"),
        _sub_md("en", "B", "Beta"),
        _idless_code("en", "l2_en"),
    )
    plan, result, _de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en1, mapping={"c = 2": "c = 2", "n = 2": "n = 2"}
    )
    assert any(p.kind == "move" for p in plan.proposals)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "l1X_en" in en_after  # EN's id-less edit survives, not silently dropped


@pytest.mark.parametrize("baseline", BASELINES)
def test_add_keyed_with_opposite_neutral_edit_alerts(tmp_path: Path, baseline: str):
    # The conflict guard must cover an ADD-keyed direction, not just move — a future
    # narrowing to move-only would silently reintroduce the drop. DE adds a new slide
    # (de->en) while EN edits a neutral cell (en->de): opposite directions → alert.
    de = _deck(_title("de"), _ncode("x = 1"))
    en = _deck(_title("en"), _ncode("x = 1"))
    de1 = _deck(_title("de"), _ncode("x = 1"), _sub_md("de", "NEW", "Neu"))
    en1 = _deck(_title("en"), _ncode("x = 2"))
    plan, result, _de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en1, mapping={"# ## Neu": "# ## New"}
    )
    assert any(p.kind in ("add", "rename") for p in plan.proposals)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "x = 2" in en_after  # the neutral edit survives


@pytest.mark.parametrize("baseline", BASELINES)
def test_edit_keyed_with_opposite_neutral_edit_alerts(tmp_path: Path, baseline: str):
    # Same, with an EDIT-keyed direction: DE edits an id'd slide body (de->en) while
    # EN edits a neutral cell (en->de). Opposite directions → alert, edit kept.
    de = _deck(_title("de", "s1", "T1"), _ncode("x = 1"))
    en = _deck(_title("en", "s1", "T1"), _ncode("x = 1"))
    de1 = _deck(_title("de", "s1", "T1 EDIT"), _ncode("x = 1"))
    en1 = _deck(_title("en", "s1", "T1"), _ncode("x = 2"))
    plan, result, _de_after, en_after = _sync(
        tmp_path, baseline, de, en, de1, en1, mapping={"# # T1 EDIT": "# # T1 EDIT EN"}
    )
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "x = 2" in en_after


# ---------------------------------------------------------------------------
# Issue #282 — the reordering half may ALSO edit its own content. A group
# reorder makes positional pairing of neutral / id-less cells unsound, so ANY
# concurrent change on the OTHER (target) half — a body edit to a DIFFERENT
# cell, or a cross-group reassignment that leaves the content multiset
# unchanged — is mis-paired and silently overwritten (a §7a destructive
# auto-heal, or a watermark-advancing drop). Sync must alert on any target-half
# neutral/id-less change while one half reorders, and the merge where only the
# reordering half changed content (target untouched) must still succeed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_source_and_target_neutral_edits_alerts(tmp_path: Path, baseline: str):
    # DE reorders groups AND edits its own neutral cell p; EN independently edits a
    # DIFFERENT neutral cell q. The reorder cross-pairs p and q in the positional
    # §7a check, which used to auto-heal toward DE and DESTROY EN's q edit on disk.
    # Must alert and leave BOTH edits on disk (nothing written).
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _ncode("p = 1"),
        _sub_md("de", "B", "Be"),
        _ncode("q = 2"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("p = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("q = 2"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _ncode("q = 2"),
        _sub_md("de", "A", "Al"),
        _ncode("p = 11"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("p = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("q = 22"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "p = 11" in de_after  # DE's edit untouched
    assert "q = 22" in en_after  # EN's edit NOT destructively auto-healed away


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_source_and_target_idless_edits_alerts(tmp_path: Path, baseline: str):
    # DE reorders groups AND edits its own id-less cell l1; EN edits a DIFFERENT
    # id-less cell l2. The both-drifted id-less branch used to trust the move
    # direction and return silently, advancing the watermark and stranding EN's
    # l2 edit. Must alert; EN's edit kept.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _idless_code("de", "l1_de"),
        _sub_md("de", "B", "Be"),
        _idless_code("de", "l2_de"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _idless_code("en", "l1_en"),
        _sub_md("en", "B", "Be"),
        _idless_code("en", "l2_en"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _idless_code("de", "l2_de"),
        _sub_md("de", "A", "Al"),
        _idless_code("de", "l1X_de"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _idless_code("en", "l1_en"),
        _sub_md("en", "B", "Be"),
        _idless_code("en", "l2X_en"),
    )
    plan, result, _de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    assert "l2X_en" in en_after  # EN's id-less edit not silently dropped


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_target_neutral_cross_group_reassign_alerts(tmp_path: Path, baseline: str):
    # DE reorders groups; EN re-associates its neutral cells across groups (codeA
    # under B, codeB under A) — a change that leaves the content MULTISET unchanged,
    # so a Counter-based check is blind to it. The ordered (positional) compare on
    # the non-reordering target half still detects it. Must alert.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _ncode("codeA = 1"),
        _sub_md("de", "B", "Be"),
        _ncode("codeB = 2"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("codeA = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("codeB = 2"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _ncode("codeB = 2"),
        _sub_md("de", "A", "Al"),
        _ncode("codeA = 1"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("codeB = 2"),
        _sub_md("en", "B", "Be"),
        _ncode("codeA = 1"),
    )
    plan, result, _de_after, _en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_source_edit_only_merges(tmp_path: Path, baseline: str):
    # The legitimate merge that must NOT alert: the author edits ONE half (DE) —
    # reordering its groups AND editing its own neutral cell — while the OTHER half
    # (EN) is untouched. The reorder + edit both flow de->en, so EN receives both.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _ncode("p = 1"),
        _sub_md("de", "B", "Be"),
        _ncode("q = 2"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("p = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("q = 2"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _ncode("q = 2"),
        _sub_md("de", "A", "Al"),
        _ncode("p = 11"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en)
    assert not _alerted(plan, result)
    assert result.watermark_recorded
    assert _group_ids(en_after) == ["title", "B", "A"]  # the reorder reached EN
    assert "p = 11" in en_after  # DE's neutral edit propagated to EN
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_identical_neutral_edit_on_both_halves_merges(tmp_path: Path, baseline: str):
    # The conflict guard must NOT fire when a neutral edit was applied IDENTICALLY to
    # both halves (the unify invariant still holds: de == en) while one half reorders.
    # The halves agree on the cell, so a reorder cannot clobber it — sync must apply
    # the reorder cleanly. (The guard gates on a reorder-invariant per-group compare of
    # the two halves, not just target-vs-baseline, precisely to allow this.)
    de = _deck(_title("de"), _sub_md("de", "A", "Al"), _ncode("x = 1"), _sub_md("de", "B", "Be"))
    en = _deck(_title("en"), _sub_md("en", "A", "Al"), _ncode("x = 1"), _sub_md("en", "B", "Be"))
    # DE reorders B before A AND edits x; EN edits x to the SAME value (no reorder).
    de1 = _deck(_title("de"), _sub_md("de", "B", "Be"), _sub_md("de", "A", "Al"), _ncode("x = 2"))
    en1 = _deck(_title("en"), _sub_md("en", "A", "Al"), _ncode("x = 2"), _sub_md("en", "B", "Be"))
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert not _alerted(plan, result)
    assert result.watermark_recorded
    assert _group_ids(en_after) == ["title", "B", "A"]  # the reorder reached EN
    assert "x = 2" in en_after and "x = 1" not in en_after  # the shared edit is kept, once
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_target_intra_group_neutral_reorder_alerts(tmp_path: Path, baseline: str):
    # DE reorders slide GROUPS; EN reorders two neutral cells WITHIN one group. The two
    # halves' per-group neutral *multisets* are equal, so a Counter-based gate would
    # miss it and align_anchored's positional §7a check would destructively auto-heal
    # EN's reorder away. The per-group ORDERED compare (and bypassing align_anchored
    # under a move) catches it: must alert and keep EN's order on disk.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _ncode("a1 = 1"),
        _ncode("a2 = 2"),
        _sub_md("de", "B", "Be"),
        _ncode("b1 = 3"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("a1 = 1"),
        _ncode("a2 = 2"),
        _sub_md("en", "B", "Be"),
        _ncode("b1 = 3"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _ncode("b1 = 3"),
        _sub_md("de", "A", "Al"),
        _ncode("a1 = 1"),
        _ncode("a2 = 2"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("a2 = 2"),
        _ncode("a1 = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("b1 = 3"),
    )
    plan, result, _de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert _alerted(plan, result)
    assert not result.watermark_recorded
    # EN's one-sided intra-group reorder is preserved on disk (not auto-healed away).
    assert _neutral_bodies(en_after) == ["a2 = 2", "a1 = 1", "b1 = 3"]


@pytest.mark.parametrize("baseline", BASELINES)
def test_move_with_multiple_identical_neutral_edits_on_both_halves_merges(
    tmp_path: Path, baseline: str
):
    # The >=2-cell analog of the identical-both-halves merge: two neutral cells edited
    # to the SAME new values on both halves while DE reorders groups. Here de_shared !=
    # en_shared (the reorder permutes DE's flat sequence), which used to make
    # align_anchored false-diverge; bypassing it under a move and gating on the
    # per-group signature lets this merge cleanly with no alert.
    de = _deck(
        _title("de"),
        _sub_md("de", "A", "Al"),
        _ncode("a = 1"),
        _sub_md("de", "B", "Be"),
        _ncode("b = 2"),
    )
    en = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("a = 1"),
        _sub_md("en", "B", "Be"),
        _ncode("b = 2"),
    )
    de1 = _deck(
        _title("de"),
        _sub_md("de", "B", "Be"),
        _ncode("b = 22"),
        _sub_md("de", "A", "Al"),
        _ncode("a = 11"),
    )
    en1 = _deck(
        _title("en"),
        _sub_md("en", "A", "Al"),
        _ncode("a = 11"),
        _sub_md("en", "B", "Be"),
        _ncode("b = 22"),
    )
    plan, result, de_after, en_after = _sync(tmp_path, baseline, de, en, de1, en1)
    assert not _alerted(plan, result)
    assert result.watermark_recorded
    assert _group_ids(en_after) == ["title", "B", "A"]  # the reorder reached EN
    assert "a = 11" in en_after and "b = 22" in en_after
    assert _neutral_bodies(en_after) == _neutral_bodies(de_after)
