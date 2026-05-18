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
