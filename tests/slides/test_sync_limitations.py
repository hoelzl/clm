"""Executable documentation of the two *serious* ``clm slides sync`` limitations
(Issue #190 items 2 & 3) as they behave **today**.

These tests pin the broken-today behavior so the Phase 2/3 fixes have a precise
flip-point and the no-op corpus harness has a mechanism to point at:

* **item 2** — a code-only edit to a *language-neutral* shared cell produces no
  proposal, so the sync silently fails to propagate it and the two split halves
  diverge. Phase 3 (the anchor-keyed diff + deterministic copy-to-twin) makes
  the edit propagate; flip ``test_item2_*`` then.
* **item 3** — when a slide group is rebuilt for a *sibling's* sake, an unchanged
  id-less localized code cell in it is re-translated, because its ``("L", kind)``
  structural signature cannot prove it is unchanged. Phase 2 (anchor + content
  hash verbatim reuse) splices it without translating; flip ``test_item3_*`` then.

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
from clm.slides.sync_plan import build_sync_plan, ordered_sync_cells

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
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash) for c in cells],
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
# Item 3 — an unchanged id-less localized code cell is re-translated on rebuild
# ---------------------------------------------------------------------------


def test_item3_group_rebuild_retranslates_unchanged_localized_code(tmp_path: Path):
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
    # TODAY: the unchanged localized code cell is re-translated as a bystander of
    # the sibling-triggered group rebuild.
    retranslated = [body for (role, _sl, _tl, body) in translator.calls if "Kommentar" in body]
    assert retranslated, (
        f"expected the unchanged localized code to be re-translated; got {translator.calls}"
    )
    # (Phase 2 splices it verbatim by anchor+hash; assert `retranslated == []` then.)
