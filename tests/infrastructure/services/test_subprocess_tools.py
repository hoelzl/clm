"""Tests for subprocess_tools module.

This module tests async subprocess execution including:
- Successful subprocess execution
- Timeout handling with retry logic
- Non-retriable errors (FileNotFoundError, PermissionError)
- Process termination
- Retry exhaustion
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clx.infrastructure.services.subprocess_tools import (
    CONVERSION_TIMEOUT,
    NUM_RETRIES,
    SubprocessError,
    run_subprocess,
    try_to_terminate_process,
)


class TestSubprocessError:
    """Test the SubprocessError exception class."""

    def test_subprocess_error_is_exception(self):
        """SubprocessError should be an Exception subclass."""
        assert issubclass(SubprocessError, Exception)

    def test_subprocess_error_with_message(self):
        """SubprocessError should accept a message."""
        error = SubprocessError("Test error message")
        assert str(error) == "Test error message"


class TestConstants:
    """Test module constants."""

    def test_conversion_timeout_value(self):
        """CONVERSION_TIMEOUT should be 60 seconds."""
        assert CONVERSION_TIMEOUT == 60

    def test_num_retries_value(self):
        """NUM_RETRIES should be 3."""
        assert NUM_RETRIES == 3


class TestRunSubprocessSuccess:
    """Test successful subprocess execution."""

    @pytest.mark.asyncio
    async def test_successful_execution_returns_process_and_output(self):
        """Successful execution should return process, stdout, stderr."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"stdout", b"stderr"))
        mock_process.pid = 12345

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(b"stdout", b"stderr"))):
                process, stdout, stderr = await run_subprocess(
                    ["echo", "hello"], "test-correlation-id"
                )

        assert stdout == b"stdout"
        assert stderr == b"stderr"

    @pytest.mark.asyncio
    async def test_subprocess_called_with_correct_args(self):
        """Should call create_subprocess_exec with correct arguments."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_create:
            with patch("asyncio.wait_for", new=AsyncMock(return_value=(b"", b""))):
                await run_subprocess(["cmd", "arg1", "arg2"], "test-id")

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert call_args[0] == ("cmd", "arg1", "arg2")
        assert call_args[1]["stdout"] == asyncio.subprocess.PIPE
        assert call_args[1]["stderr"] == asyncio.subprocess.PIPE


class TestRunSubprocessTimeout:
    """Test timeout handling and retry logic."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_retry(self):
        """Timeout should trigger retry."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        call_count = 0

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise asyncio.TimeoutError()
            return (b"success", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    process, stdout, stderr = await run_subprocess(["cmd"], "test-id")

        assert call_count == 3  # Two timeouts, then success
        assert stdout == b"success"

    @pytest.mark.asyncio
    async def test_timeout_uses_exponential_backoff(self):
        """Timeout should increase exponentially."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        timeouts_used = []

        async def mock_wait_for(coro, timeout):
            timeouts_used.append(timeout)
            if len(timeouts_used) < 3:
                raise asyncio.TimeoutError()
            return (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    await run_subprocess(["cmd"], "test-id")

        # Verify exponential backoff: 60, 120, 240
        assert timeouts_used[0] == CONVERSION_TIMEOUT  # 60
        assert timeouts_used[1] == CONVERSION_TIMEOUT * 2  # 120
        assert timeouts_used[2] == CONVERSION_TIMEOUT * 4  # 240

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises_error(self):
        """Should raise SubprocessError after all retries exhausted."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        async def always_timeout(coro, timeout):
            raise asyncio.TimeoutError()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=always_timeout):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(
                        SubprocessError, match=f"failed after {NUM_RETRIES} attempts"
                    ):
                        await run_subprocess(["cmd"], "test-id")


class TestRunSubprocessNonRetriableErrors:
    """Test non-retriable error handling."""

    @pytest.mark.asyncio
    async def test_file_not_found_raises_immediately(self):
        """FileNotFoundError should raise SubprocessError immediately."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            with pytest.raises(SubprocessError, match="non-retriable error"):
                await run_subprocess(["nonexistent_command"], "test-id")

    @pytest.mark.asyncio
    async def test_permission_error_raises_immediately(self):
        """PermissionError should raise SubprocessError immediately."""
        with patch("asyncio.create_subprocess_exec", side_effect=PermissionError("denied")):
            with pytest.raises(SubprocessError, match="non-retriable error"):
                await run_subprocess(["protected_command"], "test-id")

    @pytest.mark.asyncio
    async def test_non_retriable_error_includes_command(self):
        """Non-retriable error message should include the command."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            with pytest.raises(SubprocessError) as exc_info:
                await run_subprocess(["my_command", "arg1"], "test-id")
            assert "my_command arg1" in str(exc_info.value)


class TestRunSubprocessUnexpectedErrors:
    """Test unexpected error handling."""

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_subprocess_error(self):
        """Unexpected errors should raise SubprocessError."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.returncode = None

        async def raise_unexpected(coro, timeout):
            raise RuntimeError("unexpected")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=raise_unexpected):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessError, match="Unexpected error"):
                        await run_subprocess(["cmd"], "test-id")

    @pytest.mark.asyncio
    async def test_unexpected_error_terminates_process(self):
        """Unexpected errors should terminate the process."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.returncode = None

        async def raise_unexpected(coro, timeout):
            raise RuntimeError("unexpected")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=raise_unexpected):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessError):
                        await run_subprocess(["cmd"], "test-id")

        mock_process.terminate.assert_called()


class TestTryToTerminateProcess:
    """Test the try_to_terminate_process function."""

    @pytest.mark.asyncio
    async def test_terminate_is_called(self):
        """Should call terminate on the process."""
        mock_process = MagicMock()
        mock_process.returncode = 0  # Process exits after terminate
        mock_process.terminate = MagicMock()

        with patch("asyncio.sleep", new=AsyncMock()):
            await try_to_terminate_process("test-id", mock_process)

        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_called_if_still_running(self):
        """Should call kill if process still running after terminate."""
        mock_process = MagicMock()
        mock_process.returncode = None  # Process didn't exit
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        with patch("asyncio.sleep", new=AsyncMock()):
            await try_to_terminate_process("test-id", mock_process)

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_not_called_if_process_exited(self):
        """Should not call kill if process exited after terminate."""
        mock_process = MagicMock()
        mock_process.returncode = 0  # Process exited
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        with patch("asyncio.sleep", new=AsyncMock()):
            await try_to_terminate_process("test-id", mock_process)

        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_process_lookup_error(self):
        """Should handle ProcessLookupError gracefully."""
        mock_process = MagicMock()
        mock_process.terminate = MagicMock(side_effect=ProcessLookupError())

        with patch("asyncio.sleep", new=AsyncMock()):
            # Should not raise
            await try_to_terminate_process("test-id", mock_process)

    @pytest.mark.asyncio
    async def test_handles_other_exceptions(self):
        """Should handle other exceptions gracefully."""
        mock_process = MagicMock()
        mock_process.terminate = MagicMock(side_effect=RuntimeError("unexpected"))

        with patch("asyncio.sleep", new=AsyncMock()):
            # Should not raise
            await try_to_terminate_process("test-id", mock_process)

    @pytest.mark.asyncio
    async def test_waits_before_checking_returncode(self):
        """Should wait 2 seconds before checking if process exited."""
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.terminate = MagicMock()

        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await try_to_terminate_process("test-id", mock_process)

        mock_sleep.assert_called_once_with(2.0)


class TestCorrelationIdInErrors:
    """Test that correlation IDs are included in error messages."""

    @pytest.mark.asyncio
    async def test_correlation_id_in_subprocess_error(self):
        """SubprocessError should include correlation ID."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            with pytest.raises(SubprocessError) as exc_info:
                await run_subprocess(["cmd"], "my-unique-correlation-id")
            assert "my-unique-correlation-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_correlation_id_in_retry_exhausted_error(self):
        """Retry exhausted error should include correlation ID."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        async def always_timeout(coro, timeout):
            raise asyncio.TimeoutError()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=always_timeout):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessError) as exc_info:
                        await run_subprocess(["cmd"], "unique-id-123")
                    assert "unique-id-123" in str(exc_info.value)
