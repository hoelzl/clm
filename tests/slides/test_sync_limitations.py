"""Behavioral tests for the two *serious* ``clm slides sync`` limitations
(Issue #190 items 2 & 3).

* **item 2** — *still broken (Phase 3 will fix)*: a code-only edit to a
  *language-neutral* shared cell produces no proposal, so the sync silently fails
  to propagate it and the two split halves diverge. ``test_item2_*`` pins the
  broken-today behavior; flip it when Phase 3 (the anchor-keyed diff +
  deterministic copy-to-twin) lands.
* **item 3** — *FIXED (Phase 2)*: when a slide group is rebuilt for a *sibling's*
  sake, an unchanged id-less localized code cell is spliced verbatim by its
  content anchor instead of being re-translated. ``test_item3_*`` asserts the fix
  (the translator is never called for the unchanged cell).

Tiny synthetic decks, fast suite, no corpus, no network (the translator/judge
are counting stand-ins).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import SyncProposal
from clm.notebooks.slide_parser import parse_cells
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_plan import build_sync_plan, watermark_rows

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
# Item 2 — a code-only change to a neutral shared cell is not propagated
# ---------------------------------------------------------------------------


def test_item2_neutral_code_only_edit_is_silently_dropped(tmp_path: Path):
    de = _slide("de", "a", "# ## A") + _code_shared("import time")
    en = _slide("en", "a", "# ## A") + _code_shared("import time")
    de_path, en_path = _write_pair(tmp_path, de, en)

    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    translator = CountingTranslator()
    judge = CountingJudge()
    try:
        _seed(cache, de_path, en_path)
        # Author edits ONLY the shared, language-neutral code cell on DE.
        de_path.write_text(
            _slide("de", "a", "# ## A") + _code_shared("import time\nx = 1"),
            encoding="utf-8",
        )
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache, allow_git_fallback=False)
        result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()

    # TODAY: the edit is invisible to the engine — no proposal, no direction.
    assert plan.is_noop
    assert result.applied == 0
    assert translator.calls == []
    # The shared code on EN is NOT updated, so the split halves now diverge.
    assert "x = 1" not in en_path.read_text(encoding="utf-8")
    # (Phase 3 propagates this; flip the four assertions above then.)


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
