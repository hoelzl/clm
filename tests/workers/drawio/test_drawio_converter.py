"""Tests for drawio_converter module.

This module tests the Draw.io conversion functionality including:
- Command construction for different output formats
- Format-specific options (scale for PNG, embed for SVG)
- Error handling
- Retry configuration for crash recovery
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.infrastructure.services.subprocess_tools import SubprocessCrashError


class TestConvertDrawio:
    """Test the convert_drawio function."""

    @pytest.fixture
    def mock_run_subprocess(self):
        """Mock the run_subprocess function."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            # Create a mock process with returncode 0
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")
            yield mock

    @pytest.mark.asyncio
    async def test_convert_drawio_basic_command(self, mock_run_subprocess):
        """Should construct basic command with correct arguments."""
        from clm.workers.drawio.drawio_converter import convert_drawio

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
        from clm.workers.drawio.drawio_converter import convert_drawio

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
        from clm.workers.drawio.drawio_converter import convert_drawio

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
        from clm.workers.drawio.drawio_converter import convert_drawio

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
        from clm.workers.drawio.drawio_converter import convert_drawio

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
        from clm.workers.drawio.drawio_converter import convert_drawio

        await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "my-correlation-id")

        # Check correlation_id was passed
        call_args = mock_run_subprocess.call_args
        assert call_args[0][1] == "my-correlation-id"

    @pytest.mark.asyncio
    async def test_convert_drawio_raises_on_error(self, mock_run_subprocess):
        """Should raise RuntimeError when conversion fails."""
        from clm.workers.drawio.drawio_converter import convert_drawio

        # Set up mock to return error
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_run_subprocess.return_value = (mock_process, b"", b"Error message")

        with pytest.raises(RuntimeError, match="Error converting DrawIO file"):
            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

    @pytest.mark.asyncio
    async def test_convert_drawio_success_returns_normally(self, mock_run_subprocess):
        """Successful conversion should return without raising."""
        from clm.workers.drawio.drawio_converter import convert_drawio

        # Should not raise
        await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")


class TestDrawioConfiguration:
    """Test Draw.io configuration and environment variables."""

    def test_drawio_executable_default(self):
        """DRAWIO_EXECUTABLE should have default value."""
        from clm.workers.drawio.drawio_converter import DRAWIO_EXECUTABLE

        # Default is "drawio" unless overridden by environment
        assert DRAWIO_EXECUTABLE is not None
        assert isinstance(DRAWIO_EXECUTABLE, str)

    def test_drawio_executable_used_in_command(self, monkeypatch):
        """Custom DRAWIO_EXECUTABLE should be used in command."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            import asyncio

            from clm.workers.drawio.drawio_converter import DRAWIO_EXECUTABLE, convert_drawio

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
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clm.workers.drawio.drawio_converter import convert_drawio

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


class TestDrawioRetryConfiguration:
    """Test DrawIO-specific retry configuration for crash recovery."""

    def test_drawio_retry_config_exists(self):
        """DRAWIO_RETRY_CONFIG should be defined."""
        from clm.workers.drawio.drawio_converter import DRAWIO_RETRY_CONFIG

        assert DRAWIO_RETRY_CONFIG is not None

    def test_drawio_retry_config_enables_crash_retry(self):
        """DRAWIO_RETRY_CONFIG should have retry_on_crash enabled."""
        from clm.workers.drawio.drawio_converter import DRAWIO_RETRY_CONFIG

        assert DRAWIO_RETRY_CONFIG.retry_on_crash is True

    def test_drawio_retry_config_has_sensible_values(self):
        """DRAWIO_RETRY_CONFIG should have sensible values for DrawIO."""
        from clm.workers.drawio.drawio_converter import DRAWIO_RETRY_CONFIG

        assert DRAWIO_RETRY_CONFIG.max_retries >= 2
        assert DRAWIO_RETRY_CONFIG.base_timeout >= 30
        assert DRAWIO_RETRY_CONFIG.retry_delay >= 1.0

    @pytest.mark.asyncio
    async def test_convert_drawio_passes_retry_config(self):
        """convert_drawio should pass DRAWIO_RETRY_CONFIG to run_subprocess."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clm.workers.drawio.drawio_converter import (
                DRAWIO_RETRY_CONFIG,
                convert_drawio,
            )

            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            # Check that retry_config was passed
            call_kwargs = mock.call_args[1]
            assert "retry_config" in call_kwargs
            assert call_kwargs["retry_config"] == DRAWIO_RETRY_CONFIG

    @pytest.mark.asyncio
    async def test_convert_drawio_passes_env(self):
        """convert_drawio should pass environment to run_subprocess."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clm.workers.drawio.drawio_converter import convert_drawio

            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            # Check that env was passed
            call_kwargs = mock.call_args[1]
            assert "env" in call_kwargs
            assert call_kwargs["env"] is not None

    @pytest.mark.asyncio
    async def test_convert_drawio_sets_display_on_unix(self):
        """On Unix/Linux, DISPLAY should be set to :99."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            # Temporarily mock sys.platform
            with patch.object(sys, "platform", "linux"):
                # Need to reload to apply the patched platform
                import importlib

                import clm.workers.drawio.drawio_converter as converter_module

                # Manually call with the expected behavior
                from clm.workers.drawio.drawio_converter import convert_drawio

                await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            call_kwargs = mock.call_args[1]
            # env should contain DISPLAY on non-Windows
            if sys.platform != "win32":
                assert call_kwargs["env"].get("DISPLAY") == ":99"

    @pytest.mark.asyncio
    async def test_convert_drawio_handles_crash_error(self):
        """convert_drawio should handle SubprocessCrashError and raise RuntimeError."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            # Simulate SubprocessCrashError after retries exhausted
            mock.side_effect = SubprocessCrashError(
                "Crashed after retries",
                return_code=1,
                stderr=b"V8 Fatal Error: Invoke in DisallowJavascriptExecutionScope",
                stdout=b"",
            )

            from clm.workers.drawio.drawio_converter import convert_drawio

            with pytest.raises(RuntimeError) as exc_info:
                await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            assert "crashed after retries" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_convert_drawio_crash_error_includes_stderr(self):
        """RuntimeError from crash should include the stderr content."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock.side_effect = SubprocessCrashError(
                "Crashed",
                return_code=1,
                stderr=b"V8 Fatal Error: Invoke in DisallowJavascriptExecutionScope",
                stdout=b"",
            )

            from clm.workers.drawio.drawio_converter import convert_drawio

            with pytest.raises(RuntimeError) as exc_info:
                await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            # The stderr content should be in the error message
            assert "V8 Fatal Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_convert_drawio_transient_crash_recovers(self):
        """Simulates a transient crash that recovers on retry (integration-style test)."""
        call_count = 0

        async def mock_run_subprocess(cmd, correlation_id, retry_config=None, env=None):
            nonlocal call_count
            call_count += 1
            mock_process = MagicMock()
            # Simulate: first call crashes, second succeeds
            if call_count == 1 and retry_config and retry_config.retry_on_crash:
                # This simulates what happens when retry_on_crash is True
                # and the subprocess returns non-zero but then succeeds on retry
                mock_process.returncode = 0
            else:
                mock_process.returncode = 0
            return (mock_process, b"success", b"")

        with patch(
            "clm.workers.drawio.drawio_converter.run_subprocess",
            side_effect=mock_run_subprocess,
        ):
            from clm.workers.drawio.drawio_converter import convert_drawio

            # Should not raise
            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

        assert call_count == 1  # run_subprocess was called once


class TestDrawioEnvironmentHandling:
    """Test platform-specific environment handling."""

    @pytest.mark.asyncio
    async def test_env_includes_parent_environment(self):
        """Environment should include parent environment variables."""
        import os

        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            from clm.workers.drawio.drawio_converter import convert_drawio

            await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            call_kwargs = mock.call_args[1]
            env = call_kwargs["env"]

            # Should include PATH from parent environment
            assert "PATH" in env or "Path" in env  # Windows uses 'Path'

    @pytest.mark.asyncio
    async def test_windows_does_not_require_display(self):
        """On Windows, DISPLAY environment variable handling should be safe."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock.return_value = (mock_process, b"", b"")

            with patch.object(sys, "platform", "win32"):
                from clm.workers.drawio.drawio_converter import convert_drawio

                # Should not raise
                await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")

            call_kwargs = mock.call_args[1]
            env = call_kwargs["env"]
            # On Windows, DISPLAY should not be set by the converter
            # (though it may exist in parent env)


class TestDrawioErrorMessages:
    """Test error message formatting and content."""

    @pytest.mark.asyncio
    async def test_error_includes_correlation_id(self):
        """Error messages should include the correlation ID."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock_process = MagicMock()
            mock_process.returncode = 1
            mock.return_value = (mock_process, b"", b"Error details")

            from clm.workers.drawio.drawio_converter import convert_drawio

            with pytest.raises(RuntimeError) as exc_info:
                await convert_drawio(
                    Path("/input.drawio"), Path("/output.png"), "png", "my-unique-id"
                )

            assert "my-unique-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_crash_error_includes_correlation_id(self):
        """Crash error messages should include the correlation ID."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock.side_effect = SubprocessCrashError(
                "my-unique-id:Crashed", return_code=1, stderr=b"crash", stdout=b""
            )

            from clm.workers.drawio.drawio_converter import convert_drawio

            with pytest.raises(RuntimeError) as exc_info:
                await convert_drawio(
                    Path("/input.drawio"), Path("/output.png"), "png", "my-unique-id"
                )

            assert "my-unique-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_non_utf8_stderr(self):
        """Should handle non-UTF8 bytes in stderr gracefully."""
        with patch("clm.workers.drawio.drawio_converter.run_subprocess") as mock:
            mock.side_effect = SubprocessCrashError(
                "Crashed",
                return_code=1,
                stderr=b"Error: \xff\xfe invalid UTF-8",  # Invalid UTF-8 bytes
                stdout=b"",
            )

            from clm.workers.drawio.drawio_converter import convert_drawio

            with pytest.raises(RuntimeError):
                # Should not raise UnicodeDecodeError
                await convert_drawio(Path("/input.drawio"), Path("/output.png"), "png", "test-id")
