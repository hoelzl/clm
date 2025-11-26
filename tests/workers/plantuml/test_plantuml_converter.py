"""Tests for plantuml_converter module.

This module tests the PlantUML conversion functionality including:
- Output name extraction from @startuml directive
- Command construction for PlantUML conversion
- JAR path handling
- Error handling
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Check if PlantUML module can be imported (JAR must exist)
def _can_import_plantuml():
    """Check if plantuml_converter can be imported."""
    try:
        # The module validates JAR path at import time
        from clx.workers.plantuml import plantuml_converter

        return True
    except (FileNotFoundError, ImportError):
        return False


# Try to find the PlantUML JAR
def _find_plantuml_jar():
    """Find PlantUML JAR in known locations."""
    possible_paths = [
        Path(__file__).parents[4] / "docker" / "plantuml" / "plantuml-1.2024.6.jar",
        Path(__file__).parents[4] / "plantuml-1.2024.6.jar",
    ]
    for path in possible_paths:
        if path.exists():
            return str(path)
    return None


# Set the environment variable if needed
_jar_path = _find_plantuml_jar()
if _jar_path and not _can_import_plantuml():
    os.environ["PLANTUML_JAR"] = _jar_path

# Now try to import
HAS_PLANTUML = _can_import_plantuml()

# Skip module if PlantUML not available
pytestmark = pytest.mark.skipif(
    not HAS_PLANTUML, reason="PlantUML JAR not found - skipping plantuml_converter tests"
)


# Only import if available to avoid import-time errors
if HAS_PLANTUML:
    from clx.workers.plantuml.plantuml_converter import (
        PLANTUML_NAME_REGEX,
        get_plantuml_output_name,
    )


class TestGetPlantumlOutputName:
    """Test the get_plantuml_output_name function."""

    def test_extract_quoted_name(self):
        """Should extract name from double-quoted @startuml directive."""
        content = '@startuml "my-diagram"\nsome content\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "my-diagram"

    def test_extract_unquoted_name(self):
        """Should extract unquoted name from @startuml directive."""
        content = "@startuml diagram_name\nsome content\n@enduml"
        result = get_plantuml_output_name(content)
        assert result == "diagram_name"

    def test_extract_name_with_spaces_quoted(self):
        """Should handle quoted names with special characters."""
        content = '@startuml "my diagram"\ncontent\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "my diagram"

    def test_no_name_returns_default(self):
        """Should return default when no name in @startuml."""
        content = "@startuml\nsome content\n@enduml"
        result = get_plantuml_output_name(content)
        assert result == "plantuml"

    def test_custom_default(self):
        """Should return custom default when provided."""
        content = "@startuml\ncontent\n@enduml"
        result = get_plantuml_output_name(content, default="custom_default")
        assert result == "custom_default"

    def test_empty_content_returns_default(self):
        """Should return default for empty content."""
        result = get_plantuml_output_name("")
        assert result == "plantuml"

    def test_no_startuml_returns_default(self):
        """Should return default when no @startuml directive."""
        content = "just some random text"
        result = get_plantuml_output_name(content)
        assert result == "plantuml"

    def test_name_with_apostrophe_returns_default(self):
        """Should return default when name contains apostrophe (commented out)."""
        content = "@startuml don't_use_this\ncontent\n@enduml"
        result = get_plantuml_output_name(content)
        # According to the code, if name contains ' it's likely commented out
        assert result == "plantuml"

    def test_quoted_name_with_apostrophe_returns_default(self):
        """Should return default for quoted name with apostrophe."""
        content = '@startuml "diagram\'s_name"\ncontent\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "plantuml"

    def test_multiple_startuml_uses_first(self):
        """Should use the first @startuml directive."""
        content = '@startuml "first"\n@enduml\n@startuml "second"\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "first"

    def test_startuml_with_whitespace(self):
        """Should handle whitespace around name."""
        content = "@startuml   diagram_with_spaces  \ncontent\n@enduml"
        result = get_plantuml_output_name(content)
        assert result == "diagram_with_spaces"

    def test_startuml_at_beginning_of_line(self):
        """Should match @startuml at beginning of content."""
        content = '@startuml "diagram"\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "diagram"

    def test_startuml_not_at_beginning(self):
        """Should match @startuml even with content before it."""
        content = 'Some header\n@startuml "diagram"\ncontent\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "diagram"


class TestPlantumlNameRegex:
    """Test the PLANTUML_NAME_REGEX pattern."""

    def test_regex_matches_quoted_name(self):
        """Regex should match double-quoted names."""
        match = PLANTUML_NAME_REGEX.search('@startuml "my-name"')
        assert match is not None
        assert match.group(1) == "my-name"
        assert match.group(2) is None

    def test_regex_matches_unquoted_name(self):
        """Regex should match unquoted names."""
        match = PLANTUML_NAME_REGEX.search("@startuml simple_name")
        assert match is not None
        assert match.group(1) is None
        assert match.group(2) == "simple_name"

    def test_regex_requires_space_or_tab(self):
        """Regex requires space or tab before name."""
        # Should match with space
        match = PLANTUML_NAME_REGEX.search("@startuml name")
        assert match is not None

        # Should match with tab
        match = PLANTUML_NAME_REGEX.search("@startuml\tname")
        assert match is not None

    def test_regex_no_match_without_name(self):
        """Regex should not match @startuml without name."""
        match = PLANTUML_NAME_REGEX.search("@startuml")
        assert match is None

        match = PLANTUML_NAME_REGEX.search("@startuml\n")
        assert match is None


class TestConvertPlantuml:
    """Test the convert_plantuml function."""

    @pytest.fixture
    def mock_run_subprocess(self):
        """Mock the run_subprocess function."""
        with patch("clx.workers.plantuml.plantuml_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")
            yield mock

    @pytest.mark.asyncio
    async def test_convert_plantuml_basic_command(self, mock_run_subprocess):
        """Should construct basic command with correct arguments."""
        from clx.workers.plantuml.plantuml_converter import convert_plantuml

        input_file = Path("/input/diagram.pu")

        await convert_plantuml(input_file, "test-correlation-id")

        mock_run_subprocess.assert_called_once()
        cmd = mock_run_subprocess.call_args[0][0]

        # Verify command structure
        assert cmd[0] == "java"
        assert "-DPLANTUML_LIMIT_SIZE=8192" in cmd
        assert "-jar" in cmd
        assert "-tpng" in cmd
        assert "-Sdpi=200" in cmd
        assert "-o" in cmd
        assert str(input_file) in cmd

    @pytest.mark.asyncio
    async def test_convert_plantuml_uses_jar_path(self, mock_run_subprocess):
        """Should use configured PLANTUML_JAR path."""
        from clx.workers.plantuml.plantuml_converter import PLANTUML_JAR, convert_plantuml

        await convert_plantuml(Path("/input/diagram.pu"), "test-id")

        cmd = mock_run_subprocess.call_args[0][0]
        jar_idx = cmd.index("-jar")
        assert cmd[jar_idx + 1] == PLANTUML_JAR

    @pytest.mark.asyncio
    async def test_convert_plantuml_output_dir_is_input_parent(self, mock_run_subprocess):
        """Output directory should be the input file's parent directory."""
        from clx.workers.plantuml.plantuml_converter import convert_plantuml

        input_file = Path("/some/nested/path/diagram.pu")

        await convert_plantuml(input_file, "test-id")

        cmd = mock_run_subprocess.call_args[0][0]
        o_idx = cmd.index("-o")
        assert cmd[o_idx + 1] == str(input_file.parent)

    @pytest.mark.asyncio
    async def test_convert_plantuml_passes_correlation_id(self, mock_run_subprocess):
        """Should pass correlation_id to run_subprocess."""
        from clx.workers.plantuml.plantuml_converter import convert_plantuml

        await convert_plantuml(Path("/input.pu"), "my-correlation-id")

        call_args = mock_run_subprocess.call_args
        assert call_args[0][1] == "my-correlation-id"

    @pytest.mark.asyncio
    async def test_convert_plantuml_raises_on_error(self, mock_run_subprocess):
        """Should raise RuntimeError when conversion fails."""
        from clx.workers.plantuml.plantuml_converter import convert_plantuml

        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_run_subprocess.return_value = (mock_process, b"", b"Error message")

        with pytest.raises(RuntimeError, match="Error converting PlantUML file"):
            await convert_plantuml(Path("/input.pu"), "test-id")

    @pytest.mark.asyncio
    async def test_convert_plantuml_success_returns_normally(self, mock_run_subprocess):
        """Successful conversion should return without raising."""
        from clx.workers.plantuml.plantuml_converter import convert_plantuml

        # Should not raise
        await convert_plantuml(Path("/input.pu"), "test-id")


class TestPlantumlConfiguration:
    """Test PlantUML configuration."""

    def test_plantuml_jar_is_set(self):
        """PLANTUML_JAR should be set."""
        from clx.workers.plantuml.plantuml_converter import PLANTUML_JAR

        assert PLANTUML_JAR is not None
        assert isinstance(PLANTUML_JAR, str)
        assert len(PLANTUML_JAR) > 0

    def test_plantuml_jar_path_exists(self):
        """PLANTUML_JAR path should exist."""
        from clx.workers.plantuml.plantuml_converter import PLANTUML_JAR

        # The module raises FileNotFoundError at import if JAR not found
        # So if we get here, the JAR exists
        jar_path = Path(PLANTUML_JAR)
        assert jar_path.exists(), f"PlantUML JAR not found at {PLANTUML_JAR}"


class TestCommandStructure:
    """Test the structure of generated commands."""

    @pytest.mark.asyncio
    async def test_command_has_all_java_options(self):
        """Generated command should have all required Java options."""
        with patch("clx.workers.plantuml.plantuml_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clx.workers.plantuml.plantuml_converter import convert_plantuml

            await convert_plantuml(Path("/test/diagram.pu"), "test-id")

            cmd = mock.call_args[0][0]

            # Verify java command with all options
            assert cmd[0] == "java"

            # Find all options
            has_limit_size = any("-DPLANTUML_LIMIT_SIZE" in arg for arg in cmd)
            has_jar = "-jar" in cmd
            has_png = "-tpng" in cmd
            has_dpi = "-Sdpi=200" in cmd
            has_output = "-o" in cmd

            assert has_limit_size, "Missing PLANTUML_LIMIT_SIZE option"
            assert has_jar, "Missing -jar option"
            assert has_png, "Missing -tpng option"
            assert has_dpi, "Missing -Sdpi option"
            assert has_output, "Missing -o option"

    @pytest.mark.asyncio
    async def test_input_file_is_last_argument(self):
        """Input file should be the last argument."""
        with patch("clx.workers.plantuml.plantuml_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clx.workers.plantuml.plantuml_converter import convert_plantuml

            input_file = Path("/test/my_diagram.pu")
            await convert_plantuml(input_file, "test-id")

            cmd = mock.call_args[0][0]
            assert cmd[-1] == str(input_file)


class TestEdgeCases:
    """Test edge cases for output name extraction."""

    def test_name_with_numbers(self):
        """Should handle names with numbers."""
        content = "@startuml diagram123\n@enduml"
        result = get_plantuml_output_name(content)
        assert result == "diagram123"

    def test_name_with_underscores(self):
        """Should handle names with underscores."""
        content = "@startuml my_diagram_name\n@enduml"
        result = get_plantuml_output_name(content)
        assert result == "my_diagram_name"

    def test_name_with_hyphens_quoted(self):
        """Should handle quoted names with hyphens."""
        content = '@startuml "my-diagram-name"\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == "my-diagram-name"

    def test_quoted_name_empty(self):
        """Should handle empty quoted name."""
        content = '@startuml ""\n@enduml'
        result = get_plantuml_output_name(content)
        # Empty quotes don't match the quoted pattern (requires 1+ chars),
        # so it matches the unquoted pattern and returns literal '""'
        assert result == '""'

    def test_very_long_name(self):
        """Should handle very long names."""
        long_name = "a" * 100
        content = f'@startuml "{long_name}"\n@enduml'
        result = get_plantuml_output_name(content)
        assert result == long_name
