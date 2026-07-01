"""Tests for :mod:`clm.slides.assign_ids`."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.llm.ollama_client import StaticTitleSuggester
from clm.notebooks.slide_parser import parse_cells
from clm.slides.assign_ids import (
    AssignOptions,
    assign_ids_for_text,
    assign_ids_in_directory,
    assign_ids_in_file,
    assign_ids_in_split_pair,
)
from clm.slides.split import split_text


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

    def test_image_without_alt_soft_refuses_with_filename_proposal(self):
        # Used to hard-refuse; since #233 the filename stem is proposed as
        # a content-derived slug (accepted via --accept-content-derived).
        text = '# %% [markdown] lang="en" tags=["slide"]\n#\n# <img src="divider.png"/>\n'
        new_text, result = _run(text)
        assert result.refusals[0].severity == "soft"
        assert result.refusals[0].proposed_slug == "img-divider"

    def test_divider_cell_hard_refuses(self):
        text = '# %% [markdown] lang="en" tags=["slide"]\n#\n# ---\n'
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


class TestPlaceholderVoiceoverReinherit:
    """``<deck-stem>-cell-N`` placeholder ids are re-pointed without --force (#233)."""

    DECK = (
        '# %% [markdown] lang="de" tags=["slide"]\n'
        "# ## RAG Architecture\n"
        '# %% [markdown] lang="de" tags=["voiceover"] slide_id="{vo_id}"\n'
        "# - voiceover content\n"
    )

    def _run_at(self, vo_id: str, path: str, **kwargs):
        options = AssignOptions(**kwargs)
        return assign_ids_for_text(self.DECK.format(vo_id=vo_id), Path(path), options)

    def test_full_stem_placeholder_repointed(self):
        new_text, result = self._run_at(
            "slides_030v_simple_chatbot-cell-3", "slides_030v_simple_chatbot.de.py"
        )
        narrative = [a for a in result.assignments if a.source == "voiceover-reinherit"]
        assert len(narrative) == 1
        assert narrative[0].slide_id == "rag-architecture"
        assert 'slide_id="rag-architecture"' in new_text
        assert "cell-3" not in new_text

    def test_stem_suffix_placeholder_repointed(self):
        # Conversion tools stamped a *suffix* of the deck stem, e.g.
        # ``simple_chatbot-cell-1`` in deck slides_030v_simple_chatbot.
        new_text, result = self._run_at("simple_chatbot-cell-1", "slides_030v_simple_chatbot.de.py")
        narrative = [a for a in result.assignments if a.source == "voiceover-reinherit"]
        assert len(narrative) == 1
        assert 'slide_id="rag-architecture"' in new_text

    def test_unrelated_cell_n_id_kept(self):
        # ``…-cell-N`` whose prefix is NOT this deck's stem is left alone.
        new_text, result = self._run_at("other-deck-cell-2", "slides_030v_simple_chatbot.de.py")
        assert not [a for a in result.assignments if a.source == "voiceover-reinherit"]
        assert 'slide_id="other-deck-cell-2"' in new_text

    def test_legit_existing_id_kept(self):
        new_text, result = self._run_at("rag-overview", "slides_030v_simple_chatbot.de.py")
        assert not [a for a in result.assignments if "voiceover" in a.source]
        assert 'slide_id="rag-overview"' in new_text

    def test_preserved_placeholder_wins(self):
        new_text, result = self._run_at(
            "!slides_030v_simple_chatbot-cell-3", "slides_030v_simple_chatbot.de.py"
        )
        assert not [a for a in result.assignments if "voiceover" in a.source]
        assert 'slide_id="!slides_030v_simple_chatbot-cell-3"' in new_text

    def test_report_only_proposes_without_writing(self):
        new_text, result = self._run_at(
            "slides_030v_simple_chatbot-cell-3",
            "slides_030v_simple_chatbot.de.py",
            report_only=True,
        )
        narrative = [a for a in result.assignments if a.source == "voiceover-reinherit"]
        assert len(narrative) == 1
        assert 'slide_id="slides_030v_simple_chatbot-cell-3"' in new_text


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


class TestSplitTwinAware:
    """#162 defensive: per-file ``assign-ids`` on a split half adopts the twin's
    ``slide_id`` instead of minting a divergent one, keeping ``de_id == en_id``.
    """

    @staticmethod
    def _slide_ids(path: Path) -> list[str | None]:
        return [
            c.metadata.slide_id
            for c in parse_cells(path.read_text(encoding="utf-8"))
            if c.metadata.is_slide_start
        ]

    def _pair(self, tmp_path: Path, de: str, en: str) -> tuple[Path, Path]:
        return (
            _write(tmp_path, de, "slides_x.de.py"),
            _write(tmp_path, en, "slides_x.en.py"),
        )

    def test_idless_half_adopts_twin_id(self, tmp_path: Path):
        # EN already carries an id; the id-less DE adopts it rather than slugging
        # "mein-thema" from its own heading.
        de_path, _ = self._pair(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n',
            '# %% [markdown] lang="en" tags=["slide"] slide_id="my-topic"\n# ## My Topic\n',
        )
        result = assign_ids_in_file(de_path, AssignOptions())
        assert self._slide_ids(de_path) == ["my-topic"]
        assert any(a.source == "twin" for a in result.assignments)

    def test_born_split_reaches_parity(self, tmp_path: Path):
        # Both halves id-less with different headings. Assign DE then EN: the EN
        # run adopts the DE-minted id, so the halves end up in parity.
        de_path, en_path = self._pair(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n',
            '# %% [markdown] lang="en" tags=["slide"]\n# ## My Topic\n',
        )
        assign_ids_in_file(de_path, AssignOptions())
        assign_ids_in_file(en_path, AssignOptions())
        de_ids = self._slide_ids(de_path)
        assert de_ids == self._slide_ids(en_path)
        assert de_ids == ["mein-thema"]  # DE ran first, so its slug wins

    def test_count_mismatch_skips_reuse(self, tmp_path: Path):
        # Misaligned halves (DE has an extra slide): positional reuse is unsafe,
        # so DE mints normally and the divergence is left for the validator's
        # #162 detective.
        de_path, _ = self._pair(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Intro\n'
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Extra\n',
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n# ## Intro\n',
        )
        result = assign_ids_in_file(de_path, AssignOptions())
        assert all(a.source != "twin" for a in result.assignments)
        assert self._slide_ids(de_path) == ["intro", "extra"]

    def test_no_twin_mints_normally(self, tmp_path: Path):
        de_path = _write(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n',
            "slides_x.de.py",
        )
        result = assign_ids_in_file(de_path, AssignOptions())
        assert self._slide_ids(de_path) == ["mein-thema"]
        assert all(a.source != "twin" for a in result.assignments)

    def test_existing_divergent_id_not_touched_without_force(self, tmp_path: Path):
        # The defensive only fills id-less cells. A pre-existing divergent id is
        # left alone (the detective surfaces it); assign-ids must not silently
        # rewrite it under the no-force default.
        de_path, _ = self._pair(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"] slide_id="de-own"\n# ## Thema\n',
            '# %% [markdown] lang="en" tags=["slide"] slide_id="en-own"\n# ## Topic\n',
        )
        assign_ids_in_file(de_path, AssignOptions())
        assert self._slide_ids(de_path) == ["de-own"]


class TestSplitGenerative:
    """#162 generative: directory ``assign-ids`` mints **EN-authority** ids
    across both halves of a split pair at once — deterministic, order-independent
    (contrast the per-file defensive, which is first-assigned-wins)."""

    _HEADER = '# j2 from \'macros.j2\' import header\n# {{ header("DE", "EN") }}\n\n'

    def _born_split(
        self, tmp_path: Path, de_title: str = "Mein Thema", en_title: str = "My Topic"
    ) -> tuple[Path, Path]:
        # Build a valid id-less split pair by splitting an id-less bilingual deck
        # (so the header macros are exactly what `split` produces and `unify`
        # accepts). The two headings differ so EN-authority is observable.
        bilingual = (
            self._HEADER
            + f'# %% [markdown] lang="de" tags=["slide"]\n# ## {de_title}\n\n'
            + f'# %% [markdown] lang="en" tags=["slide"]\n# ## {en_title}\n\n'
        )
        de, en = split_text(bilingual)
        return (
            _write(tmp_path, de, "slides_x.de.py"),
            _write(tmp_path, en, "slides_x.en.py"),
        )

    @staticmethod
    def _ids(path: Path) -> list[str | None]:
        return [
            c.metadata.slide_id
            for c in parse_cells(path.read_text(encoding="utf-8"))
            if c.metadata.is_slide_start
        ]

    def _divergent_shared_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        # A pair whose *shared* (no-lang) cell differs between the halves. unify
        # raises on that, so the round-trip is not faithful -> generative bails.
        bilingual = (
            self._HEADER
            + '# %% [markdown] lang="de" tags=["slide"]\n# ## Mein Thema\n\n'
            + '# %% [markdown] lang="en" tags=["slide"]\n# ## My Topic\n\n'
            + '# %% tags=["keep"]\nx = 1\n\n'
        )
        de, en = split_text(bilingual)
        de = de.replace("x = 1", "x = 2")  # tamper the DE shared cell
        return (
            _write(tmp_path, de, "slides_x.de.py"),
            _write(tmp_path, en, "slides_x.en.py"),
        )

    def test_split_pair_function_mints_en_authority(self, tmp_path: Path):
        de_path, en_path = self._born_split(tmp_path)
        result = assign_ids_in_split_pair(de_path, en_path, AssignOptions())
        assert result is not None
        assert self._ids(de_path) == self._ids(en_path) == ["my-topic"]

    def test_directory_mints_en_authority(self, tmp_path: Path):
        de_path, en_path = self._born_split(tmp_path)
        assign_ids_in_directory(tmp_path, AssignOptions())
        # EN heading "My Topic" -> "my-topic" wins on BOTH halves, not the DE
        # "mein-thema" the per-file defensive would pick.
        assert self._ids(de_path) == self._ids(en_path) == ["my-topic"]

    def test_round_trippable_misalignment_is_not_corrupted(self, tmp_path: Path):
        # An *extra* DE slide round-trips faithfully through unify/split, so the
        # generative proceeds safely: the aligned slide gets the EN-authority id
        # on both halves, the DE-only slide gets its own id, and the inherent
        # divergence (extra only on DE) is left for the validator's detective.
        de_path, en_path = self._born_split(tmp_path)
        de_path.write_text(
            de_path.read_text(encoding="utf-8")
            + '# %% [markdown] lang="de" tags=["slide"]\n# ## Extra\n',
            encoding="utf-8",
        )
        result = assign_ids_in_split_pair(de_path, en_path, AssignOptions())
        assert result is not None
        assert self._ids(de_path) == ["my-topic", "extra"]
        assert self._ids(en_path) == ["my-topic"]

    def test_divergent_shared_cell_returns_none(self, tmp_path: Path):
        de_path, en_path = self._divergent_shared_pair(tmp_path)
        assert assign_ids_in_split_pair(de_path, en_path, AssignOptions()) is None

    def test_directory_falls_back_to_defensive_on_divergent_shared(self, tmp_path: Path):
        # The generative bails (not round-trippable); the per-file defensive
        # fallback still reaches slide_id parity on the aligned slide.
        de_path, en_path = self._divergent_shared_pair(tmp_path)
        assign_ids_in_directory(tmp_path, AssignOptions())
        assert self._ids(de_path) == self._ids(en_path) == ["mein-thema"]

    def test_report_only_writes_nothing(self, tmp_path: Path):
        de_path, en_path = self._born_split(tmp_path)
        before = (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8"))
        result = assign_ids_in_split_pair(de_path, en_path, AssignOptions(report_only=True))
        assert result is not None
        assert result.assignments  # proposals are still reported
        assert (de_path.read_text(encoding="utf-8"), en_path.read_text(encoding="utf-8")) == before


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


class TestCodeDerivedFallback:
    """``--accept-code-derived`` first-code-line fallback for bare-expression
    code cells (#251) — the cells that historically hard-refused with the LLM
    as the only non-manual escape.
    """

    BARE = '# %% lang="en" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n'

    def test_default_still_hard_refuses(self):
        # Unchanged from pre-#251: a bare expression hard-refuses by default.
        new_text, result = _run(self.BARE)
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"
        assert new_text == self.BARE

    def test_accept_code_derived_mints(self):
        new_text, result = _run(self.BARE, accept_code_derived=True)
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_id == "1-1j-1-1j"
        assert a.source == "code:line"
        assert 'slide_id="1-1j-1-1j"' in new_text

    def test_accept_content_derived_alone_does_not_mint(self):
        # Critical back-compat: course_gate / sync_apply / translate_bootstrap
        # pass accept_content_derived=True and must NOT start minting opaque
        # code-line slugs — that needs the separate accept_code_derived knob.
        new_text, result = _run(self.BARE, accept_content_derived=True)
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"
        assert new_text == self.BARE

    def test_magic_only_still_hard_refuses_with_flag(self):
        # The magic is skipped by the scanner, so nothing is extractable.
        text = '# %% lang="en" tags=["subslide"]\n!pip install requests\n'
        new_text, result = _run(text, accept_code_derived=True)
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"

    def test_punctuation_only_still_hard_refuses_with_flag(self):
        text = '# %% lang="en" tags=["subslide"]\n...\n'
        new_text, result = _run(text, accept_code_derived=True)
        assert result.assignments == []
        assert result.refusals[0].severity == "hard"

    def test_idempotent_rerun(self):
        new_text, _ = _run(self.BARE, accept_code_derived=True)
        new_text2, result2 = _run(new_text, accept_code_derived=True)
        assert result2.assignments == []
        assert new_text2 == new_text

    def test_collision_suffix_for_identical_code_lines(self):
        # Comparison expressions stay on the code:line path (#233 moved
        # subscript displays like letters[0:3] up to content:code:expr).
        text = (
            '# %% lang="en" tags=["subslide"]\na == b\n# %% lang="en" tags=["subslide"]\na == b\n'
        )
        new_text, result = _run(text, accept_code_derived=True)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["a-b", "a-b-2"]

    def test_subscript_display_is_content_derived(self):
        # letters[0:3] is intent-extracted since #233: content:code:expr,
        # gated by --accept-content-derived, with the clean base-name slug.
        text = '# %% lang="en" tags=["subslide"]\nletters[0:3]\n'
        new_text, result = _run(text, accept_content_derived=True)
        assert [a.slide_id for a in result.assignments] == ["letters"]
        assert result.assignments[0].source == "content:code:expr"

    def test_for_loop_is_content_derived(self):
        text = (
            '# %% lang="en" tags=["subslide"]\n'
            "for student in classroom:\n"
            "    print(evaluate_student(student))\n"
        )
        new_text, result = _run(text, accept_content_derived=True)
        assert [a.slide_id for a in result.assignments] == ["for-student-in-classroom"]
        assert result.assignments[0].source == "content:code:for"

    def test_preserve_marker_untouched(self):
        text = '# %% lang="en" tags=["subslide"] slide_id="!keep"\n(1 + 1j) * (1 + 1j)\n'
        new_text, result = _run(text, accept_code_derived=True, force=True)
        assert result.assignments == []
        assert 'slide_id="!keep"' in new_text

    def test_llm_wins_over_code_derived(self):
        suggester = StaticTitleSuggester(default="Complex Multiplication")
        new_text, result = _run(
            self.BARE,
            accept_code_derived=True,
            llm_suggest=True,
            llm_suggester=suggester,
        )
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.slide_id == "complex-multiplication"
        assert a.source == "llm"

    # -- pair-safety across all three id paths --

    def test_pair_safe_bilingual_single_file(self):
        text = (
            '# %% lang="de" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n'
            '# %% lang="en" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n'
        )
        new_text, result = _run(text, accept_code_derived=True)
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["1-1j-1-1j", "1-1j-1-1j"]
        assert {a.source for a in result.assignments} == {"code:line", "paired"}

    def test_pair_safe_split_pair_function(self, tmp_path: Path):
        de = _write(
            tmp_path,
            '# %% lang="de" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n',
            "deck.de.py",
        )
        en = _write(
            tmp_path,
            '# %% lang="en" tags=["subslide"]\n(1 + 1j) * (1 + 1j)\n',
            "deck.en.py",
        )
        assign_ids_in_split_pair(de, en, AssignOptions(accept_code_derived=True))
        assert 'slide_id="1-1j-1-1j"' in de.read_text(encoding="utf-8")
        assert 'slide_id="1-1j-1-1j"' in en.read_text(encoding="utf-8")

    def test_pair_safe_directory(self, tmp_path: Path):
        # find_slide_files only discovers slides_*/topic_*/project_* names.
        _write(tmp_path, '# %% lang="de" tags=["subslide"]\na == b\n', "slides_deck.de.py")
        _write(tmp_path, '# %% lang="en" tags=["subslide"]\na == b\n', "slides_deck.en.py")
        assign_ids_in_directory(tmp_path, AssignOptions(accept_code_derived=True))
        de_text = (tmp_path / "slides_deck.de.py").read_text(encoding="utf-8")
        en_text = (tmp_path / "slides_deck.en.py").read_text(encoding="utf-8")
        assert 'slide_id="a-b"' in de_text
        assert 'slide_id="a-b"' in en_text

    def test_pair_safe_per_file_twin(self, tmp_path: Path):
        # EN minted via code-derived, then the id-less DE half adopts the twin
        # id through the per-file twin path — code-derived ids stay in parity.
        de = _write(tmp_path, '# %% lang="de" tags=["subslide"]\na == b\n', "deck.de.py")
        en = _write(tmp_path, '# %% lang="en" tags=["subslide"]\na == b\n', "deck.en.py")
        assign_ids_in_file(en, AssignOptions(accept_code_derived=True))
        assign_ids_in_file(de, AssignOptions(accept_code_derived=True))
        import re

        de_id = re.search(r'slide_id="([^"]+)"', de.read_text(encoding="utf-8")).group(1)
        en_id = re.search(r'slide_id="([^"]+)"', en.read_text(encoding="utf-8")).group(1)
        assert de_id == en_id == "a-b"

    def test_non_python_pair_safe(self):
        # ast.parse can't parse C#; the comment-token-aware fallback completes
        # the deck and the EN-authority pair stays in slide_id parity.
        text = (
            '// %% lang="de" tags=["subslide"]\nvar z = (1 + 2) * (3 + 4);\n'
            '// %% lang="en" tags=["subslide"]\nvar z = (1 + 2) * (3 + 4);\n'
        )
        new_text, result = assign_ids_for_text(
            text, Path("deck.cs"), AssignOptions(accept_code_derived=True)
        )
        slugs = [a.slide_id for a in result.assignments]
        assert slugs == ["var-z-1-2-3-4", "var-z-1-2-3-4"]


class TestLLMSuggestOnHardRefusal:
    """Phase 4: ``--llm-suggest`` fires on NON_EXTRACTABLE cells as a
    last resort. Without this, the LLM would silently no-op on the
    entire hard-refusal set (the dominant pattern in real corpora).
    """

    # A divider-only cell: genuinely nothing to extract (an alt-less <img>
    # stopped being a hard refusal with the #233 filename-stem fallback).
    HARD_REFUSAL_TEXT = '# %% [markdown] lang="en" tags=["slide"]\n#\n# ---\n'

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


# ---------------------------------------------------------------------------
# Stamp mode (sync-v3 Phase 0, #520): localized + narrative id stamping
# ---------------------------------------------------------------------------

_STAMP = {"stamp_ids": True, "accept_content_derived": True, "accept_code_derived": True}

_STAMP_BILINGUAL = (
    '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
    "# ## Einführung\n"
    "\n"
    '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
    "# ## Introduction\n"
    "\n"
    '# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"\n'
    "# Willkommen zur Einführung in dieses Thema.\n"
    "\n"
    '# %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"\n'
    "# Welcome to the introduction of this topic.\n"
    "\n"
    '# %% [markdown] lang="de"\n'
    "# Ein lokalisierter Hinweis ohne Bezeichner.\n"
    "\n"
    '# %% [markdown] lang="en"\n'
    "# A localized note without an identifier.\n"
    "\n"
    '# %% tags=["keep"]\n'
    "x = 1\n"
)


class TestStampIds:
    def test_owner_inherited_narrative_repointed_to_own_id(self):
        new_text, result = _run(_STAMP_BILINGUAL, **_STAMP)
        narrative = [a for a in result.assignments if a.source == "narrative-repoint"]
        assert len(narrative) == 2  # both twins of the voiceover pair
        own = narrative[0].slide_id
        assert own != "intro"
        assert narrative[1].slide_id == own
        # The slide pair keeps its id untouched.
        assert new_text.count('slide_id="intro"') == 2

    def test_idless_localized_pair_shares_one_content_slug(self):
        new_text, result = _run(_STAMP_BILINGUAL, **_STAMP)
        localized = [
            a
            for a in result.assignments
            if a.source in ("content:prose", "paired") and "note" in a.slide_id
        ]
        assert len(localized) == 2
        assert localized[0].slide_id == localized[1].slide_id
        # EN-authority: the shared slug comes from the EN body.
        assert localized[0].slide_id.startswith("a-localized-note")

    def test_shared_and_j2_cells_never_stamped(self):
        new_text, result = _run(_STAMP_BILINGUAL, **_STAMP)
        # The neutral code cell is untouched: exactly 2 narrative re-points
        # plus 2 localized stamps.
        assert '# %% tags=["keep"]\nx = 1' in new_text
        assert len(result.assignments) == 4

    def test_idempotent_second_run(self):
        first, result1 = _run(_STAMP_BILINGUAL, **_STAMP)
        assert result1.assignments
        second, result2 = _run(first, **_STAMP)
        assert result2.assignments == []
        assert result2.refusals == []
        assert second == first

    def test_stamp_off_keeps_legacy_behavior(self):
        new_text, result = _run(_STAMP_BILINGUAL, accept_content_derived=True)
        # Without stamp_ids the voiceover keeps the inherited owner id and
        # localized content cells stay id-less.
        assert new_text == _STAMP_BILINGUAL
        assert result.assignments == []

    def test_own_id_narrative_kept(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="my-own-story"\n'
            "# Sprechertext\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"] slide_id="my-own-story"\n'
            "# Voiceover text\n"
        )
        new_text, result = _run(text, **_STAMP)
        assert result.assignments == []
        assert new_text == text

    def test_preserved_narrative_id_wins_and_twin_adopts(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="!keep-me"\n'
            "# Sprechertext\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# Voiceover text\n"
        )
        new_text, result = _run(text, **_STAMP)
        assert 'slide_id="!keep-me"' in new_text  # marker untouched
        # The id-less EN twin adopts the preserved bare form (resolved via
        # the pair cache, hence "paired" — the same label the slide
        # machinery uses for a sibling applying a cached resolution).
        assert len(result.assignments) == 1
        adopted = result.assignments[0]
        assert adopted.slide_id == "keep-me"
        assert adopted.source == "paired"

    def test_solo_localized_cell_refused_not_half_stamped(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# Ein Hinweis nur auf Deutsch.\n"
        )
        new_text, result = _run(text, **_STAMP)
        assert result.assignments == []
        assert len(result.refusals) == 1
        assert "no directly-adjacent DE/EN twin" in result.refusals[0].reason
        assert new_text == text

    def test_narrative_without_anchor_refused(self):
        text = (
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# Sprechertext ohne Slide davor\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# Voiceover without a preceding slide\n"
        )
        new_text, result = _run(text, **_STAMP)
        assert result.assignments == []
        assert all("no preceding slide" in r.reason for r in result.refusals)
        assert new_text == text

    def test_collision_gets_suffix(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="a"\n'
            "# ## A\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# Der gleiche Text.\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# The same narration text.\n"
            "\n"
            '# %% [markdown] lang="de" tags=["subslide"] slide_id="b"\n'
            "# ## B\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# Der gleiche Text nochmal.\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"]\n'
            "# The same narration text.\n"
        )
        new_text, result = _run(text, **_STAMP)
        slugs = sorted({a.slide_id for a in result.assignments})
        assert len(slugs) == 2
        assert slugs[1] == f"{slugs[0]}-2"

    def test_content_not_accepted_refuses_with_proposal(self):
        new_text, result = _run(_STAMP_BILINGUAL, stamp_ids=True)
        # Without the accept knobs the prose-derived stamps refuse softly,
        # carrying the proposed slug for the worklist.
        soft = [r for r in result.refusals if r.severity == "soft" and r.proposed_slug]
        assert soft
        assert result.assignments == []

    def test_localized_code_pair_stamped_via_code_extractor(self):
        text = (
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% lang="de"\n'
            "zahl = 1\n"
            "\n"
            '# %% lang="en"\n'
            "number = 1\n"
        )
        new_text, result = _run(text, **_STAMP)
        assert [a.slide_id for a in result.assignments] == ["number", "number"]

    def test_split_pair_stamped_identically(self, tmp_path):
        from clm.slides.assign_ids import assign_ids_in_files

        de = _write(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"] slide_id="intro"\n'
            "# Willkommen zur Einführung in dieses Thema.\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# Ein lokalisierter Hinweis ohne Bezeichner.\n",
            name="slides_stamp.de.py",
        )
        en = _write(
            tmp_path,
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
            "# ## Introduction\n"
            "\n"
            '# %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"\n'
            "# Welcome to the introduction of this topic.\n"
            "\n"
            '# %% [markdown] lang="en"\n'
            "# A localized note without an identifier.\n",
            name="slides_stamp.en.py",
        )

        result = assign_ids_in_files([de, en], AssignOptions(**_STAMP))
        assert result.files_modified == 2
        assert not result.refusals

        def ids_of(path: Path) -> list[str]:
            cells = parse_cells(path.read_text(encoding="utf-8"), "#")
            return [c.metadata.slide_id for c in cells if c.metadata.slide_id]

        de_ids = ids_of(de)
        en_ids = ids_of(en)
        assert de_ids == en_ids  # identical ids, identical order
        assert len(de_ids) == 3
        assert len(set(de_ids)) == 3  # the narrative no longer shares "intro"

    def test_split_half_alone_downgraded_with_refusal(self, tmp_path):
        de = _write(
            tmp_path,
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de"\n'
            "# Ein Hinweis.\n",
            name="slides_solo.de.py",
        )
        result = assign_ids_in_file(de, AssignOptions(**_STAMP))
        assert result.assignments == []
        assert any("--stamp-ids needs both halves" in r.reason for r in result.refusals)
        # The localized cell was NOT half-stamped.
        assert "slide_id" not in de.read_text(encoding="utf-8").splitlines()[3]
