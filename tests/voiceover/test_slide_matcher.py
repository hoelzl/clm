"""Tests for slide matching between two slide-file revisions."""

from __future__ import annotations

from clm.notebooks.slide_parser import group_slides, parse_cells
from clm.voiceover.slide_matcher import MatchKind, content_similarity, match_slides


def _parse(text: str, lang: str = "de"):
    return group_slides(parse_cells(text), lang, include_header=False)


SOURCE_TEXT = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
#
# ## Einführung
# Willkommen zum Kurs.

# %% [markdown] lang="de" tags=["voiceover"]
#
# - Begruessung und Ueberblick.
# - Was euch heute erwartet.

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
#
# ## REST vs SOAP
# REST ist der moderne Ansatz.

# %% [markdown] lang="de" tags=["voiceover"]
#
# - REST ist leichtgewichtig.
# - SOAP ist heute eher Legacy.

# %% [markdown] lang="de" tags=["slide"] slide_id="gone"
#
# ## Ein altes Thema
# Dieser Abschnitt wurde entfernt.
"""

TARGET_TEXT_UNCHANGED = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
#
# ## Einführung
# Willkommen zum Kurs.

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
#
# ## REST vs SOAP
# REST ist der moderne Ansatz.

# %% [markdown] lang="de" tags=["slide"] slide_id="new-topic"
#
# ## Neues Thema
# Ein ganz neuer Abschnitt.
"""

TARGET_TEXT_MODIFIED = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
#
# ## Einführung
# Willkommen zum Kurs. Heute: Web APIs im Detail.
# Zusaetzlich eine ganz neue Einleitung mit viel mehr Text, damit
# die Content-Aehnlichkeit deutlich unter 90 Prozent faellt.
# Noch mehr Text um sicherzustellen, dass sich der Content
# substantiell unterscheidet. Noch ein ganzer Absatz Text.

# %% [markdown] lang="de" tags=["slide"] slide_id="rest-vs-soap"
#
# ## REST vs SOAP
# REST ist der moderne Ansatz.
"""


class TestSlideIdMatching:
    def test_exact_id_match_with_unchanged_content(self):
        source = _parse(SOURCE_TEXT)
        target = _parse(TARGET_TEXT_UNCHANGED)

        matches = match_slides(source, target)
        # 3 target slides (unchanged/unchanged/new) + 1 removed_at_head = 4 entries
        by_key = {m.key: m for m in matches}

        assert by_key["id:intro"].kind is MatchKind.UNCHANGED
        assert by_key["id:rest-vs-soap"].kind is MatchKind.UNCHANGED
        assert by_key["id:new-topic"].kind is MatchKind.NEW_AT_HEAD
        assert by_key["id:gone"].kind is MatchKind.REMOVED_AT_HEAD

    def test_target_indices_match_group_order(self):
        source = _parse(SOURCE_TEXT)
        target = _parse(TARGET_TEXT_UNCHANGED)
        matches = match_slides(source, target)

        intro = next(m for m in matches if m.key == "id:intro")
        assert intro.target_index == 0
        rest = next(m for m in matches if m.key == "id:rest-vs-soap")
        assert rest.target_index == 1


class TestModifiedDetection:
    def test_modified_when_content_diverges(self):
        source = _parse(SOURCE_TEXT)
        target = _parse(TARGET_TEXT_MODIFIED)

        matches = match_slides(source, target)
        intro = next(m for m in matches if m.key == "id:intro")

        assert intro.kind is MatchKind.MODIFIED
        assert intro.content_similarity < 90.0


class TestTitleFallback:
    def test_fallback_to_title_when_ids_absent(self):
        source_text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Einführung
"""
        target_text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Einführung
"""
        source = _parse(source_text)
        target = _parse(target_text)

        matches = match_slides(source, target)
        assert len(matches) == 1
        assert matches[0].kind is MatchKind.UNCHANGED
        assert matches[0].key.startswith("title:einführung") or matches[0].key.startswith(
            "title:einf"
        )

    def test_fuzzy_title_match_when_title_slightly_changed(self):
        source_text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## REST vs SOAP
"""
        target_text = """\
# %% [markdown] lang="de" tags=["slide"]
# ## REST versus SOAP
"""
        source = _parse(source_text)
        target = _parse(target_text)

        matches = match_slides(source, target)
        # Should fuzzy-match the slightly-renamed title.
        assert any(
            m.kind in (MatchKind.UNCHANGED, MatchKind.MODIFIED) and m.source_index is not None
            for m in matches
        )


class TestContentSimilarity:
    def test_identical_is_100(self):
        assert content_similarity("hello world", "hello world") == 100.0

    def test_both_empty_is_100(self):
        assert content_similarity("", "") == 100.0

    def test_one_empty_is_zero(self):
        assert content_similarity("", "hello") == 0.0

    def test_disjoint_content_is_low(self):
        score = content_similarity("apple banana cherry", "xylophone zebra quartz")
        assert score < 50.0

    def test_markdown_formatting_ignored(self):
        a = "## **Bold** _italic_ `code`"
        b = "Bold italic code"
        # Similarity should be very high after normalization.
        assert content_similarity(a, b) > 80.0


class TestHeaderSlidesIgnored:
    def test_header_groups_do_not_match(self):
        # j2-based headers (if present) would have slide_type="header".
        # parse_slides excludes them when include_header=False; for this
        # test we verify match_slides filters them when include_header=True.
        source_text = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="a"
# ## A
"""
        target_text = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="a"
# ## A
"""
        source = group_slides(parse_cells(source_text), "de", include_header=True)
        target = group_slides(parse_cells(target_text), "de", include_header=True)

        matches = match_slides(source, target)
        for m in matches:
            # No header groups should appear as matches.
            assert m.target_group is None or m.target_group.slide_type != "header"
            assert m.source_group is None or m.source_group.slide_type != "header"
