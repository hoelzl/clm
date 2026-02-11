"""Tests for subprocess_tools module.

This module tests async subprocess execution including:
- Successful subprocess execution
- Timeout handling with retry logic
- Non-retriable errors (FileNotFoundError, PermissionError)
- Process termination
- Retry exhaustion
- Crash retry logic (retry_on_crash)
- RetryConfig configuration
- SubprocessCrashError handling
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.infrastructure.services.subprocess_tools import (
    CONVERSION_TIMEOUT,
    DEFAULT_RETRY_CONFIG,
    NUM_RETRIES,
    RetryConfig,
    SubprocessCrashError,
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

        async def mock_wait_for(coro, timeout):
            # Properly close the coroutine to avoid RuntimeWarning
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"stdout", b"stderr")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
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

        async def mock_wait_for(coro, timeout):
            # Properly await or close the coroutine to avoid RuntimeWarning
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_create:
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
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
            # Simulate successful completion - returncode is set
            mock_process.returncode = 0
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
            # Properly close the coroutine to avoid RuntimeWarning
            try:
                coro.close()
            except AttributeError:
                pass
            timeouts_used.append(timeout)
            if len(timeouts_used) < 3:
                raise asyncio.TimeoutError()
            # Simulate successful completion - returncode is set
            mock_process.returncode = 0
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


class TestSubprocessCrashError:
    """Test the SubprocessCrashError exception class."""

    def test_subprocess_crash_error_is_subprocess_error(self):
        """SubprocessCrashError should be a SubprocessError subclass."""
        assert issubclass(SubprocessCrashError, SubprocessError)

    def test_subprocess_crash_error_is_exception(self):
        """SubprocessCrashError should be an Exception subclass."""
        assert issubclass(SubprocessCrashError, Exception)

    def test_subprocess_crash_error_with_all_attributes(self):
        """SubprocessCrashError should store return_code, stderr, stdout."""
        error = SubprocessCrashError(
            "Test crash", return_code=1, stderr=b"error output", stdout=b"normal output"
        )
        assert str(error) == "Test crash"
        assert error.return_code == 1
        assert error.stderr == b"error output"
        assert error.stdout == b"normal output"

    def test_subprocess_crash_error_with_defaults(self):
        """SubprocessCrashError should have default empty bytes for stderr/stdout."""
        error = SubprocessCrashError("Test crash", return_code=42)
        assert error.return_code == 42
        assert error.stderr == b""
        assert error.stdout == b""

    def test_subprocess_crash_error_negative_return_code(self):
        """SubprocessCrashError should handle negative return codes (signals)."""
        error = SubprocessCrashError("Signal killed", return_code=-9)
        assert error.return_code == -9


class TestRetryConfig:
    """Test the RetryConfig dataclass."""

    def test_default_values(self):
        """RetryConfig should have sensible defaults."""
        config = RetryConfig()
        assert config.max_retries == NUM_RETRIES
        assert config.base_timeout == CONVERSION_TIMEOUT
        assert config.retry_on_crash is False
        assert config.retry_delay == 1.0

    def test_custom_values(self):
        """RetryConfig should accept custom values."""
        config = RetryConfig(max_retries=5, base_timeout=30, retry_on_crash=True, retry_delay=2.5)
        assert config.max_retries == 5
        assert config.base_timeout == 30
        assert config.retry_on_crash is True
        assert config.retry_delay == 2.5

    def test_default_retry_config_constant(self):
        """DEFAULT_RETRY_CONFIG should match RetryConfig defaults."""
        assert DEFAULT_RETRY_CONFIG.max_retries == NUM_RETRIES
        assert DEFAULT_RETRY_CONFIG.base_timeout == CONVERSION_TIMEOUT
        assert DEFAULT_RETRY_CONFIG.retry_on_crash is False
        assert DEFAULT_RETRY_CONFIG.retry_delay == 1.0


class TestRunSubprocessWithRetryConfig:
    """Test run_subprocess with custom RetryConfig."""

    @pytest.mark.asyncio
    async def test_custom_max_retries(self):
        """Should respect custom max_retries value."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        call_count = 0

        async def always_timeout(coro, timeout):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        config = RetryConfig(max_retries=5)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=always_timeout):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessError):
                        await run_subprocess(["cmd"], "test-id", retry_config=config)

        assert call_count == 5

    @pytest.mark.asyncio
    async def test_custom_base_timeout(self):
        """Should use custom base_timeout for exponential backoff."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        timeouts_used = []

        async def capture_timeout(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            timeouts_used.append(timeout)
            if len(timeouts_used) < 3:
                raise asyncio.TimeoutError()
            # Simulate successful completion - returncode is set
            mock_process.returncode = 0
            return (b"", b"")

        config = RetryConfig(base_timeout=10)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=capture_timeout):
                with patch("asyncio.sleep", new=AsyncMock()):
                    await run_subprocess(["cmd"], "test-id", retry_config=config)

        # Verify exponential backoff with custom base: 10, 20, 40
        assert timeouts_used[0] == 10
        assert timeouts_used[1] == 20
        assert timeouts_used[2] == 40


class TestRunSubprocessCrashRetry:
    """Test crash retry logic (retry_on_crash)."""

    @pytest.mark.asyncio
    async def test_no_retry_on_crash_by_default(self):
        """By default, non-zero exit code should not trigger retry."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"out", b"error"))

        call_count = 0

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"out", b"error")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                process, stdout, stderr = await run_subprocess(["cmd"], "test-id")

        # Should return after first call, not retry
        assert call_count == 1
        assert process.returncode == 1

    @pytest.mark.asyncio
    async def test_retry_on_crash_enabled_triggers_retry(self):
        """With retry_on_crash=True, non-zero exit code should trigger retry."""
        call_count = 0

        def create_mock_process():
            nonlocal call_count
            mock_process = MagicMock()
            mock_process.pid = 12345 + call_count
            # First 2 calls crash, third succeeds
            if call_count < 2:
                mock_process.returncode = 1
            else:
                mock_process.returncode = 0
            return mock_process

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            if call_count < 3:
                return (b"out", b"crash error")
            return (b"success", b"")

        config = RetryConfig(retry_on_crash=True, retry_delay=0.01)

        with patch(
            "asyncio.create_subprocess_exec", side_effect=lambda *a, **kw: create_mock_process()
        ):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
                    process, stdout, stderr = await run_subprocess(
                        ["cmd"], "test-id", retry_config=config
                    )

        assert call_count == 3  # Two crashes, then success
        assert stdout == b"success"
        # Verify delay was used between retries
        assert mock_sleep.call_count == 2  # Called after each crash before retry

    @pytest.mark.asyncio
    async def test_crash_retry_uses_configured_delay(self):
        """Crash retry should use the configured retry_delay."""
        call_count = 0

        def create_mock_process():
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.returncode = 1 if call_count < 2 else 0
            return mock_process

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"out", b"error" if call_count < 3 else b"")

        config = RetryConfig(retry_on_crash=True, retry_delay=5.0)

        with patch(
            "asyncio.create_subprocess_exec", side_effect=lambda *a, **kw: create_mock_process()
        ):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
                    await run_subprocess(["cmd"], "test-id", retry_config=config)

        # Verify the correct delay was used
        for call in mock_sleep.call_args_list:
            assert call[0][0] == 5.0

    @pytest.mark.asyncio
    async def test_crash_retry_exhausted_raises_crash_error(self):
        """When all crash retries exhausted, should raise SubprocessCrashError."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 42

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"stdout content", b"stderr content")

        config = RetryConfig(max_retries=3, retry_on_crash=True, retry_delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessCrashError) as exc_info:
                        await run_subprocess(["cmd", "arg"], "test-id", retry_config=config)

        error = exc_info.value
        assert error.return_code == 42
        assert error.stderr == b"stderr content"
        assert error.stdout == b"stdout content"
        assert "crashed after 3 attempts" in str(error)
        assert "Exit code: 42" in str(error)

    @pytest.mark.asyncio
    async def test_crash_error_includes_stderr_in_message(self):
        """SubprocessCrashError message should include stderr content."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 1

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"", b"V8 Fatal Error: Invoke in DisallowJavascriptExecutionScope")

        config = RetryConfig(max_retries=2, retry_on_crash=True, retry_delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessCrashError) as exc_info:
                        await run_subprocess(["drawio"], "test-id", retry_config=config)

        assert "V8 Fatal Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_crash_retry_count_independent_of_timeout_retry(self):
        """Crash retries should be counted in the same retry budget as timeouts."""
        call_count = 0
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        async def alternating_failures(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            # Alternate between timeout and crash
            if call_count % 2 == 1:
                raise asyncio.TimeoutError()
            else:
                mock_process.returncode = 1
                return (b"", b"crash")

        config = RetryConfig(max_retries=4, retry_on_crash=True, retry_delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=alternating_failures):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises((SubprocessError, SubprocessCrashError)):
                        await run_subprocess(["cmd"], "test-id", retry_config=config)

        # Should have made exactly max_retries attempts
        assert call_count == 4


class TestRunSubprocessEnvParameter:
    """Test environment variable handling."""

    @pytest.mark.asyncio
    async def test_env_passed_to_subprocess(self):
        """Environment dict should be passed to subprocess."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"", b"")

        custom_env = {"MY_VAR": "my_value", "DISPLAY": ":99"}

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_create:
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                await run_subprocess(["cmd"], "test-id", env=custom_env)

        # Verify env was passed
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["env"] == custom_env

    @pytest.mark.asyncio
    async def test_env_none_by_default(self):
        """When env is not specified, it should be None (inherit parent env)."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 0

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_create:
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                await run_subprocess(["cmd"], "test-id")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["env"] is None


class TestRunSubprocessCrashRecovery:
    """Test crash recovery scenarios that simulate real-world issues."""

    @pytest.mark.asyncio
    async def test_transient_crash_recovers(self):
        """Simulates DrawIO V8 crash that recovers on retry."""
        call_count = 0

        def create_mock_process():
            nonlocal call_count
            mock_process = MagicMock()
            mock_process.pid = 12345 + call_count
            # First call crashes (like V8 GC issue), second succeeds
            mock_process.returncode = 0 if call_count >= 1 else 1
            return mock_process

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            if call_count == 1:
                return (b"", b"Fatal error in V8\nInvoke in DisallowJavascriptExecutionScope")
            return (b"success", b"")

        config = RetryConfig(retry_on_crash=True, retry_delay=0.01)

        with patch(
            "asyncio.create_subprocess_exec", side_effect=lambda *a, **kw: create_mock_process()
        ):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    process, stdout, stderr = await run_subprocess(
                        ["drawio", "--export"], "test-id", retry_config=config
                    )

        assert call_count == 2
        assert stdout == b"success"
        assert process.returncode == 0

    @pytest.mark.asyncio
    async def test_persistent_crash_fails_after_retries(self):
        """Subprocess that crashes every time should fail after max retries."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = -11  # SIGSEGV

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"", b"Segmentation fault")

        config = RetryConfig(max_retries=3, retry_on_crash=True, retry_delay=0.01)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessCrashError) as exc_info:
                        await run_subprocess(["crashy_app"], "test-id", retry_config=config)

        assert exc_info.value.return_code == -11
        assert b"Segmentation fault" in exc_info.value.stderr

    @pytest.mark.asyncio
    async def test_zero_return_code_succeeds_immediately(self):
        """Successful execution (return code 0) should not retry."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 0

        call_count = 0

        async def mock_wait_for(coro, timeout):
            nonlocal call_count
            call_count += 1
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"output", b"")

        config = RetryConfig(retry_on_crash=True)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                process, stdout, stderr = await run_subprocess(
                    ["cmd"], "test-id", retry_config=config
                )

        assert call_count == 1
        assert stdout == b"output"


class TestBackwardCompatibility:
    """Test that existing behavior is preserved for callers not using new features."""

    @pytest.mark.asyncio
    async def test_run_subprocess_without_retry_config(self):
        """Calling run_subprocess without retry_config should work as before."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 0

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"output", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                process, stdout, stderr = await run_subprocess(["cmd"], "test-id")

        assert stdout == b"output"

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_normally_without_retry_on_crash(self):
        """Non-zero exit without retry_on_crash should return normally (old behavior)."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = 5

        async def mock_wait_for(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            return (b"out", b"err")

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=mock_wait_for):
                process, stdout, stderr = await run_subprocess(["cmd"], "test-id")

        # Should return without raising, allowing caller to check returncode
        assert process.returncode == 5
        assert stdout == b"out"
        assert stderr == b"err"

    @pytest.mark.asyncio
    async def test_default_config_matches_original_constants(self):
        """Default RetryConfig should use original constant values."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()

        timeouts_used = []

        async def capture_timeout(coro, timeout):
            try:
                coro.close()
            except AttributeError:
                pass
            timeouts_used.append(timeout)
            raise asyncio.TimeoutError()

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with patch("asyncio.wait_for", side_effect=capture_timeout):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(SubprocessError):
                        await run_subprocess(["cmd"], "test-id")

        # Should use original constants
        assert len(timeouts_used) == NUM_RETRIES
        assert timeouts_used[0] == CONVERSION_TIMEOUT
