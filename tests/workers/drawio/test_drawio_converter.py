"""Tests for drawio_converter module.

This module tests the Draw.io conversion functionality including:
- Command construction for different output formats
- Format-specific options (scale for PNG, embed for SVG)
- Error handling
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestConvertDrawio:
    """Test the convert_drawio function."""

    @pytest.fixture
    def mock_run_subprocess(self):
        """Mock the run_subprocess function."""
        with patch("clx.workers.drawio.drawio_converter.run_subprocess") as mock:
            # Create a mock process with returncode 0
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")
            yield mock

    @pytest.mark.asyncio
    async def test_convert_drawio_basic_command(self, mock_run_subprocess):
        """Should construct basic command with correct arguments."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        input_path = Path("/input/diagram.drawio")
        output_path = Path("/output/diagram.png")

        await convert_drawio(input_path, output_path, "png", "test-correlation-id")

        mock_run_subprocess.assert_called_once()
        cmd = mock_run_subprocess.call_args[0][0]

        # Verify command structure
        assert "--export" in cmd
        assert "--format" in cmd
        assert "png" in cmd
        assert "--output" in cmd
        assert "--border" in cmd
        assert "20" in cmd
        assert "--no-sandbox" in cmd

    @pytest.mark.asyncio
    async def test_convert_drawio_png_format_includes_scale(self, mock_run_subprocess):
        """PNG format should include --scale option."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        input_path = Path("/input/diagram.drawio")
        output_path = Path("/output/diagram.png")

        await convert_drawio(input_path, output_path, "png", "test-id")

        cmd = mock_run_subprocess.call_args[0][0]
        assert "--scale" in cmd
        scale_idx = cmd.index("--scale")
        assert cmd[scale_idx + 1] == "3"

    @pytest.mark.asyncio
    async def test_convert_drawio_svg_format_includes_embed(self, mock_run_subprocess):
        """SVG format should include --embed-svg-images option."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        input_path = Path("/input/diagram.drawio")
        output_path = Path("/output/diagram.svg")

        await convert_drawio(input_path, output_path, "svg", "test-id")

        cmd = mock_run_subprocess.call_args[0][0]
        assert "--embed-svg-images" in cmd
        # SVG should NOT have scale
        assert "--scale" not in cmd

    @pytest.mark.asyncio
    async def test_convert_drawio_pdf_format_no_extra_options(self, mock_run_subprocess):
        """PDF format should not have format-specific options."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        input_path = Path("/input/diagram.drawio")
        output_path = Path("/output/diagram.pdf")

        await convert_drawio(input_path, output_path, "pdf", "test-id")

        cmd = mock_run_subprocess.call_args[0][0]
        # PDF should have neither scale nor embed options
        assert "--scale" not in cmd
        assert "--embed-svg-images" not in cmd

    @pytest.mark.asyncio
    async def test_convert_drawio_uses_posix_paths(self, mock_run_subprocess):
        """Should use POSIX-style paths in command."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        input_path = Path("/input/path/diagram.drawio")
        output_path = Path("/output/path/diagram.png")

        await convert_drawio(input_path, output_path, "png", "test-id")

        cmd = mock_run_subprocess.call_args[0][0]

        # Find input and output paths in command
        input_posix = input_path.as_posix()
        output_posix = output_path.as_posix()

        assert input_posix in cmd
        assert output_posix in cmd

    @pytest.mark.asyncio
    async def test_convert_drawio_passes_correlation_id(self, mock_run_subprocess):
        """Should pass correlation_id to run_subprocess."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "my-correlation-id")

        # Check correlation_id was passed
        call_args = mock_run_subprocess.call_args
        assert call_args[0][1] == "my-correlation-id"

    @pytest.mark.asyncio
    async def test_convert_drawio_raises_on_error(self, mock_run_subprocess):
        """Should raise RuntimeError when conversion fails."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        # Set up mock to return error
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_run_subprocess.return_value = (mock_process, b"", b"Error message")

        with pytest.raises(RuntimeError, match="Error converting DrawIO file"):
            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

    @pytest.mark.asyncio
    async def test_convert_drawio_success_returns_normally(self, mock_run_subprocess):
        """Successful conversion should return without raising."""
        from clx.workers.drawio.drawio_converter import convert_drawio

        # Should not raise
        await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")


class TestDrawioConfiguration:
    """Test Draw.io configuration and environment variables."""

    def test_drawio_executable_default(self):
        """DRAWIO_EXECUTABLE should have default value."""
        from clx.workers.drawio.drawio_converter import DRAWIO_EXECUTABLE

        # Default is "drawio" unless overridden by environment
        assert DRAWIO_EXECUTABLE is not None
        assert isinstance(DRAWIO_EXECUTABLE, str)

    def test_drawio_executable_used_in_command(self, monkeypatch):
        """Custom DRAWIO_EXECUTABLE should be used in command."""
        with patch("clx.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            import asyncio

            from clx.workers.drawio.drawio_converter import DRAWIO_EXECUTABLE, convert_drawio

            asyncio.run(
                convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")
            )

            cmd = mock.call_args[0][0]
            # First element should be the executable
            assert cmd[0] == DRAWIO_EXECUTABLE


class TestCommandStructure:
    """Test the structure of generated commands."""

    @pytest.mark.asyncio
    async def test_command_has_all_required_parts(self):
        """Generated command should have all required parts."""
        with patch("clx.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clx.workers.drawio.drawio_converter import convert_drawio

            await convert_drawio(
                Path("/test/input.drawio"), Path("/test/output.png"), "png", "test-id"
            )

            cmd = mock.call_args[0][0]

            # Verify order and structure
            assert cmd[1] == "--no-sandbox"
            assert cmd[2] == "--export"
            # Input path should be after --export
            assert "/test/input.drawio" in cmd[3]
            assert cmd[4] == "--format"
            assert cmd[5] == "png"
            assert cmd[6] == "--output"
            assert "/test/output.png" in cmd[7]
            assert cmd[8] == "--border"
            assert cmd[9] == "20"
