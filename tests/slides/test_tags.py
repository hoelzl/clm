"""Tests for clm.slides.tags — canonical tag definitions."""

from clm.slides.tags import (
    ALL_VALID_TAGS,
    CODE_CONTENT_TAGS,
    EXPECTED_CODE_TAGS,
    EXPECTED_GENERIC_TAGS,
    EXPECTED_MARKDOWN_TAGS,
    PRIVATE_TAGS,
    SLIDE_TAGS,
    STRUCTURAL_TAGS,
)


class TestTagSets:
    """Verify tag set membership and relationships."""

    def test_slide_tags(self):
        assert "slide" in SLIDE_TAGS
        assert "subslide" in SLIDE_TAGS
        assert "notes" in SLIDE_TAGS
        assert "voiceover" in SLIDE_TAGS

    def test_private_tags(self):
        assert "notes" in PRIVATE_TAGS
        assert "voiceover" in PRIVATE_TAGS
        assert "private" in PRIVATE_TAGS
        assert "slide" not in PRIVATE_TAGS

    def test_code_content_tags(self):
        assert "keep" in CODE_CONTENT_TAGS
        assert "start" in CODE_CONTENT_TAGS
        assert "completed" in CODE_CONTENT_TAGS

    def test_structural_tags(self):
        assert "workshop" in STRUCTURAL_TAGS

    def test_completed_tag_is_recognized(self):
        """The 'completed' tag must be in all relevant sets."""
        assert "completed" in EXPECTED_CODE_TAGS
        assert "completed" in EXPECTED_GENERIC_TAGS
        assert "completed" in ALL_VALID_TAGS

    def test_workshop_tag_is_recognized(self):
        """The 'workshop' tag must be in all relevant sets."""
        assert "workshop" in EXPECTED_GENERIC_TAGS
        assert "workshop" in EXPECTED_MARKDOWN_TAGS
        assert "workshop" in ALL_VALID_TAGS

    def test_existing_tags_preserved(self):
        """All tags from the original jupyter_utils.py sets are present."""
        # Original _EXPECTED_CODE_TAGS = {"keep", "start"} | generic
        for tag in (
            "keep",
            "start",
            "slide",
            "subslide",
            "notes",
            "voiceover",
            "private",
            "alt",
            "del",
        ):
            assert tag in EXPECTED_CODE_TAGS, f"{tag!r} missing from EXPECTED_CODE_TAGS"

        # Original _EXPECTED_MARKDOWN_TAGS = {"notes", "voiceover", "answer", "nodataurl"} | generic
        for tag in (
            "notes",
            "voiceover",
            "answer",
            "nodataurl",
            "slide",
            "subslide",
            "private",
            "alt",
            "del",
        ):
            assert tag in EXPECTED_MARKDOWN_TAGS, f"{tag!r} missing from EXPECTED_MARKDOWN_TAGS"

    def test_code_tags_superset_of_generic(self):
        assert EXPECTED_GENERIC_TAGS <= EXPECTED_CODE_TAGS

    def test_markdown_tags_superset_of_generic(self):
        assert EXPECTED_GENERIC_TAGS <= EXPECTED_MARKDOWN_TAGS

    def test_all_valid_is_union(self):
        assert ALL_VALID_TAGS == EXPECTED_CODE_TAGS | EXPECTED_MARKDOWN_TAGS

    def test_sets_are_frozenset(self):
        """Tag sets must be immutable."""
        assert isinstance(SLIDE_TAGS, frozenset)
        assert isinstance(PRIVATE_TAGS, frozenset)
        assert isinstance(EXPECTED_CODE_TAGS, frozenset)
        assert isinstance(EXPECTED_MARKDOWN_TAGS, frozenset)
        assert isinstance(ALL_VALID_TAGS, frozenset)
