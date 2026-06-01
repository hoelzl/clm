"""Behavioral tests for the two *serious* ``clm slides sync`` limitations
(Issue #190 items 2 & 3) — both now FIXED.

* **item 2** — *FIXED (Phase 3a)*: a code-only edit to a *language-neutral*
  shared cell is detected by the anchor diff (``align_anchored``) — which side
  drifted from the watermark gives the direction — and the structural pass copies
  it verbatim to the twin, no LLM. ``test_item2_*`` asserts the fix.
* **item 3** — *FIXED (Phase 2)*: when a slide group is rebuilt for a *sibling's*
  sake, an unchanged id-less localized code cell is spliced verbatim by its
  content anchor instead of being re-translated. ``test_item3_*`` asserts the fix
  (the translator is never called for the unchanged cell).

Tiny synthetic decks, fast suite, no corpus, no network (the translator/judge
are counting stand-ins).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from clm.infrastructure.llm.cache import SyncAlignmentCache, SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import _resolve_alignment, apply_plan
from clm.slides.sync_plan import build_sync_plan, watermark_rows
from clm.slides.sync_recover import (
    NEW,
    RegionCell,
    StaticAlignmentRecoverer,
)

# ---------------------------------------------------------------------------
# Deck builders + no-LLM spies
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n'


def _code_shared(body: str) -> str:
    """A language-neutral shared code cell (item-2 territory)."""
    return f'# %% tags=["keep"]\n{body}\n'


def _code_localized_idless(lang: str, body: str) -> str:
    """An id-less localized code cell (item-3 territory)."""
    return f'# %% lang="{lang}"\n{body}\n'


def _code_idd_neutral(sid: str, body: str) -> str:
    """A language-neutral code cell carrying a slide_id (the def-my-fun shape)."""
    return f'# %% tags=["keep"] slide_id="{sid}"\n{body}\n'


def _code_localized_idd(lang: str, sid: str, body: str) -> str:
    """A localized code cell carrying a slide_id (the Phase 5d paired shape)."""
    return f'# %% lang="{lang}" tags=["keep"] slide_id="{sid}"\n{body}\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    """Seed the membership-widened watermark exactly as ``_record_watermark`` does."""
    de_rows = watermark_rows(parse_cells(de_path.read_text(encoding="utf-8")))
    en_rows = watermark_rows(parse_cells(en_path.read_text(encoding="utf-8")))
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="de", cells=de_rows["de"])
    cache.put_deck(de_path=str(de_path), en_path=str(en_path), lang="en", cells=en_rows["en"])
    cache.put_deck(
        de_path=str(de_path), en_path=str(en_path), lang="shared", cells=de_rows["shared"]
    )


@dataclass
class CountingTranslator:
    prompt_version: str = "counting"
    calls: list[tuple[str, str, str, str]] = field(default_factory=list)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append((role, source_lang, target_lang, source_body))
        return source_body


@dataclass
class CountingJudge:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def propose(
        self, source_body: str, target_body: str, *, source_lang: str, target_lang: str
    ) -> SyncProposal:
        self.calls.append((source_lang, target_lang))
        return SyncProposal(verdict="in_sync", proposed_text=target_body)


# ---------------------------------------------------------------------------
# Item 2 — FIXED (Phase 3a): a code-only change to a neutral shared cell is
# detected by the anchor diff and copied verbatim to the twin (no LLM).
# ---------------------------------------------------------------------------


def test_item2_neutral_code_only_edit_propagates_verbatim(tmp_path: Path):
    de = _slide("de", "a", "# ## A") + _code_shared("import time")
    en = _slide("en", "a", "# ## A") + _code_shared("import time")
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        # Author edits ONLY the shared, language-neutral code cell on DE — no
        # narrative or id'd change, so the keyed classifier produces no proposal.
        de_path.write_text(
            _slide("de", "a", "# ## A") + _code_shared("import time\nx = 1"),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    # FIXED: the anchor diff detects DE drifted -> de->en direction; no keyed
    # proposal, so it is NOT a no-op.
    assert not plan.is_noop
    assert plan.anchor_direction == "de->en"
    # A neutral cell is shared verbatim — copied to the EN twin, never translated.
    assert translator.calls == []
    assert "x = 1" in en_path.read_text(encoding="utf-8")


def test_item2_duplicate_construct_non_last_edit_propagates(tmp_path: Path):
    # Two neutral cells share construct:print. Editing the NON-LAST one must still
    # propagate — an anchor-keyed map would collapse them last-writer-wins and
    # silently drop the edit (Issue #190 review). The ordered-hash detector sees it.
    de = _slide("de", "a", "# ## A") + _code_shared('print("a")') + _code_shared('print("b")')
    en = _slide("en", "a", "# ## A") + _code_shared('print("a")') + _code_shared('print("b")')
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "a", "# ## A") + _code_shared('print("z")') + _code_shared('print("b")'),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    assert plan.anchor_direction == "de->en"
    en_text = en_path.read_text(encoding="utf-8")
    assert 'print("z")' in en_text  # the non-last edit propagated
    assert 'print("a")' not in en_text
    assert 'print("b")' in en_text  # the unchanged sibling is intact


def test_item2b_localized_idless_code_edit_is_retranslated(tmp_path: Path):
    # An id-less LOCALIZED code cell edited (body changed, construct stable) must
    # be re-translated. Its ("L", kind) signature is unchanged by a body edit, so
    # the group is force-rebuilt because the cell drifted from baseline (Phase 3b).
    # Direction comes from a co-occurring narrative edit (single-language workflow).
    de = _slide("de", "g", "# ## G") + _code_localized_idless("de", '# Komm\nprint("a")')
    en = _slide("en", "g", "# ## G") + _code_localized_idless("en", '# Comment\nprint("a")')
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")  # narrative edit -> keyed direction
            + _code_localized_idless("de", '# Komm\nprint("b")'),  # localized code edited
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    assert plan.count("edit") == 1  # the narrative edit supplies the direction
    # The drifted localized code cell was re-translated (group force-rebuilt).
    retranslated = [body for (_r, _sl, _tl, body) in translator.calls if 'print("b")' in body]
    assert retranslated, f"the edited localized code must be re-translated; got {translator.calls}"


# ---------------------------------------------------------------------------
# Phase 3c — shared-cell divergence (both decks edited the same neutral cell)
# ---------------------------------------------------------------------------


def _diverge(tmp_path: Path) -> tuple[Path, Path, SyncWatermarkCache]:
    """Seed a synced pair, then edit the SAME neutral cell differently on both halves."""
    de = _slide("de", "a", "# ## A") + _code_shared("import time")
    en = _slide("en", "a", "# ## A") + _code_shared("import time")
    de_path, en_path = _write_pair(tmp_path, de, en)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    de_path.write_text(
        _slide("de", "a", "# ## A") + _code_shared("import time\nx = 1"), encoding="utf-8"
    )
    en_path.write_text(
        _slide("en", "a", "# ## A") + _code_shared("import time\ny = 2"), encoding="utf-8"
    )
    return de_path, en_path, cache


def test_shared_divergence_auto_heals_toward_newer_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLM_SYNC__SHARED_DIVERGENCE", raising=False)  # default auto-heal
    de_path, en_path, cache = _diverge(tmp_path)
    os.utime(en_path, (1_600_000_000, 1_600_000_000))  # EN older
    os.utime(de_path, (1_600_000_900, 1_600_000_900))  # DE newer -> DE wins
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    assert plan.anchor_direction == "de->en"  # newer (DE) won
    assert any(i.severity == "warning" for i in plan.issues)
    assert not plan.has_errors
    en_text = en_path.read_text(encoding="utf-8")
    assert "x = 1" in en_text  # DE's version healed onto EN
    assert "y = 2" not in en_text


def test_shared_divergence_error_mode_surfaces_and_writes_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLM_SYNC__SHARED_DIVERGENCE", "error")
    de_path, en_path, cache = _diverge(tmp_path)
    en_before = en_path.read_bytes()
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    assert plan.has_errors
    assert plan.anchor_direction is None
    assert en_path.read_bytes() == en_before  # error -> the buffered apply writes nothing


def test_shared_divergence_no_winner_errors(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLM_SYNC__SHARED_DIVERGENCE", raising=False)
    de_path, en_path, cache = _diverge(tmp_path)
    os.utime(de_path, (1_600_000_000, 1_600_000_000))  # mtimes tie, no keyed edit
    os.utime(en_path, (1_600_000_000, 1_600_000_000))
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
    finally:
        cache.close()

    assert plan.has_errors  # no winner -> error even in auto-heal mode
    assert plan.anchor_direction is None


def test_independent_cross_cell_edits_error_without_reverting(tmp_path: Path, monkeypatch):
    # DE edits neutral cell A; EN edits a DIFFERENT neutral cell B (two compatible
    # one-sided edits). A whole-file divergence verdict + auto-heal would pick one
    # winner and silently REVERT the other's edit (Phase 3c review, the data-loss
    # bug). Cell-precise classification makes this irreconcilable -> error -> the
    # buffered apply writes nothing, so NEITHER edit is lost.
    monkeypatch.delenv("CLM_SYNC__SHARED_DIVERGENCE", raising=False)  # default auto-heal
    de = _slide("de", "a", "# ## A") + _code_shared("import time") + _code_shared("import os")
    en = _slide("en", "a", "# ## A") + _code_shared("import time") + _code_shared("import os")
    de_path, en_path = _write_pair(tmp_path, de, en)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "a", "# ## A")
            + _code_shared("import time\nx = 1")
            + _code_shared("import os"),
            encoding="utf-8",
        )
        en_path.write_text(
            _slide("en", "a", "# ## A")
            + _code_shared("import time")
            + _code_shared("import os\ny = 2"),
            encoding="utf-8",
        )
        os.utime(
            de_path, (1_600_000_900, 1_600_000_900)
        )  # DE newer -> would have won + reverted EN
        os.utime(en_path, (1_600_000_000, 1_600_000_000))
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert plan.has_errors
    assert plan.anchor_direction is None
    assert "x = 1" in de_path.read_text(encoding="utf-8")  # DE's edit preserved
    assert "y = 2" in en_path.read_text(encoding="utf-8")  # EN's edit NOT reverted


# ---------------------------------------------------------------------------
# Phase 4 — deterministic def-my-fun id-migration (§9)
# ---------------------------------------------------------------------------


def test_phase4_def_my_fun_id_migration(tmp_path: Path):
    # The author splits an id'd neutral code cell — adds `import time` above the
    # def, leaving slide_id="def-my-fun" on the import. The id has drifted off its
    # construct; a different id-less cell now carries `function my_fun`. The sync
    # deterministically moves the id back to the def cell and mints a content slug
    # on the orphan import — no LLM — and propagates the corrected ids to the twin.
    de = _slide("de", "s", "# ## S") + _code_idd_neutral(
        "def-my-fun", 'def my_fun():\n    print("foo")'
    )
    en = _slide("en", "s", "# ## S") + _code_idd_neutral(
        "def-my-fun", 'def my_fun():\n    print("foo")'
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "s", "# ## S")
            + _code_idd_neutral("def-my-fun", "import time")  # id left on the import half
            + _code_shared('def my_fun():\n    time.sleep(1)\n    print("foo")'),  # id-less def
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    assert result.applied_migrate == 1
    assert translator.calls == []  # deterministic, no LLM
    for path in (de_path, en_path):  # corrected ids on BOTH decks (neutral -> symmetric)
        by_id = {
            c.metadata.slide_id: c
            for c in parse_cells(path.read_text(encoding="utf-8"))
            if c.metadata.slide_id
        }
        assert "def my_fun" in by_id["def-my-fun"].content  # id followed its construct
        assert "time.sleep(1)" in by_id["def-my-fun"].content  # the edited body came too
        assert "import time" in by_id["import-time"].content  # orphan got a content slug


def test_phase4_no_migration_without_matching_idless_cell(tmp_path: Path):
    # The id drifted (def -> import on the SAME cell) but there is NO id-less cell
    # carrying the old construct (no split — a genuine replacement). The migration
    # must NOT fire (it is not an unambiguous id-move); the cell is left for the
    # ordinary edit/structural path.
    de = _slide("de", "s", "# ## S") + _code_idd_neutral(
        "def-my-fun", 'def my_fun():\n    print("foo")'
    )
    en = _slide("en", "s", "# ## S") + _code_idd_neutral(
        "def-my-fun", 'def my_fun():\n    print("foo")'
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "s", "# ## S")
            + _code_idd_neutral("def-my-fun", "import time"),  # replaced
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0  # no id-less construct match -> no migration


def test_phase4_no_false_move_to_unrelated_preexisting_cell(tmp_path: Path):
    # An id'd cell's construct changes via a legitimate edit (import os -> config),
    # and an UNRELATED, pre-existing id-less cell happens to carry the old construct
    # (import os). The migration must NOT move the id onto that unrelated cell — it
    # is not a split-product (its content is in the baseline). (Review finding.)
    de = (
        _slide("de", "s", "# ## S")
        + _code_idd_neutral("setup", "import os\nsetup_run()")
        + _code_shared("import os\nother_run()")  # unrelated, id-less, same construct
    )
    en = (
        _slide("en", "s", "# ## S")
        + _code_idd_neutral("setup", "import os\nsetup_run()")
        + _code_shared("import os\nother_run()")
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "s", "# ## S")
            + _code_idd_neutral("setup", "config = load()\nsetup_run()")  # construct changed
            + _code_shared("import os\nother_run()"),  # unrelated cell unchanged
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0  # no false move
    by_id = {
        c.metadata.slide_id: c
        for c in parse_cells(de_path.read_text(encoding="utf-8"))
        if c.metadata.slide_id
    }
    assert "config = load()" in by_id["setup"].content  # the id stayed on the edited cell


def test_phase4_id_migration_is_symmetric_across_decks(tmp_path: Path):
    # The def-my-fun split is present byte-identically on BOTH decks (and a narrative
    # edit supplies the direction). The migration must write BOTH decks — the
    # structural pass keys on the body and can't carry a header-only id change — so
    # the corrected ids must NOT diverge across de/en. (Review finding, critical.)
    base_def = 'def my_fun():\n    print("foo")'
    new_def = 'def my_fun():\n    time.sleep(1)\n    print("foo")'
    de0 = _slide("de", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    en0 = _slide("en", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    de_path, en_path = _write_pair(tmp_path, de0, en0)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        # Both decks split identically; only DE's narrative changes (the direction).
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")
            + _code_idd_neutral("def-my-fun", "import time")
            + _code_shared(new_def),
            encoding="utf-8",
        )
        en_path.write_text(
            _slide("en", "g", "# ## G")
            + _code_idd_neutral("def-my-fun", "import time")
            + _code_shared(new_def),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 2  # migrated on BOTH decks
    ids = []
    for path in (de_path, en_path):
        by_id = {
            c.metadata.slide_id: c
            for c in parse_cells(path.read_text(encoding="utf-8"))
            if c.metadata.slide_id
        }
        assert "def my_fun" in by_id["def-my-fun"].content
        assert "import time" in by_id["import-time"].content
        ids.append(sorted(by_id))
    assert ids[0] == ids[1]  # identical slide_ids on both decks — no divergence


# ---------------------------------------------------------------------------
# Phase 5 — bounded LLM recovery of an ambiguous drifted-id region (§10)
# ---------------------------------------------------------------------------


def _split_renamed_both_decks(tmp_path: Path):
    """Seed a watermark, then split+RENAME the def on both decks (de narrative
    edit supplies the direction). Returns (de_path, en_path, cache). The rename
    means the deterministic §9 construct match fails → recovery territory."""
    base_def = 'def my_fun():\n    print("foo")'
    de0 = _slide("de", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    en0 = _slide("en", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    de_path, en_path = _write_pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    _seed(cache, de_path, en_path)
    # Both decks: import wears the id; the def is RENAMED (my_function) and id-less.
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


def test_phase5_llm_recover_resolves_renamed_split(tmp_path: Path):
    # The deterministic §9 migration is stuck (the def was renamed, so no id-less
    # cell carries the baseline construct). With --llm-recover, the recoverer maps
    # the import as new and the renamed def as the def-my-fun continuation; the
    # engine applies it symmetrically to both decks. Resolved ONCE, applied to both.
    de_path, en_path, cache = _split_renamed_both_decks(tmp_path)
    recoverer = StaticAlignmentRecoverer(mapping={0: NEW, 1: "def-my-fun"})
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan,
            judge=CountingJudge(),
            translator=CountingTranslator(),
            watermark_cache=cache,
            recoverer=recoverer,
        )
    finally:
        cache.close()

    assert recoverer.calls == 1  # resolved once, applied to both decks
    assert result.applied_migrate == 4  # 2 cells re-identified on each of 2 decks
    for path in (de_path, en_path):
        by_id = {
            c.metadata.slide_id: c
            for c in parse_cells(path.read_text(encoding="utf-8"))
            if c.metadata.slide_id
        }
        assert "def my_function" in by_id["def-my-fun"].content  # id followed the rename
        assert "import time" in by_id["import-time"].content  # orphan got a content slug


def test_phase5_no_recover_leaves_region_untouched(tmp_path: Path):
    # Without --llm-recover (recoverer=None), the ambiguous region is left for
    # review: no migration, no error (the pre-Phase-5 behavior).
    de_path, en_path, cache = _split_renamed_both_decks(tmp_path)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0
    by_id = {
        c.metadata.slide_id: c
        for c in parse_cells(de_path.read_text(encoding="utf-8"))
        if c.metadata.slide_id
    }
    assert "import time" in by_id["def-my-fun"].content  # id still on the import (untouched)


def test_phase5_invalid_map_safe_aborts(tmp_path: Path):
    # A recoverer that returns an unsound map (an invented id) must safe-abort: the
    # region is deferred and left untouched, never half-applied, and not an error
    # (so the rest of the deck still writes).
    de_path, en_path, cache = _split_renamed_both_decks(tmp_path)
    recoverer = StaticAlignmentRecoverer(mapping={0: "not-a-base-id", 1: "def-my-fun"})
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan,
            judge=CountingJudge(),
            translator=CountingTranslator(),
            watermark_cache=cache,
            recoverer=recoverer,
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0
    assert result.deferred >= 1  # safe-aborted -> region deferred, re-surfaces next run
    assert result.errors == []  # a deferral, not a blocking error
    assert not result.watermark_recorded  # held so the region re-surfaces


def test_phase5_recoverer_failure_safe_aborts(tmp_path: Path):
    # The recoverer being down (RecoveryError) safe-aborts the same way.
    de_path, en_path, cache = _split_renamed_both_decks(tmp_path)
    recoverer = StaticAlignmentRecoverer(raise_error=True)
    try:
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan,
            judge=CountingJudge(),
            translator=CountingTranslator(),
            watermark_cache=cache,
            recoverer=recoverer,
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0
    assert result.deferred >= 1
    assert result.errors == []


def test_phase5_deterministic_case_does_not_call_recoverer(tmp_path: Path):
    # When the deterministic §9 migration CAN resolve the split (the def keeps its
    # name), the two tiers are not mixed — the recoverer is never consulted.
    base_def = 'def my_fun():\n    print("foo")'
    de0 = _slide("de", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    en0 = _slide("en", "g", "# ## G") + _code_idd_neutral("def-my-fun", base_def)
    de_path, en_path = _write_pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    recoverer = StaticAlignmentRecoverer(raise_error=True)  # would blow up if called
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")
            + _code_idd_neutral("def-my-fun", "import time")
            + _code_shared('def my_fun():\n    time.sleep(1)\n    print("foo")'),  # SAME name
            encoding="utf-8",
        )
        en_path.write_text(
            _slide("en", "g", "# ## G")
            + _code_idd_neutral("def-my-fun", "import time")
            + _code_shared('def my_fun():\n    time.sleep(1)\n    print("foo")'),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan,
            judge=CountingJudge(),
            translator=CountingTranslator(),
            watermark_cache=cache,
            recoverer=recoverer,
        )
    finally:
        cache.close()

    assert recoverer.calls == 0  # deterministic handled it; LLM not consulted
    assert result.applied_migrate == 2  # one move per deck (the def kept its name)


def test_phase5_alignment_is_cached_and_reused(tmp_path: Path):
    # The validated map is cached by region fingerprint: a second resolve with a
    # recoverer that would FAIL still succeeds from the cache (no LLM re-spend).
    base = [RegionCell("def-my-fun", "function-my-fun", "h0")]
    current = [
        RegionCell("def-my-fun", "import-time", "h1"),
        RegionCell(None, "function-my-function", "h2"),
    ]
    align_cache = SyncAlignmentCache(tmp_path / "clm-llm.sqlite")
    try:
        r1 = StaticAlignmentRecoverer(mapping={0: NEW, 1: "def-my-fun"})
        first = _resolve_alignment(r1, align_cache, base, current)
        assert first == {0: NEW, 1: "def-my-fun"}
        assert r1.calls == 1
        assert len(align_cache.iter_entries()) == 1  # written through

        r2 = StaticAlignmentRecoverer(raise_error=True)  # cache hit must avoid this
        second = _resolve_alignment(r2, align_cache, base, current)
        assert second == {0: NEW, 1: "def-my-fun"}
        assert r2.calls == 0  # served from cache, recoverer never called
    finally:
        align_cache.close()


# ---------------------------------------------------------------------------
# Phase 5d — symmetric localized id-migration paired chokepoint (§9 localized)
# ---------------------------------------------------------------------------


def test_phase5d_localized_id_migration_is_paired(tmp_path: Path):
    # A LOCALIZED id'd code cell (lang=) is split on both decks: the id is left on
    # the new import while the def — translated differently per deck — is id-less.
    # The paired chokepoint moves the id onto BOTH decks' def twins and mints one
    # shared slug onto BOTH import orphans, keeping de_id == en_id (the structural
    # pass cannot carry a header-only change between the two translated bodies).
    de0 = _slide("de", "g", "# ## G") + _code_localized_idd(
        "de", "greet", 'def greet():\n    print("Hallo")'
    )
    en0 = _slide("en", "g", "# ## G") + _code_localized_idd(
        "en", "greet", 'def greet():\n    print("Hello")'
    )
    de_path, en_path = _write_pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")  # narrative edit -> direction de->en
            + _code_localized_idd("de", "greet", "import time")  # id left on the import
            + _code_localized_idless("de", 'def greet():\n    time.sleep(1)\n    print("Hallo")'),
            encoding="utf-8",
        )
        en_path.write_text(
            _slide("en", "g", "# ## G")
            + _code_localized_idd("en", "greet", "import time")
            + _code_localized_idless("en", 'def greet():\n    time.sleep(1)\n    print("Hello")'),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 2  # one logical move, written to both decks
    de_by_id = {
        c.metadata.slide_id: c
        for c in parse_cells(de_path.read_text(encoding="utf-8"))
        if c.metadata.slide_id
    }
    en_by_id = {
        c.metadata.slide_id: c
        for c in parse_cells(en_path.read_text(encoding="utf-8"))
        if c.metadata.slide_id
    }
    # The id followed its construct on BOTH decks, preserving the translated bodies.
    assert "def greet" in de_by_id["greet"].content and "Hallo" in de_by_id["greet"].content
    assert "def greet" in en_by_id["greet"].content and "Hello" in en_by_id["greet"].content
    assert "import time" in de_by_id["import-time"].content
    assert "import time" in en_by_id["import-time"].content
    assert set(de_by_id) == set(en_by_id)  # de_id == en_id preserved (no divergence)


def test_phase5d_no_migration_when_only_one_deck_split(tmp_path: Path):
    # Asymmetric: only DE splits the localized cell; EN's twin still wears the id on
    # the un-split def. The paired guard requires drift on BOTH decks, so no move.
    de0 = _slide("de", "g", "# ## G") + _code_localized_idd(
        "de", "greet", 'def greet():\n    print("Hallo")'
    )
    en0 = _slide("en", "g", "# ## G") + _code_localized_idd(
        "en", "greet", 'def greet():\n    print("Hello")'
    )
    de_path, en_path = _write_pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")
            + _code_localized_idd("de", "greet", "import time")
            + _code_localized_idless("de", 'def greet():\n    time.sleep(1)\n    print("Hallo")'),
            encoding="utf-8",
        )
        # EN untouched (greet still on the def).
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0  # asymmetric split -> paired guard declines


def test_phase5d_no_false_move_localized_without_new_twin(tmp_path: Path):
    # The localized id'd construct changes on both decks (genuine replacement), but
    # there is NO new id-less twin carrying the old construct. The paired migration
    # must not fire (it is not an unambiguous split).
    de0 = _slide("de", "g", "# ## G") + _code_localized_idd(
        "de", "greet", 'def greet():\n    print("Hallo")'
    )
    en0 = _slide("en", "g", "# ## G") + _code_localized_idd(
        "en", "greet", 'def greet():\n    print("Hello")'
    )
    de_path, en_path = _write_pair(tmp_path, de0, en0)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")
            + _code_localized_idd("de", "greet", "import time"),  # replaced, no def twin
            encoding="utf-8",
        )
        en_path.write_text(
            _slide("en", "g", "# ## G") + _code_localized_idd("en", "greet", "import time"),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(
            plan, judge=CountingJudge(), translator=CountingTranslator(), watermark_cache=cache
        )
    finally:
        cache.close()

    assert result.applied_migrate == 0  # no id-less construct twin -> no migration


# ---------------------------------------------------------------------------
# Item 3 — FIXED (Phase 2): an unchanged id-less localized code cell is spliced
# verbatim on a sibling-triggered rebuild, never re-translated.
# ---------------------------------------------------------------------------


def test_item3_unchanged_localized_code_is_reused_not_retranslated(tmp_path: Path):
    de = (
        _slide("de", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("de", "# Kommentar\nx = 1")
    )
    en = (
        _slide("en", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("en", "# Comment\nx = 1")
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        # Edit the narrative (gives the structural pass a de->en direction) AND the
        # neutral shared code (changes the group's signature -> rebuild), leaving
        # the localized code cell byte-identical.
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")
            + _code_shared("import time\nimport os")
            + _code_localized_idless("de", "# Kommentar\nx = 1"),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    # The narrative edit is the only per-cell proposal; it supplies the direction.
    assert plan.count("edit") == 1
    # FIXED: the unchanged localized code cell (anchor construct:x, same content
    # hash as baseline) is spliced verbatim — the translator is never called for it.
    retranslated = [body for (_role, _sl, _tl, body) in translator.calls if "Kommentar" in body]
    assert retranslated == [], (
        f"unchanged localized code must be spliced verbatim, not re-translated; "
        f"got {translator.calls}"
    )
    # The EN twin is preserved verbatim (still its own '# Comment', not a verbatim
    # copy of DE's '# Kommentar' that a re-translation stand-in would have produced).
    en_text = en_path.read_text(encoding="utf-8")
    assert "# Comment" in en_text
    assert "# Kommentar" not in en_text
    # The sibling that triggered the rebuild (neutral code) still propagated.
    assert "import os" in en_text


def test_item3_changed_localized_code_is_still_retranslated(tmp_path: Path):
    # The reuse must NOT over-fire: a genuinely EDITED localized code cell — same
    # construct anchor (construct:x), different content hash — is re-translated,
    # not spliced from the stale target twin.
    de = (
        _slide("de", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("de", "# Kommentar\nx = 1")
    )
    en = (
        _slide("en", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("en", "# Comment\nx = 1")
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")  # narrative edit -> direction
            + _code_shared("import time\nimport os")  # neutral edit -> rebuild
            + _code_localized_idless("de", "# Kommentar\nx = 1\nprint(x)"),  # CHANGED body
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    # The changed localized cell was re-translated (its baseline hash no longer
    # matches), so the translator WAS called for it.
    retranslated = [body for (_role, _sl, _tl, body) in translator.calls if "print(x)" in body]
    assert retranslated, f"a changed localized cell must be re-translated; got {translator.calls}"


def test_item3_duplicate_construct_does_not_splice_wrong_twin(tmp_path: Path):
    # Two id-less localized code cells in one group share a construct anchor
    # (both `result = ...` -> construct:result). The reuse path must NOT splice an
    # arbitrary first-match twin (which dropped one cell and duplicated the other —
    # Issue #190 review, the critical finding). A non-unique anchor disables reuse;
    # both cells translate, so both EN twins survive verbatim.
    de = (
        _slide("de", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("de", "# A\nresult = compute_a()")
        + _code_localized_idless("de", "# B\nresult = compute_b()")
    )
    en = (
        _slide("en", "g", "# ## G")
        + _code_shared("import time")
        + _code_localized_idless("en", "# A\nresult = compute_a()")
        + _code_localized_idless("en", "# B\nresult = compute_b()")
    )
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        de_path.write_text(
            _slide("de", "g", "# ## G erweitert")  # narrative edit -> direction
            + _code_shared("import time\nimport os")  # neutral edit -> rebuild
            + _code_localized_idless("de", "# A\nresult = compute_a()")  # unchanged
            + _code_localized_idless("de", "# B\nresult = compute_b()"),  # unchanged
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    en_text = en_path.read_text(encoding="utf-8")
    # Neither cell is dropped or duplicated — both bodies survive exactly once.
    assert en_text.count("compute_a()") == 1
    assert en_text.count("compute_b()") == 1
