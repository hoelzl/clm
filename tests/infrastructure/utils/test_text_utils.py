"""Tests for the shared ANSI escape scrubber (moved from clm.cli in #802/A2)."""

from clm.infrastructure.utils.text_utils import ANSI_ESCAPE_PATTERN, strip_ansi


class TestStripAnsi:
    """Test ANSI escape sequence removal."""

    def test_strip_ansi_empty_string(self):
        """Empty string should return empty string."""
        assert strip_ansi("") == ""

    def test_strip_ansi_none_returns_none(self):
        """None should return falsy value."""
        result = strip_ansi(None)
        assert not result

    def test_strip_ansi_no_codes(self):
        """Text without ANSI codes should be unchanged."""
        text = "Hello, World!"
        assert strip_ansi(text) == "Hello, World!"

    def test_strip_ansi_color_codes(self):
        """Should strip color codes."""
        # Red text
        text = "\033[31mRed text\033[0m"
        assert strip_ansi(text) == "Red text"

    def test_strip_ansi_bold_codes(self):
        """Should strip bold codes."""
        text = "\033[1mBold text\033[0m"
        assert strip_ansi(text) == "Bold text"

    def test_strip_ansi_multiple_codes(self):
        """Should strip multiple codes in sequence."""
        # Bold red with green
        text = "\033[1m\033[31mBold red\033[0m and \033[32mgreen\033[0m"
        assert strip_ansi(text) == "Bold red and green"

    def test_strip_ansi_256_color(self):
        """Should strip 256-color codes."""
        text = "\033[38;5;208mOrange\033[0m"
        assert strip_ansi(text) == "Orange"

    def test_strip_ansi_osc_sequences(self):
        """Should strip OSC sequences (like terminal title)."""
        text = "\033]0;Window Title\007Normal text"
        assert strip_ansi(text) == "Normal text"

    def test_strip_ansi_cursor_movement(self):
        """Should strip cursor movement codes."""
        # Cursor up, down, forward, back
        text = "Before\033[2AAfter"  # Move up 2
        assert strip_ansi(text) == "BeforeAfter"

    def test_strip_ansi_preserves_newlines(self):
        """Newlines should be preserved."""
        text = "\033[32mLine 1\033[0m\nLine 2"
        assert strip_ansi(text) == "Line 1\nLine 2"


class TestAnsiEscapePattern:
    """Test the ANSI escape pattern regex."""

    def test_pattern_matches_csi_sequences(self):
        """Pattern should match CSI sequences."""
        assert ANSI_ESCAPE_PATTERN.search("\033[0m")
        assert ANSI_ESCAPE_PATTERN.search("\033[1;31m")
        assert ANSI_ESCAPE_PATTERN.search("\033[38;5;208m")

    def test_pattern_matches_osc_sequences(self):
        """Pattern should match OSC sequences."""
        assert ANSI_ESCAPE_PATTERN.search("\033]0;Title\007")
