"""Tests for ``clm slides coverage`` (Phase 4)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from clm.infrastructure.llm.cache import CoverageCache
from clm.infrastructure.llm.ollama_client import (
    BulletVerdict,
    CoverageVerdict,
    StaticCoverageJudge,
    coverage_key,
)
from clm.notebooks.slide_parser import parse_cells
from clm.slides.coverage import (
    CoverageOptions,
    build_coverage_pairs,
    check_coverage_for_text,
    check_coverage_in_file,
    extract_bullets,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _slide_text() -> str:
    return dedent(
        """\
        # %% [markdown] lang="de" tags=["slide"] slide_id="rag-overview"
        # ## RAG Übersicht
        #
        # - Was ist RAG
        # - Vektor-Datenbanken
        # - Embedding-Modelle

        # %% [markdown] lang="en" tags=["slide"] slide_id="rag-overview"
        # ## RAG Overview
        #
        # - What is RAG
        # - Vector databases
        # - Embedding models

        # %% [markdown] lang="de" tags=["voiceover"] slide_id="rag-overview"
        # RAG steht für Retrieval-Augmented Generation. Wir benutzen
        # Vektor-Datenbanken zur Speicherung und Embedding-Modelle zur
        # Indexierung.

        # %% [markdown] lang="en" tags=["voiceover"] slide_id="rag-overview"
        # RAG stands for Retrieval-Augmented Generation. We use vector
        # databases for storage and embedding models for indexing.
        """
    )


def _slide_text_with_gap_en() -> str:
    """Same as :func:`_slide_text` but the EN voiceover skips a bullet."""
    return dedent(
        """\
        # %% [markdown] lang="de" tags=["slide"] slide_id="rag-overview"
        # ## RAG Übersicht
        #
        # - Was ist RAG
        # - Vektor-Datenbanken
        # - Embedding-Modelle

        # %% [markdown] lang="en" tags=["slide"] slide_id="rag-overview"
        # ## RAG Overview
        #
        # - What is RAG
        # - Vector databases
        # - Embedding models

        # %% [markdown] lang="de" tags=["voiceover"] slide_id="rag-overview"
        # RAG steht für Retrieval-Augmented Generation. Wir benutzen
        # Vektor-Datenbanken zur Speicherung und Embedding-Modelle zur
        # Indexierung.

        # %% [markdown] lang="en" tags=["voiceover"] slide_id="rag-overview"
        # RAG stands for Retrieval-Augmented Generation. We use vector
        # databases for storage.
        """
    )


# ---------------------------------------------------------------------------
# extract_bullets
# ---------------------------------------------------------------------------


class TestExtractBullets:
    def test_returns_dash_bullets(self):
        content = "# ## Title\n#\n# - Alpha\n# - Beta\n# - Gamma"
        assert extract_bullets(content) == ["Alpha", "Beta", "Gamma"]

    def test_returns_star_bullets(self):
        assert extract_bullets("# * One\n# * Two") == ["One", "Two"]

    def test_returns_numbered_bullets(self):
        assert extract_bullets("# 1. First\n# 2. Second") == ["First", "Second"]

    def test_strips_emphasis(self):
        assert extract_bullets("# - **bold** text") == ["bold text"]

    def test_strips_inline_code(self):
        assert extract_bullets("# - use `clm` to build") == ["use clm to build"]

    def test_strips_links(self):
        assert extract_bullets("# - see [the docs](http://example.com)") == ["see the docs"]

    def test_empty_for_no_bullets(self):
        assert extract_bullets("# ## Heading only") == []

    def test_ignores_text_lines(self):
        content = "# ## Title\n# Some prose\n# - bullet\n# more prose"
        assert extract_bullets(content) == ["bullet"]


# ---------------------------------------------------------------------------
# build_coverage_pairs
# ---------------------------------------------------------------------------


class TestBuildCoveragePairs:
    def test_paired_de_en_emits_two_pairs(self):
        cells = parse_cells(_slide_text())
        pairs = build_coverage_pairs(cells)
        langs = sorted(p.lang for p in pairs)
        assert langs == ["de", "en"]
        for p in pairs:
            assert p.slide_id == "rag-overview"
            assert len(p.narrative_cells) == 1
            assert p.narrative_cells[0].metadata.lang == p.lang

    def test_solo_slide_emits_one_pair(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="solo"
            # ## Solo Slide
            #
            # - One

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="solo"
            # Voiceover content here.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        assert len(pairs) == 1
        assert pairs[0].lang == "en"
        assert pairs[0].slide_id == "solo"

    def test_second_slide_closes_first_pair(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="alpha"
            # ## Alpha
            #
            # - A1

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="alpha"
            # Alpha voiceover.

            # %% [markdown] lang="en" tags=["slide"] slide_id="beta"
            # ## Beta
            #
            # - B1

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="beta"
            # Beta voiceover.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        slide_ids = [p.slide_id for p in pairs]
        assert slide_ids == ["alpha", "beta"]
        for p in pairs:
            assert len(p.narrative_cells) == 1

    def test_intervening_code_cell_does_not_close_pair(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="x"
            # ## X
            #
            # - x1

            # %% tags=["keep"]
            print("hello")

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="x"
            # X voiceover.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        assert len(pairs) == 1
        assert len(pairs[0].narrative_cells) == 1

    def test_lang_less_slide_closes_all_pairs(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="x"
            # ## X
            #
            # - x1

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="x"
            # X voiceover.

            # %% [markdown] tags=["slide"]
            # ## Shared
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        # Only the EN pair; the shared (no lang) slide doesn't emit one.
        assert len(pairs) == 1
        assert pairs[0].lang == "en"

    def test_title_macro_anchors_following_voiceover(self):
        text = dedent(
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Titel", "Title") }}

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="title"
            # Welcome to the workshop.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        assert len(pairs) == 1
        assert pairs[0].slide_id == "title"
        assert pairs[0].lang == "en"

    def test_workshop_slide_is_skipped(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Intro
            #
            # - Intro bullet

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"
            # Intro voiceover covers the intro bullet.

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise-1"
            # ## Exercise 1
            #
            # - Do this
            # - Do that

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise-2"
            # ## Exercise 2
            #
            # - Another thing
            """
        )
        cells = parse_cells(text)
        skipped: list[int] = []
        pairs = build_coverage_pairs(cells, workshop_slide_count=skipped)
        assert [p.slide_id for p in pairs] == ["intro"]
        assert skipped == [2]  # exercise-1 and exercise-2

    def test_workshop_entry_closes_open_pair(self):
        # A regular slide opens a pair; the next cell is a workshop slide.
        # Before the workshop, the regular pair must be finalised so its
        # voiceover (already attached) is checked normally.
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Intro
            #
            # - Intro bullet

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"
            # Intro voiceover.

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise"
            # ## Exercise
            #
            # - Do something
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        assert len(pairs) == 1
        assert pairs[0].slide_id == "intro"
        assert len(pairs[0].narrative_cells) == 1

    def test_end_workshop_resumes_normal_pairing(self):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Intro
            #
            # - Intro bullet

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise"
            # ## Exercise
            #
            # - Do this

            # %% [markdown] lang="en" tags=["slide", "end-workshop"] slide_id="next-section"
            # ## Next Section
            #
            # - Next bullet

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="next-section"
            # Next-section voiceover.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        ids = [p.slide_id for p in pairs]
        assert ids == ["intro", "next-section"]
        next_section = next(p for p in pairs if p.slide_id == "next-section")
        assert len(next_section.narrative_cells) == 1

    def test_voiceover_inside_workshop_does_not_attach(self):
        # A voiceover cell inside a workshop scope must not slip back
        # onto the previous (non-workshop) pair.
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Intro
            #
            # - Intro bullet

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise"
            # ## Exercise
            #
            # - Do this

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="exercise"
            # This voiceover is inside the workshop and should be ignored.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        assert len(pairs) == 1
        assert pairs[0].slide_id == "intro"
        assert pairs[0].narrative_cells == []

    def test_title_anchor_clears_after_real_slide(self):
        # After a real slide appears, narrative cells with a new lang
        # must NOT spuriously attach to the title slide.
        text = dedent(
            """\
            # j2 from 'macros.j2' import header
            # {{ header("Titel", "Title") }}

            # %% [markdown] lang="en" tags=["slide"] slide_id="alpha"
            # ## Alpha
            #
            # - A

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="alpha"
            # Alpha voiceover.

            # %% [markdown] lang="de" tags=["voiceover"] slide_id="title"
            # Stray DE voiceover after the title slide.
            """
        )
        cells = parse_cells(text)
        pairs = build_coverage_pairs(cells)
        # Only the EN alpha pair — the stray DE voiceover is unpaired
        # because the title anchor was deactivated by the alpha slide.
        assert len(pairs) == 1
        assert pairs[0].slide_id == "alpha"
        assert pairs[0].lang == "en"


# ---------------------------------------------------------------------------
# check_coverage_for_text — happy path + gaps + caching
# ---------------------------------------------------------------------------


def _verdict(*bullets_and_covered: tuple[str, bool]) -> CoverageVerdict:
    bullets = tuple(BulletVerdict(text=t, covered=c) for t, c in bullets_and_covered)
    verdict = "covered" if all(c for _, c in bullets_and_covered) else "gaps"
    return CoverageVerdict(verdict=verdict, bullets=bullets)


class TestCheckCoverageForText:
    def test_clean_deck_reports_no_findings(self, tmp_path: Path):
        judge = StaticCoverageJudge(
            default_verdict=_verdict(("What is RAG", True), ("Vector databases", True))
        )
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
            assert result.findings == []
            assert result.pairs_checked == 2  # DE + EN
            assert result.llm_calls == 2
            assert result.cache_hits == 0
        finally:
            cache.close()

    def test_gap_reported_as_warning(self, tmp_path: Path):
        gap_verdict = _verdict(
            ("What is RAG", True),
            ("Vector databases", True),
            ("Embedding models", False),
        )
        clean_verdict = _verdict(
            ("Was ist RAG", True),
            ("Vektor-Datenbanken", True),
            ("Embedding-Modelle", True),
        )
        de_text = _narrative_text_de()
        en_text = _narrative_text_en_with_gap()
        judge = StaticCoverageJudge(
            {
                coverage_key(
                    ["Was ist RAG", "Vektor-Datenbanken", "Embedding-Modelle"],
                    de_text,
                    lang="de",
                ): clean_verdict,
                coverage_key(
                    ["What is RAG", "Vector databases", "Embedding models"],
                    en_text,
                    lang="en",
                ): gap_verdict,
            }
        )
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                _slide_text_with_gap_en(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
            assert len(result.findings) == 1
            f = result.findings[0]
            assert f.severity == "warning"
            assert f.lang == "en"
            assert f.slide_id == "rag-overview"
            assert "Embedding models" in f.uncovered_bullets
        finally:
            cache.close()

    def test_cache_hit_zero_llm_calls_on_rerun(self, tmp_path: Path):
        judge = StaticCoverageJudge(
            default_verdict=_verdict(
                ("Was ist RAG", True),
                ("Vektor-Datenbanken", True),
                ("Embedding-Modelle", True),
            )
        )
        cache_path = tmp_path / "clm-llm.sqlite"
        cache = CoverageCache(cache_path)
        try:
            first = check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
            assert first.llm_calls == 2
            assert first.cache_hits == 0
        finally:
            cache.close()

        # Brand-new judge so a stray call would be visible.
        judge2 = StaticCoverageJudge()
        cache2 = CoverageCache(cache_path)
        try:
            second = check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge2, cache=cache2),
            )
            assert second.llm_calls == 0
            assert second.cache_hits == 2
            assert judge2.calls == []
        finally:
            cache2.close()

    def test_single_bullet_edit_invalidates_only_that_pair(self, tmp_path: Path):
        judge = StaticCoverageJudge(default_verdict=_verdict(("Anything", True)))
        cache_path = tmp_path / "clm-llm.sqlite"
        cache = CoverageCache(cache_path)
        try:
            check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
            assert judge.calls and len(judge.calls) == 2
        finally:
            cache.close()

        # Edit only the EN slide cell — DE pair should still hit the cache.
        edited = _slide_text().replace("- Embedding models", "- Embedding model choice")
        judge2 = StaticCoverageJudge(default_verdict=_verdict(("Anything", True)))
        cache2 = CoverageCache(cache_path)
        try:
            result = check_coverage_for_text(
                edited, tmp_path / "deck.py", CoverageOptions(judge=judge2, cache=cache2)
            )
            assert result.llm_calls == 1
            assert result.cache_hits == 1
            # Only the EN pair re-checked.
            assert len(judge2.calls) == 1
            assert judge2.calls[0][2] == "en"
        finally:
            cache2.close()

    def test_no_voiceover_for_bullets_reports_finding_without_llm(self, tmp_path: Path):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="x"
            # ## X
            #
            # - Bullet one
            # - Bullet two
            """
        )
        judge = StaticCoverageJudge()
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                text, tmp_path / "deck.py", CoverageOptions(judge=judge, cache=cache)
            )
            assert len(result.findings) == 1
            f = result.findings[0]
            assert "no voiceover" in f.message
            assert f.uncovered_bullets == ("Bullet one", "Bullet two")
            assert result.llm_calls == 0
            assert judge.calls == []
        finally:
            cache.close()

    def test_slide_without_bullets_is_skipped_silently(self, tmp_path: Path):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="heading-only"
            # ## Heading Only

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="heading-only"
            # No bullets to cover here.
            """
        )
        judge = StaticCoverageJudge()
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                text, tmp_path / "deck.py", CoverageOptions(judge=judge, cache=cache)
            )
            assert result.findings == []
            assert result.pairs_total == 1
            assert result.pairs_checked == 0
            assert judge.calls == []
        finally:
            cache.close()

    def test_judge_failure_is_skipped_not_fatal(self, tmp_path: Path):
        judge = StaticCoverageJudge()  # raises on every call
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
            assert result.findings == []
            assert result.pairs_skipped == 2
            assert result.llm_calls == 0
        finally:
            cache.close()

    def test_judge_none_runs_in_cache_only_mode(self, tmp_path: Path):
        cache_path = tmp_path / "clm-llm.sqlite"
        judge = StaticCoverageJudge(default_verdict=_verdict(("x", True)))
        cache = CoverageCache(cache_path)
        try:
            check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache),
            )
        finally:
            cache.close()

        cache2 = CoverageCache(cache_path)
        try:
            result = check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=None, cache=cache2),
            )
            assert result.cache_hits == 2
            assert result.llm_calls == 0
            assert result.pairs_skipped == 0
        finally:
            cache2.close()

    def test_report_only_skips_cache_writes(self, tmp_path: Path):
        judge = StaticCoverageJudge(default_verdict=_verdict(("x", True)))
        cache_path = tmp_path / "clm-llm.sqlite"
        cache = CoverageCache(cache_path)
        try:
            check_coverage_for_text(
                _slide_text(),
                tmp_path / "deck.py",
                CoverageOptions(judge=judge, cache=cache, report_only=True),
            )
            assert cache.iter_entries() == []
        finally:
            cache.close()

    def test_workshop_slides_excluded_end_to_end(self, tmp_path: Path):
        text = dedent(
            """\
            # %% [markdown] lang="en" tags=["slide"] slide_id="intro"
            # ## Intro
            #
            # - Intro bullet

            # %% [markdown] lang="en" tags=["voiceover"] slide_id="intro"
            # Intro voiceover covers the intro bullet.

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise-1"
            # ## Exercise 1
            #
            # - Do this
            # - Do that

            # %% [markdown] lang="en" tags=["slide", "workshop"] slide_id="exercise-2"
            # ## Exercise 2
            #
            # - Another thing
            """
        )
        judge = StaticCoverageJudge(default_verdict=_verdict(("Intro bullet", True)))
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_for_text(
                text, tmp_path / "deck.py", CoverageOptions(judge=judge, cache=cache)
            )
            assert result.pairs_total == 1  # intro only
            assert result.pairs_in_workshop == 2  # both exercises
            assert result.findings == []
            assert judge.calls == [(("Intro bullet",), judge.calls[0][1], "en")]
        finally:
            cache.close()

    def test_check_coverage_in_file(self, tmp_path: Path):
        deck = tmp_path / "deck.py"
        deck.write_text(_slide_text(), encoding="utf-8")
        judge = StaticCoverageJudge(default_verdict=_verdict(("x", True)))
        cache = CoverageCache(tmp_path / "clm-llm.sqlite")
        try:
            result = check_coverage_in_file(deck, CoverageOptions(judge=judge, cache=cache))
            assert result.files_visited == 1
            assert result.pairs_checked == 2
        finally:
            cache.close()


# ---------------------------------------------------------------------------
# Helpers mirroring the production extraction logic so tests can build
# deterministic keys for StaticCoverageJudge mappings.
# ---------------------------------------------------------------------------


def _narrative_text_de() -> str:
    """Whatever _narrative_text() would return for the DE voiceover."""
    from clm.notebooks.slide_parser import parse_cells
    from clm.slides.coverage import _narrative_text

    cells = parse_cells(_slide_text())
    de_voiceover = [c for c in cells if c.metadata.is_narrative and c.metadata.lang == "de"]
    return _narrative_text(de_voiceover)


def _narrative_text_en_with_gap() -> str:
    from clm.notebooks.slide_parser import parse_cells
    from clm.slides.coverage import _narrative_text

    cells = parse_cells(_slide_text_with_gap_en())
    en_voiceover = [c for c in cells if c.metadata.is_narrative and c.metadata.lang == "en"]
    return _narrative_text(en_voiceover)


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCoverageCli:
    def test_dump_empty_cache(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.slides.coverage import coverage_cmd

        runner = CliRunner()
        result = runner.invoke(coverage_cmd, ["--dump", "--cache-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no cached verdicts" in result.output

    def test_dump_json_empty_cache(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.slides.coverage import coverage_cmd

        runner = CliRunner()
        result = runner.invoke(coverage_cmd, ["--dump", "--json", "--cache-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert result.output.strip() == "[]"

    def test_runs_against_file_without_ollama(self, tmp_path: Path):
        """With no Ollama daemon reachable, the command should still complete.

        Pairs that lack voiceover surface as warnings (no LLM needed);
        pairs that need a verdict get skipped silently.
        """
        from click.testing import CliRunner

        from clm.cli.commands.slides.coverage import coverage_cmd

        deck = tmp_path / "deck.py"
        deck.write_text(
            dedent(
                """\
                # %% [markdown] lang="en" tags=["slide"] slide_id="x"
                # ## X
                #
                # - Bullet without voiceover
                """
            ),
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            coverage_cmd,
            [
                str(deck),
                "--cache-dir",
                str(tmp_path / "cache"),
                "--ollama-url",
                "http://127.0.0.1:1",  # nothing listens here
            ],
        )
        # Exit 1 because the "no voiceover" finding counts as a warning.
        assert result.exit_code == 1
        assert "no voiceover" in result.output

    def test_requires_path_unless_dump(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.slides.coverage import coverage_cmd

        runner = CliRunner()
        result = runner.invoke(coverage_cmd, ["--cache-dir", str(tmp_path)])
        assert result.exit_code != 0
        assert "PATH is required" in result.output
