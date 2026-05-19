"""Tests for :mod:`clm.slides.headingless`."""

from __future__ import annotations

from clm.slides.headingless import Category, cell_text_for_llm, classify, extract_heading


class TestExtractHeading:
    def test_returns_text(self):
        content = "#\n# ## My Heading\n#\n# - bullet\n"
        assert extract_heading(content) == "My Heading"

    def test_none_when_no_heading(self):
        content = "#\n# - just a bullet\n"
        assert extract_heading(content) is None

    def test_finds_first_heading(self):
        content = "# ## First\n# ## Second\n"
        assert extract_heading(content) == "First"

    def test_handles_subheadings(self):
        content = "# ### Sub Level\n"
        assert extract_heading(content) == "Sub Level"


class TestClassify:
    def test_headed(self):
        content = "# ## Heading\n# - bullet\n"
        e = classify(content)
        assert e.category == Category.HEADED
        assert e.text == "Heading"
        assert e.source == "heading"

    def test_extractable_bullet(self):
        content = "#\n# - First bullet here\n"
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert e.source == "bullet"
        assert "First bullet here" in e.text

    def test_extractable_numbered(self):
        content = "#\n# 1. First step\n"
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert "First step" in e.text

    def test_extractable_bold(self):
        content = "#\n# **A Bold Line**\n"
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert e.source == "bold"
        assert e.text == "A Bold Line"

    def test_extractable_img_alt(self):
        content = '#\n# <img src="x" alt="A diagram"/>\n'
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert e.source == "img_alt"
        assert e.text == "A diagram"

    def test_bullet_beats_img_alt(self):
        content = '#\n# - the bullet\n# <img alt="ignored"/>\n'
        e = classify(content)
        assert e.source == "bullet"

    def test_non_extractable_empty(self):
        assert classify("").category == Category.NON_EXTRACTABLE

    def test_non_extractable_img_without_alt(self):
        content = '#\n# <img src="divider.png"/>\n'
        assert classify(content).category == Category.NON_EXTRACTABLE

    def test_non_extractable_only_whitespace(self):
        assert classify("#\n#  \n#\n").category == Category.NON_EXTRACTABLE


class TestProseLineExtraction:
    def test_simple_prose_line(self):
        content = "#\n# Test with two turns -- does the bot remember?\n"
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert e.source == "prose"
        assert e.text == "Test with two turns -- does the bot remember"

    def test_trailing_colon_stripped(self):
        content = "#\n# Loading the model:\n"
        e = classify(content)
        assert e.category == Category.EXTRACTABLE
        assert e.source == "prose"
        assert e.text == "Loading the model"

    def test_trailing_period_stripped(self):
        content = "#\n# Here is some prose.\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "Here is some prose"

    def test_german_prose_passes_through(self):
        content = "#\n# Test mit zwei Runden -- erinnert sich der Bot?\n"
        e = classify(content)
        assert e.source == "prose"
        # Slugification handles the umlaut transliteration downstream;
        # the extractor preserves the original text for the report.
        assert e.text == "Test mit zwei Runden -- erinnert sich der Bot"

    def test_long_prose_returned_intact(self):
        long_line = "A " + ("very " * 30).strip() + " long prose line"
        content = f"#\n# {long_line}\n"
        e = classify(content)
        assert e.source == "prose"
        assert "very very very" in e.text

    def test_pure_punctuation_refuses(self):
        content = "#\n# !!!\n"
        assert classify(content).category == Category.NON_EXTRACTABLE

    def test_skips_blank_lines_before_prose(self):
        content = "#\n#\n#\n# The actual prose\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "The actual prose"

    def test_skips_naked_img_then_prose(self):
        content = '#\n# <img src="divider.png"/>\n# Real prose here\n'
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "Real prose here"

    def test_naked_img_alone_refuses(self):
        content = '#\n# <img src="divider.png"/>\n'
        assert classify(content).category == Category.NON_EXTRACTABLE

    def test_strips_inline_italic(self):
        content = "#\n# *Some italic prose*\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "Some italic prose"

    def test_strips_inline_code(self):
        content = "#\n# Calling `client.chat()` first\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "Calling client.chat() first"

    def test_strips_link_keeping_label(self):
        content = "#\n# See [the docs](https://example.com) for details\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "See the docs for details"

    def test_prose_runs_after_other_extractors(self):
        # Bullet still wins even when prose precedes it on the page.
        content = "#\n# Some intro prose.\n# - first bullet\n"
        e = classify(content)
        assert e.source == "bullet"
        assert "first bullet" in e.text

    def test_bold_still_wins_over_prose(self):
        content = "#\n# Plain prose first.\n# **Bold heading-style line**\n"
        e = classify(content)
        assert e.source == "bold"
        assert e.text == "Bold heading-style line"

    def test_img_alt_still_wins_over_prose(self):
        content = '#\n# Plain prose first.\n# <img alt="The diagram"/>\n'
        e = classify(content)
        assert e.source == "img_alt"
        assert e.text == "The diagram"

    def test_code_lines_do_not_qualify_as_prose(self):
        # Bare Python statements in a code-cell body must not match —
        # they should fall through to NON_EXTRACTABLE so the Phase-2
        # code-cell extractor can pick them up.
        content = "import requests\nimport trafilatura\n"
        assert classify(content).category == Category.NON_EXTRACTABLE

    def test_code_cell_comment_qualifies_as_prose(self):
        # A '# Initialize the client' leading comment in a code cell is
        # still useful slug material — let the prose extractor pick it
        # up since the Phase-2 AST walk skips comments by design.
        content = "# Initialize the client\nclient = OpenAI()\n"
        e = classify(content)
        assert e.source == "prose"
        assert e.text == "Initialize the client"


class TestCellTextForLLM:
    def test_strips_comment_prefix(self):
        text = cell_text_for_llm("# Line one\n# Line two\n")
        assert text == "Line one\nLine two"

    def test_drops_blank_lines(self):
        text = cell_text_for_llm("# Line one\n#\n# Line two\n")
        assert text == "Line one\nLine two"

    def test_caps_length(self):
        long = "\n".join("# " + ("x" * 100) for _ in range(50))
        text = cell_text_for_llm(long, max_chars=200)
        assert len(text) < 300
        assert text.endswith("...")
