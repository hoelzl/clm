"""Tests for :mod:`clm.slides.assign_ids`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.ollama_client import StaticTitleSuggester
from clm.slides.assign_ids import (
    AssignOptions,
    assign_ids_for_text,
    assign_ids_in_file,
)


def _write(tmp_path: Path, content: str, name: str = "slide.py") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _run(text: str, **kwargs):
    options = AssignOptions(**kwargs)
    return assign_ids_for_text(text, Path("slide.py"), options)


# ---------------------------------------------------------------------------
# Category: HEADED — slug from first heading
# ---------------------------------------------------------------------------


class TestHeadedSlides:
    def test_assigns_from_heading(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "#\n"
            "# ## Wozu eine neue Bibliothek?\n"
            "#\n"
            "# - first bullet\n"
        )
        new_text, result = _run(text)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "wozu-eine-neue-bibliothek"
        assert result.assignments[0].source == "heading"
        assert 'slide_id="wozu-eine-neue-bibliothek"' in new_text

    def test_idempotent_with_existing_id(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="my-slide"\n'
            "# ## Wozu eine neue Bibliothek?\n"
        )
        new_text, result = _run(text)
        assert result.assignments == []
        assert new_text == text

    def test_force_overwrites(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="old-id"\n'
            "# ## Wozu eine neue Bibliothek?\n"
        )
        new_text, result = _run(text, force=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "wozu-eine-neue-bibliothek"
        assert 'slide_id="wozu-eine-neue-bibliothek"' in new_text
        assert 'slide_id="old-id"' not in new_text

    def test_paired_de_en_share_slug(self):
        # §2.3 EN-derived: paired DE/EN cells get the SAME slug, derived
        # from the EN heading.
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## DE Heading\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## EN Heading\n"
        )
        new_text, result = _run(text)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["en-heading", "en-heading"]

    def test_collision_between_unpaired_groups(self):
        # Two solo DE slides with identical headings — bumps to "-2".
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Title\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Title\n"
        )
        new_text, result = _run(text)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["title", "title-2"]


# ---------------------------------------------------------------------------
# Category: EXTRACTABLE — default refusal, opt-in acceptance
# ---------------------------------------------------------------------------


class TestExtractableSlides:
    BULLET_SLIDE = (
        '# %% [markdown] lang="en" tags=["slide"]\n'
        "#\n"
        "# - First bullet about LangChain\n"
        "# - Second bullet\n"
    )

    def test_default_refuses_softly(self):
        new_text, result = _run(self.BULLET_SLIDE)
        assert result.assignments == []
        assert len(result.refusals) == 1
        refusal = result.refusals[0]
        assert refusal.severity == "soft"
        assert refusal.proposed_slug == "first-bullet-about-langchain"
        assert new_text == self.BULLET_SLIDE

    def test_accept_content_derived_writes(self):
        new_text, result = _run(self.BULLET_SLIDE, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "first-bullet-about-langchain"
        assert result.assignments[0].source.startswith("content:")
        assert 'slide_id="first-bullet-about-langchain"' in new_text

    def test_bold_line_extraction(self):
        text = (
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "#\n"
            "# **A Prominent Bold Line**\n"
            "#\n"
            "# more content\n"
        )
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "a-prominent-bold-line"

    def test_img_alt_extraction(self):
        text = (
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "#\n"
            '# <img src="x.png" alt="RAG architecture diagram"/>\n'
        )
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "rag-architecture-diagram"

    def test_force_with_matching_existing_id_is_silent_no_op(self):
        # Regression: a cell whose existing id already equals the
        # content-derived proposal is a no-op even without
        # --accept-content-derived. Previously this combination produced
        # a spurious soft refusal that misled the author into thinking
        # the flag was being ignored.
        text = (
            '# %% [markdown] lang="en" tags=["slide"] slide_id="first-bullet-about-langchain"\n'
            "#\n"
            "# - First bullet about LangChain\n"
            "# - Second bullet\n"
        )
        new_text, result = _run(text, force=True)
        assert result.assignments == []
        assert result.refusals == []
        assert new_text == text

    def test_force_with_matching_existing_id_on_paired_cells(self):
        # Same regression in the DE/EN-pair case: when both siblings
        # carry the EN-derived slug already, the run is a clean no-op.
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="first-bullet-about-langchain"\n'
            "#\n"
            "# - Erster Punkt zu LangChain\n"
            '# %% [markdown] lang="en" tags=["slide"] slide_id="first-bullet-about-langchain"\n'
            "#\n"
            "# - First bullet about LangChain\n"
        )
        new_text, result = _run(text, force=True)
        assert result.assignments == []
        assert result.refusals == []
        assert new_text == text


# ---------------------------------------------------------------------------
# Category: NON_EXTRACTABLE — hard refuse
# ---------------------------------------------------------------------------


class TestNonExtractableSlides:
    def test_empty_slide_hard_refuses(self):
        text = '# %% [markdown] lang="en" tags=["slide"]\n#\n'
        new_text, result = _run(text)
        assert result.assignments == []
        assert len(result.refusals) == 1
        assert result.refusals[0].severity == "hard"
        assert new_text == text

    def test_image_without_alt_hard_refuses(self):
        text = '# %% [markdown] lang="en" tags=["slide"]\n#\n# <img src="divider.png"/>\n'
        new_text, result = _run(text)
        assert result.refusals[0].severity == "hard"

    def test_force_preserves_existing_when_no_proposal(self):
        # Baseline rule from §2.3: --force does not remove an id we can't replace.
        text = '# %% [markdown] lang="en" tags=["slide"] slide_id="kept"\n#\n'
        new_text, result = _run(text, force=True)
        assert result.assignments == []
        # The id is left intact.
        assert 'slide_id="kept"' in new_text


# ---------------------------------------------------------------------------
# Preserve marker
# ---------------------------------------------------------------------------


class TestPreserveMarker:
    def test_force_does_not_touch_preserved(self):
        text = '# %% [markdown] lang="de" tags=["slide"] slide_id="!intro"\n# ## Some New Title\n'
        new_text, result = _run(text, force=True)
        assert result.assignments == []
        assert 'slide_id="!intro"' in new_text

    def test_preserve_locks_paired_sibling_to_bare_form(self):
        # The DE cell's preserve marker locks the group's bare slug to
        # "intro". The EN sibling joins the same group and adopts the
        # bare form (no collision — they're the same logical slide).
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="!intro"\n'
            "# ## Whatever\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## Intro\n"
        )
        new_text, result = _run(text)
        assert 'slide_id="!intro"' in new_text  # DE preserve marker stays
        assert 'lang="en" tags=["slide"] slide_id="intro"' in new_text

    def test_preserve_collides_with_unrelated_group(self):
        # An unrelated *solo* slide whose heading also slugs to "intro"
        # bumps to intro-2 because the preserved !intro is already taken.
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="!intro"\n'
            "# ## Doesn't matter\n"
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Intro\n"
        )
        new_text, result = _run(text)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["intro-2"]


# ---------------------------------------------------------------------------
# Title slide special case
# ---------------------------------------------------------------------------


class TestTitleSlide:
    def test_voiceover_after_header_inherits_title(self):
        text = (
            "# j2 from 'macros.j2' import header\n"
            '# {{ header("DE Title", "EN Title") }}\n'
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# - welcome\n"
        )
        new_text, result = _run(text)
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_id == "title"
        assert a.source == "voiceover-inherit"
        assert 'slide_id="title"' in new_text


# ---------------------------------------------------------------------------
# Voiceover / notes inheritance
# ---------------------------------------------------------------------------


class TestVoiceoverInheritance:
    def test_voiceover_inherits_preceding_slide_id(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## RAG Architecture\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# - voiceover content\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# - voiceover content en\n"
        )
        new_text, result = _run(text)
        slide_assign = next(a for a in result.assignments if a.source == "heading")
        narrative = [a for a in result.assignments if a.source == "voiceover-inherit"]
        assert slide_assign.slide_id == "rag-architecture"
        assert len(narrative) == 2
        assert all(a.slide_id == "rag-architecture" for a in narrative)

    def test_existing_voiceover_id_preserved_without_force(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## RAG\n"
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="old-cell-id"\n'
            "# - voiceover\n"
        )
        new_text, result = _run(text)
        # The slide cell gets its slug. The voiceover keeps its existing id.
        narrative = [a for a in result.assignments if a.source == "voiceover-inherit"]
        assert narrative == []
        assert 'slide_id="old-cell-id"' in new_text

    def test_force_rewrites_voiceover_to_match_slide(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## RAG\n"
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="old-cell-id"\n'
            "# - voiceover\n"
        )
        new_text, result = _run(text, force=True)
        narrative = [a for a in result.assignments if a.source == "voiceover-inherit"]
        assert len(narrative) == 1
        assert narrative[0].slide_id == "rag"
        assert 'slide_id="rag"' in new_text
        assert 'slide_id="old-cell-id"' not in new_text


# ---------------------------------------------------------------------------
# LLM suggestion path (mocked)
# ---------------------------------------------------------------------------


class TestLLMSuggest:
    BULLET_SLIDE = (
        '# %% [markdown] lang="en" tags=["slide"]\n'
        "#\n"
        "# - We use LangSmith for tracing\n"
        "# - It records every model call\n"
    )

    def test_llm_replaces_content_derived(self):
        # The static suggester returns a title regardless of input.
        suggester = StaticTitleSuggester(default="LangSmith Tracing Overview")
        new_text, result = _run(
            self.BULLET_SLIDE,
            llm_suggest=True,
            llm_suggester=suggester,
        )
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_id == "langsmith-tracing-overview"
        assert a.source == "llm"
        assert suggester.calls  # the suggester was actually consulted

    def test_llm_failure_falls_back_to_refusal(self):
        # No mapping, no default → suggester raises → soft refusal.
        suggester = StaticTitleSuggester()
        new_text, result = _run(
            self.BULLET_SLIDE,
            llm_suggest=True,
            llm_suggester=suggester,
        )
        assert result.assignments == []
        assert result.refusals[0].severity == "soft"

    def test_llm_caches_results(self):
        # Hand-rolled fake cache to verify the integration path. We don't
        # exercise the real SQLite class here — that has its own tests.
        class FakeCache:
            def __init__(self):
                self.store: dict[tuple, str] = {}

            def get(self, content_hash, prompt_version, lang):
                return self.store.get((content_hash, prompt_version, lang))

            def put(self, content_hash, prompt_version, suggested_title, lang):
                self.store[(content_hash, prompt_version, lang)] = suggested_title

        cache = FakeCache()
        suggester = StaticTitleSuggester(default="From LLM")
        _run(
            self.BULLET_SLIDE,
            llm_suggest=True,
            llm_suggester=suggester,
            llm_cache=cache,
        )
        assert len(suggester.calls) == 1
        assert len(cache.store) == 1

        # Second run: cache hit, suggester not called again.
        _run(
            self.BULLET_SLIDE,
            llm_suggest=True,
            llm_suggester=suggester,
            llm_cache=cache,
        )
        assert len(suggester.calls) == 1


# ---------------------------------------------------------------------------
# Round-trip / idempotency on whole files
# ---------------------------------------------------------------------------


class TestFileLevel:
    def test_report_only_writes_nothing(self, tmp_path: Path):
        text = '# %% [markdown] lang="de" tags=["slide"]\n# ## Heading\n'
        path = _write(tmp_path, text)
        result = assign_ids_in_file(path, AssignOptions(report_only=True))
        assert len(result.assignments) == 1
        assert path.read_text(encoding="utf-8") == text
        assert result.files_modified == 0

    def test_assign_then_rerun_is_idempotent(self, tmp_path: Path):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Heading\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## Heading\n"
        )
        path = _write(tmp_path, text)

        r1 = assign_ids_in_file(path, AssignOptions())
        assert r1.files_modified == 1
        after_first = path.read_text(encoding="utf-8")

        r2 = assign_ids_in_file(path, AssignOptions())
        assert r2.files_modified == 0
        assert path.read_text(encoding="utf-8") == after_first

    def test_force_is_stable(self, tmp_path: Path):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## RAG Architecture\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## RAG Architecture\n"
        )
        path = _write(tmp_path, text)
        assign_ids_in_file(path, AssignOptions(force=True))
        first = path.read_text(encoding="utf-8")
        assign_ids_in_file(path, AssignOptions(force=True))
        second = path.read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# Subslide / mixed-tag cells
# ---------------------------------------------------------------------------


class TestSubslide:
    def test_subslide_treated_as_slide_start(self):
        text = '# %% [markdown] lang="de" tags=["subslide"]\n# ## A Subslide Title\n'
        new_text, result = _run(text)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "a-subslide-title"


# ---------------------------------------------------------------------------
# Non-target cells should be skipped
# ---------------------------------------------------------------------------


class TestCodeCellSlideStart:
    """Code cells tagged ``slide``/``subslide`` route through the AST
    extractor when no markdown signal is present (Phase 2).
    """

    def test_import_block_produces_slug(self):
        text = (
            '# %% lang="en" tags=["subslide"]\nimport requests\nimport trafilatura\nimport ftfy\n'
        )
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        a = result.assignments[0]
        # slugify caps at 30 chars, dropping the trailing "ftfy" token
        # (`import-requests-trafilatura-ftfy` is 32 chars).
        assert a.slide_id == "import-requests-trafilatura"
        assert a.source == "content:code:import"

    def test_class_def_produces_slug(self):
        text = '# %% lang="en" tags=["slide"]\nclass HistoryChatbot(BaseChatbot):\n    pass\n'
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "class-historychatbot"
        assert result.assignments[0].source == "content:code:class"

    def test_assignment_produces_slug(self):
        text = '# %% lang="en" tags=["subslide"]\nresponse = client.chat.completions.create()\n'
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "response"
        assert result.assignments[0].source == "content:code:assign"

    def test_keep_tag_does_not_block_extraction(self):
        # ``keep`` only affects build output, not assign-ids; a
        # ``keep + subslide`` code cell still classifies as slide_start.
        text = '# %% lang="en" tags=["keep", "subslide"]\nimport requests\nimport trafilatura\n'
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "import-requests-trafilatura"

    def test_paired_de_en_code_cells_share_slug(self):
        text = (
            '# %% lang="de" tags=["subslide"]\n'
            "import requests\n"
            '# %% lang="en" tags=["subslide"]\n'
            "import requests\n"
        )
        new_text, result = _run(text, accept_content_derived=True)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["import-requests", "import-requests"]

    def test_unparsable_code_still_hard_refuses(self):
        text = '# %% lang="en" tags=["subslide"]\n!pip install requests\n'
        new_text, result = _run(text)
        assert result.refusals[0].severity == "hard"

    def test_default_still_soft_refuses_without_accept_flag(self):
        # Code extraction produces a soft refusal under EXTRACTABLE
        # semantics — author needs --accept-content-derived to write.
        text = '# %% lang="en" tags=["subslide"]\nimport requests\n'
        new_text, result = _run(text)
        assert result.assignments == []
        refusal = result.refusals[0]
        assert refusal.severity == "soft"
        assert refusal.proposed_slug == "import-requests"

    def test_comment_in_code_cell_extracted_as_prose(self):
        # A leading ``# Comment`` in a code cell qualifies as prose via
        # the Phase-1 extractor before the AST walker fires — that's
        # the desired ordering since human-written comments usually
        # describe intent better than the first AST node would.
        text = '# %% lang="en" tags=["subslide"]\n# Initialize the client\nclient = OpenAI()\n'
        new_text, result = _run(text, accept_content_derived=True)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "initialize-the-client"
        assert result.assignments[0].source == "content:prose"


class TestLLMSuggestOnHardRefusal:
    """Phase 4: ``--llm-suggest`` fires on NON_EXTRACTABLE cells as a
    last resort. Without this, the LLM would silently no-op on the
    entire hard-refusal set (the dominant pattern in real corpora).
    """

    HARD_REFUSAL_TEXT = '# %% [markdown] lang="en" tags=["slide"]\n#\n# <img src="x.png"/>\n'

    def test_llm_fires_on_hard_refusal(self):
        suggester = StaticTitleSuggester(default="RAG Architecture Diagram")
        new_text, result = _run(
            self.HARD_REFUSAL_TEXT,
            llm_suggest=True,
            llm_suggester=suggester,
        )
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_id == "rag-architecture-diagram"
        assert a.source == "llm"
        assert suggester.calls

    def test_llm_silent_on_hard_refusal_when_flag_off(self):
        # Without --llm-suggest, behavior on hard refusals is unchanged.
        suggester = StaticTitleSuggester(default="Would Be Used If Asked")
        new_text, result = _run(
            self.HARD_REFUSAL_TEXT,
            llm_suggester=suggester,
        )
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"
        assert not suggester.calls

    def test_llm_unavailable_falls_through_to_hard_refusal(self):
        # Suggester wired but no static default → raises → fail-soft to
        # the original hard refusal. Same shape as Ollama-down case.
        suggester = StaticTitleSuggester()
        new_text, result = _run(
            self.HARD_REFUSAL_TEXT,
            llm_suggest=True,
            llm_suggester=suggester,
        )
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"

    def test_llm_caches_hard_refusal_results(self):
        class FakeCache:
            def __init__(self):
                self.store: dict[tuple, str] = {}

            def get(self, content_hash, prompt_version, lang):
                return self.store.get((content_hash, prompt_version, lang))

            def put(self, content_hash, prompt_version, suggested_title, lang):
                self.store[(content_hash, prompt_version, lang)] = suggested_title

        cache = FakeCache()
        suggester = StaticTitleSuggester(default="Cached Title")
        _run(
            self.HARD_REFUSAL_TEXT,
            llm_suggest=True,
            llm_suggester=suggester,
            llm_cache=cache,
        )
        assert len(suggester.calls) == 1
        assert len(cache.store) == 1

        _run(
            self.HARD_REFUSAL_TEXT,
            llm_suggest=True,
            llm_suggester=suggester,
            llm_cache=cache,
        )
        assert len(suggester.calls) == 1

    def test_llm_fires_on_empty_code_cell_with_only_magic(self):
        # Bash-magic-only code cells are NON_EXTRACTABLE (the AST walker
        # can't parse them) — Phase 4 still gets a shot.
        text = '# %% lang="en" tags=["subslide"]\n!pip install transformers\n'
        suggester = StaticTitleSuggester(default="Install Transformers")
        new_text, result = _run(text, llm_suggest=True, llm_suggester=suggester)
        assert len(result.assignments) == 1
        assert result.assignments[0].slide_id == "install-transformers"
        assert result.assignments[0].source == "llm"


class TestSiblingAsymmetry:
    """When the EN slug source has nothing to slug from but the DE
    sibling does, Phase 3 falls back to the DE-derived slug rather
    than hard-refusing the pair.
    """

    def test_de_heading_with_empty_en_sibling(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Hallo Welt\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "#\n"
        )
        new_text, result = _run(text)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["hallo-welt", "hallo-welt"]
        # The label tracks that we fell back to the sibling.
        sources = {a.source for a in result.assignments}
        assert sources == {"sibling-heading", "paired"}

    def test_de_prose_with_empty_en_sibling(self):
        text = (
            '# %% [markdown] lang="de" tags=["subslide"]\n'
            "#\n"
            "# Erste Anfrage\n"
            '# %% [markdown] lang="en" tags=["subslide"]\n'
            "#\n"
        )
        new_text, result = _run(text, accept_content_derived=True)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["erste-anfrage", "erste-anfrage"]
        de_assignment = next(a for a in result.assignments if "sibling" in a.source)
        assert de_assignment.source == "content:sibling-prose"

    def test_de_code_with_empty_en_sibling(self):
        text = (
            '# %% lang="de" tags=["subslide"]\n'
            "import requests\n"
            '# %% lang="en" tags=["subslide"]\n'
            "#\n"
        )
        new_text, result = _run(text, accept_content_derived=True)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["import-requests", "import-requests"]

    def test_both_empty_still_hard_refuses(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "#\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "#\n"
        )
        new_text, result = _run(text)
        assert result.assignments == []
        # Both pair members refuse — DE hard (root cause), EN soft (mirrored).
        severities = sorted(r.severity for r in result.refusals)
        assert severities == ["hard", "soft"]

    def test_en_heading_unchanged_when_de_empty(self):
        # When the EN heading exists, no fallback is needed and the
        # source label stays "heading" (not "sibling-heading").
        text = (
            '# %% [markdown] lang="de" tags=["slide"]\n'
            "#\n"
            '# %% [markdown] lang="en" tags=["slide"]\n'
            "# ## Hello World\n"
        )
        new_text, result = _run(text)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["hello-world", "hello-world"]
        sources = sorted({a.source for a in result.assignments})
        assert sources == ["heading", "paired"]


class TestSkippedCells:
    def test_keep_cell_ignored(self):
        text = '# %% tags=["keep"]\nx = 1\n'
        new_text, result = _run(text)
        assert result.assignments == []
        assert result.refusals == []
        assert new_text == text

    def test_shared_no_lang_cell_ignored(self):
        text = "# %%\nimport langchain\n"
        new_text, result = _run(text)
        assert result.assignments == []
        assert result.refusals == []
        assert new_text == text
