"""Unit tests for code-cell + auxiliary-markdown sync (Issue #166, Phase 6).

The reported bug: ``clm slides sync`` only ever classified / propagated narrative
markdown (``slide`` / ``subslide`` / ``voiceover`` / ``notes``). Code cells and
untagged / ``alt`` markdown were invisible end to end. These tests demonstrate
each individual failure on small split-deck inputs and pin the fix:

- a **localized id'd** code cell (``lang=`` + ``slide_id``) is reconciled per
  cell — translated, never run through the markdown judge;
- an **id-carrying add** (a new id'd cell present on one side only) is
  translated and inserted under the same id;
- a **language-neutral** code cell (no ``lang``) is propagated verbatim;
- an **id-less localized** code cell is translated;
- **auxiliary markdown** (untagged or ``alt``, carrying a slide_id) syncs too;
- a code cell **moved between slide groups** follows;
- a **narrative-only** edit never churns a group's code, and a second run is a
  no-op.

All drive the engine with a seeded watermark + static judge/translator, so no
live LLM is touched. ``role_of`` (the predicate the whole engine keys off) is
tested directly too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import SyncWatermarkCache
from clm.infrastructure.llm.ollama_client import OllamaError, StaticSyncJudge, SyncProposal
from clm.notebooks.slide_parser import parse_cell_header, parse_cells
from clm.slides.sync_apply import apply_plan
from clm.slides.sync_plan import build_sync_plan, ordered_sync_cells
from clm.slides.sync_translate import StaticSlideTranslator
from clm.slides.sync_writeback import CODE_ROLE, role_of

# ---------------------------------------------------------------------------
# Cell builders
# ---------------------------------------------------------------------------


def _slide(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n{body}\n\n'


def _vo(lang: str, sid: str, body: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["voiceover"] slide_id="{sid}"\n{body}\n\n'


def _aux(lang: str, sid: str, body: str, *, tag: str | None = None) -> str:
    tags = f' tags=["{tag}"]' if tag else ""
    return f'# %% [markdown] lang="{lang}"{tags} slide_id="{sid}"\n{body}\n\n'


def _code_shared(body: str, *, sid: str | None = None) -> str:
    sid_attr = f' slide_id="{sid}"' if sid else ""
    return f'# %% tags=["keep"]{sid_attr}\n{body}\n\n'


def _code_loc(lang: str, body: str, *, sid: str | None = None) -> str:
    sid_attr = f' slide_id="{sid}"' if sid else ""
    return f'# %% lang="{lang}"{sid_attr}\n{body}\n\n'


def _write_pair(tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
    de_path = tmp_path / "deck.de.py"
    en_path = tmp_path / "deck.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")
    return de_path, en_path


def _seed_watermark(cache: SyncWatermarkCache, de_path: Path, en_path: Path) -> None:
    """Record the current on-disk decks as the last-synced baseline."""
    for lang, path in (("de", de_path), ("en", en_path)):
        cells = ordered_sync_cells(parse_cells(path.read_text(encoding="utf-8")), lang)
        cache.put_deck(
            de_path=str(de_path),
            en_path=str(en_path),
            lang=lang,
            cells=[(c.position, c.slide_id, c.role, c.content_hash, c.construct) for c in cells],
        )


class _DictTranslator:
    """Translator double: replace EN substrings with DE ones (protocol-shaped)."""

    prompt_version = "test"

    def __init__(self, replacements: dict[str, str]):
        self.replacements = replacements
        self.calls: list[tuple[str, str]] = []  # (role, source_body)

    def translate(self, *, source_body: str, source_lang: str, target_lang: str, role: str) -> str:
        self.calls.append((role, source_body))
        out = source_body
        for en, de in self.replacements.items():
            out = out.replace(en, de)
        return out


class _ExplodingJudge:
    """A judge that fails if ever asked — proves the judge was NOT used."""

    prompt_version = "test"

    def propose(self, *_args, **_kwargs):
        raise AssertionError("the markdown judge must not be called for a code cell")


def _bodies(path: Path, lang: str) -> dict[tuple[str | None, str], str]:
    """Map (slide_id, kind) -> raw cell body for assertions, kind ∈ code/markdown."""
    out: dict[tuple[str | None, str], str] = {}
    text = path.read_text(encoding="utf-8")
    # Re-split into raw cells to read code-cell bodies verbatim.
    from clm.slides.raw_cells import split_cells

    _, cells = split_cells(text)
    for c in cells:
        kind = "code" if c.metadata.cell_type == "code" else "markdown"
        out[(c.metadata.slide_id, kind)] = c.body
    return out


def _sync_keys(path: Path, lang: str) -> list[tuple[str | None, str]]:
    return [
        (c.slide_id, c.role) for c in ordered_sync_cells(parse_cells(path.read_text("utf-8")), lang)
    ]


def _apply(tmp_path, de, en, *, mutate_en=None, mutate_de=None, judge=None, translator=None):
    """Seed a baseline from (de, en), mutate one side, sync, return (de_path, en_path, result)."""
    de_path, en_path = _write_pair(tmp_path, de, en)
    cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
    try:
        _seed_watermark(cache, de_path, en_path)
        if mutate_en is not None:
            en_path.write_text(mutate_en, encoding="utf-8")
        if mutate_de is not None:
            de_path.write_text(mutate_de, encoding="utf-8")
        plan = build_sync_plan(de_path, en_path, watermark_cache=cache)
        result = apply_plan(plan, judge=judge, translator=translator, watermark_cache=cache)
    finally:
        cache.close()
    return de_path, en_path, result


# ---------------------------------------------------------------------------
# role_of predicate
# ---------------------------------------------------------------------------


class TestRoleOf:
    def _meta(self, header: str):
        return parse_cell_header(header)

    def test_localized_idd_code_is_role_code(self):
        assert role_of(self._meta('# %% lang="en" slide_id="x"')) == CODE_ROLE

    def test_shared_code_has_no_role(self):
        assert role_of(self._meta('# %% tags=["keep"]')) is None
        assert role_of(self._meta('# %% tags=["keep"] slide_id="x"')) is None  # no lang -> shared

    def test_idless_localized_code_has_no_role(self):
        assert role_of(self._meta('# %% lang="en"')) is None

    def test_untagged_markdown_with_id_is_role_markdown(self):
        assert role_of(self._meta('# %% [markdown] lang="en" slide_id="x"')) == "markdown"

    def test_alt_markdown_is_role_alt(self):
        assert role_of(self._meta('# %% [markdown] lang="en" tags=["alt"] slide_id="x"')) == "alt"

    def test_narrative_tag_wins(self):
        assert (
            role_of(self._meta('# %% [markdown] lang="en" tags=["slide"] slide_id="x"')) == "slide"
        )

    def test_markdown_without_id_or_role_is_none(self):
        assert role_of(self._meta('# %% [markdown] lang="en"')) is None


# ---------------------------------------------------------------------------
# Localized id'd code edit — translated, not judged
# ---------------------------------------------------------------------------


class TestLocalizedCodeEdit:
    def test_idd_code_edit_uses_translator_not_judge(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _code_loc("de", 'msg = "Hallo"', sid="setup")
        en = _slide("en", "s", "# ## Slide") + _code_loc("en", 'msg = "Hello"', sid="setup")
        # Author edits the EN code cell's string.
        en2 = _slide("en", "s", "# ## Slide") + _code_loc("en", 'msg = "Goodbye"', sid="setup")

        translator = _DictTranslator({'"Goodbye"': '"Auf Wiedersehen"'})
        de_path, en_path, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=_ExplodingJudge(), translator=translator
        )

        assert not result.has_errors, result.errors
        assert result.applied_edit == 1
        de_code = _bodies(de_path, "de")[("setup", "code")]
        assert 'msg = "Auf Wiedersehen"' in de_code  # translated, code structure kept
        assert ("code", 'msg = "Goodbye"') in translator.calls  # translator drove it


# ---------------------------------------------------------------------------
# id-carrying add of a localized code cell
# ---------------------------------------------------------------------------


class TestIdCarryingCodeAdd:
    def test_new_idd_code_cell_is_translated_and_twinned(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie")
        en = _slide("en", "s", "# ## Slide")
        # Author adds a new id'd localized code cell on EN.
        en2 = _slide("en", "s", "# ## Slide") + _code_loc("en", 'q = "What is a list?"', sid="demo")

        translator = _DictTranslator({'"What is a list?"': '"Was ist eine Liste?"'})
        de_path, en_path, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=StaticSyncJudge(), translator=translator
        )

        assert not result.has_errors, result.errors
        de_code = _bodies(de_path, "de").get(("demo", "code"))
        assert de_code is not None  # the twin was inserted under the same id
        assert 'q = "Was ist eine Liste?"' in de_code
        # Same id on both halves, role "code".
        assert ("demo", CODE_ROLE) in _sync_keys(de_path, "de")
        assert ("demo", CODE_ROLE) in _sync_keys(en_path, "en")


# ---------------------------------------------------------------------------
# Language-neutral code — propagated verbatim (no translation)
# ---------------------------------------------------------------------------


class TestSharedCode:
    def test_new_shared_code_cell_copied_verbatim(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _vo("de", "s", "# Sprechertext")
        en = _slide("en", "s", "# ## Slide") + _vo("en", "s", "# Narration")
        # EN gains a shared code cell AND edits the voiceover (establishes direction).
        en2 = (
            _slide("en", "s", "# ## Slide")
            + _code_shared("import os\nx = os.getcwd()")
            + _vo("en", "s", "# Narration extended")
        )

        translator = _DictTranslator({})
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(
                verdict="update", proposed_text="# Sprechertext erweitert"
            )
        )
        de_path, en_path, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=translator
        )

        assert not result.has_errors, result.errors
        de_text = de_path.read_text("utf-8")
        assert "import os\nx = os.getcwd()" in de_text  # verbatim, untranslated
        assert translator.calls == []  # a shared cell is never translated

    def test_edited_shared_code_propagates_verbatim(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _code_shared("VALUE = 1") + _vo("de", "s", "# T")
        en = _slide("en", "s", "# ## Slide") + _code_shared("VALUE = 1") + _vo("en", "s", "# N")
        en2 = (
            _slide("en", "s", "# ## Slide") + _code_shared("VALUE = 2") + _vo("en", "s", "# N more")
        )
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# T mehr")
        )
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=_DictTranslator({})
        )
        assert not result.has_errors, result.errors
        de_text = de_path.read_text("utf-8")
        assert "VALUE = 2" in de_text
        assert "VALUE = 1" not in de_text


# ---------------------------------------------------------------------------
# id-less localized code — translated
# ---------------------------------------------------------------------------


class TestIdlessLocalizedCode:
    def test_new_idless_localized_code_translated(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _vo("de", "s", "# T")
        en = _slide("en", "s", "# ## Slide") + _vo("en", "s", "# N")
        en2 = (
            _slide("en", "s", "# ## Slide")
            + _code_loc("en", 'run("a question")')  # id-less localized
            + _vo("en", "s", "# N+")
        )
        translator = _DictTranslator({'"a question"': '"eine Frage"'})
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# T+")
        )
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=translator
        )
        assert not result.has_errors, result.errors
        de_text = de_path.read_text("utf-8")
        assert 'run("eine Frage")' in de_text  # translated
        assert ("code", 'run("a question")') in translator.calls


# ---------------------------------------------------------------------------
# Auxiliary markdown (untagged / alt)
# ---------------------------------------------------------------------------


class TestAuxMarkdown:
    def test_new_untagged_markdown_with_id_is_added(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie")
        en = _slide("en", "s", "# ## Slide")
        en2 = _slide("en", "s", "# ## Slide") + _aux("en", "note", "# - a side note")
        translator = _DictTranslator({"a side note": "eine Randnotiz"})
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=StaticSyncJudge(), translator=translator
        )
        assert not result.has_errors, result.errors
        assert ("note", "markdown") in _sync_keys(de_path, "de")
        assert "eine Randnotiz" in de_path.read_text("utf-8")

    def test_alt_markdown_edit_is_reconciled_by_judge(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _aux("de", "s", "# Lösung alt", tag="alt")
        en = _slide("en", "s", "# ## Slide") + _aux("en", "s", "# Solution old", tag="alt")
        en2 = _slide("en", "s", "# ## Slide") + _aux("en", "s", "# Solution new", tag="alt")
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# Lösung neu")
        )
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=_DictTranslator({})
        )
        assert not result.has_errors, result.errors
        assert result.applied_edit == 1
        assert "Lösung neu" in de_path.read_text("utf-8")


# ---------------------------------------------------------------------------
# Cross-group code move
# ---------------------------------------------------------------------------


class TestCrossGroupCodeMove:
    def test_shared_code_moves_between_groups(self, tmp_path: Path):
        # SETUP shared code sits under slide A; the author moves it under slide B
        # (and edits both slide headings so direction is established per group).
        de = _slide("de", "a", "# ## A") + _code_shared("SETUP = 1") + _slide("de", "b", "# ## B")
        en = _slide("en", "a", "# ## A") + _code_shared("SETUP = 1") + _slide("en", "b", "# ## B")
        en2 = (
            _slide("en", "a", "# ## A2") + _slide("en", "b", "# ## B2") + _code_shared("SETUP = 1")
        )
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# (de)")
        )
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=_DictTranslator({})
        )
        assert not result.has_errors, result.errors
        # The DE deck must now have SETUP under group B, not group A.
        from clm.slides.raw_cells import split_cells

        _, cells = split_cells(de_path.read_text("utf-8"))
        order = [
            (c.metadata.slide_id, c.metadata.cell_type)
            for c in cells
            if c.metadata.is_slide_start or c.metadata.cell_type == "code"
        ]
        # slide a, slide b, then the code (moved under b).
        assert order == [("a", "markdown"), ("b", "markdown"), (None, "code")]
        assert de_path.read_text("utf-8").count("SETUP = 1") == 1  # not duplicated


# ---------------------------------------------------------------------------
# Narrative-only edit does not churn code; idempotent re-run
# ---------------------------------------------------------------------------


class TestNoChurn:
    def test_narrative_only_edit_leaves_code_untouched(self, tmp_path: Path):
        de = (
            _slide("de", "s", "# ## Folie")
            + _code_shared("KEEP = 1")
            + _code_loc("de", 'msg = "Hallo"', sid="m")
            + _vo("de", "s", "# T")
        )
        en = (
            _slide("en", "s", "# ## Slide")
            + _code_shared("KEEP = 1")
            + _code_loc("en", 'msg = "Hello"', sid="m")
            + _vo("en", "s", "# N")
        )
        # Only the EN voiceover changes — no code change anywhere.
        en2 = (
            _slide("en", "s", "# ## Slide")
            + _code_shared("KEEP = 1")
            + _code_loc("en", 'msg = "Hello"', sid="m")
            + _vo("en", "s", "# N updated")
        )
        before_de_code = _bodies(_write_pair(tmp_path, de, en)[0], "de")
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# T aktualisiert")
        )
        translator = _DictTranslator({"SHOULD-NOT": "BE-CALLED"})
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=translator
        )
        assert not result.has_errors, result.errors
        after = _bodies(de_path, "de")
        # The localized code twin was NOT re-translated, the shared code untouched.
        assert after[("m", "code")] == before_de_code[("m", "code")]
        assert translator.calls == []
        assert "KEEP = 1" in de_path.read_text("utf-8")

    def test_full_sync_then_rerun_is_noop(self, tmp_path: Path):
        de = _slide("de", "s", "# ## Folie") + _vo("de", "s", "# T")
        en = _slide("en", "s", "# ## Slide") + _vo("en", "s", "# N")
        en2 = (
            _slide("en", "s", "# ## Slide")
            + _code_shared("import os")
            + _code_loc("en", 'p = "x"', sid="c")
            + _vo("en", "s", "# N+")
        )
        translator = _DictTranslator({'"x"': '"y"'})
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# T+")
        )
        de_path, en_path = _write_pair(tmp_path, de, en)
        cache = SyncWatermarkCache(tmp_path / "clm-llm.sqlite")
        try:
            _seed_watermark(cache, de_path, en_path)
            en_path.write_text(en2, encoding="utf-8")
            plan1 = build_sync_plan(de_path, en_path, watermark_cache=cache)
            r1 = apply_plan(plan1, judge=judge, translator=translator, watermark_cache=cache)
            assert not r1.has_errors, r1.errors
            de_after_first = de_path.read_text("utf-8")
            # Second run: the watermark advanced; nothing left to do.
            plan2 = build_sync_plan(de_path, en_path, watermark_cache=cache)
            r2 = apply_plan(plan2, judge=judge, translator=translator, watermark_cache=cache)
        finally:
            cache.close()
        assert plan2.is_noop, [(p.kind, p.slide_id, p.role) for p in plan2.proposals]
        assert r2.applied == 0
        assert de_path.read_text("utf-8") == de_after_first  # byte-identical, no churn


# ---------------------------------------------------------------------------
# A failed translation during a structural rebuild must not drop a target cell
# ---------------------------------------------------------------------------


class TestRebuildTranslationFailure:
    def test_failed_translation_preserves_existing_target_cell(self, tmp_path: Path):
        # A group rebuild (triggered by a new shared cell) needs to re-translate
        # an id-less localized code cell. If the translator can't, the rebuild
        # must be ABORTED — the pre-existing target-deck cell is kept, never
        # silently dropped from disk — and the failure surfaced (watermark held).
        de = (
            _slide("de", "s", "# ## Folie")
            + _code_loc("de", 'frage("Wie viele?")')
            + _vo("de", "s", "# T")
        )
        en = (
            _slide("en", "s", "# ## Slide")
            + _code_loc("en", 'ask("How many?")')
            + _vo("en", "s", "# N")
        )
        # EN adds a NEW shared cell (forces the group's signature to drift -> a
        # rebuild) and edits the voiceover (establishes the en->de direction).
        en2 = (
            _slide("en", "s", "# ## Slide")
            + _code_shared("import os")
            + _code_loc("en", 'ask("How many?")')
            + _vo("en", "s", "# N more")
        )
        judge = StaticSyncJudge(
            default_proposal=SyncProposal(verdict="update", proposed_text="# T mehr")
        )
        # An empty StaticSlideTranslator raises TranslationError for every body.
        de_path, _en, result = _apply(
            tmp_path, de, en, mutate_en=en2, judge=judge, translator=StaticSlideTranslator()
        )

        de_text = de_path.read_text("utf-8")
        # The crucial invariant: the pre-existing DE code cell was NOT dropped.
        assert 'frage("Wie viele?")' in de_text
        # The failure is surfaced, not silent; the watermark is held.
        assert result.has_errors
        assert any("code-structure" in e for e in result.errors)
        assert result.watermark_recorded is False
        # The region was kept intact, so the un-translatable rebuild did not add
        # the new shared cell this pass (it re-attempts on the next run).
        assert "import os" not in de_text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
