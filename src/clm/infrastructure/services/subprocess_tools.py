import asyncio
import logging
from dataclasses import dataclass

CONVERSION_TIMEOUT = 60
NUM_RETRIES = 3

logger = logging.getLogger(__name__)


class SubprocessError(Exception):
    """Exception raised when subprocess execution fails."""

    pass


class SubprocessCrashError(SubprocessError):
    """Exception raised when subprocess crashes with non-zero exit code.

    This is a subclass of SubprocessError that specifically indicates the
    subprocess ran but exited with a non-zero return code, as opposed to
    other failure modes like timeout or permission errors.

    Attributes:
        return_code: The non-zero exit code from the subprocess
        stderr: The stderr output from the subprocess
        stdout: The stdout output from the subprocess
    """

    def __init__(self, message: str, return_code: int, stderr: bytes = b"", stdout: bytes = b""):
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr
        self.stdout = stdout


@dataclass
class RetryConfig:
    """Configuration for subprocess retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts (default: 3)
        base_timeout: Base timeout in seconds, doubles with each retry (default: 60)
        retry_on_crash: Whether to retry when subprocess exits with non-zero code (default: False)
        retry_delay: Delay in seconds between retries for crash recovery (default: 1.0)
    """

    max_retries: int = NUM_RETRIES
    base_timeout: int = CONVERSION_TIMEOUT
    retry_on_crash: bool = False
    retry_delay: float = 1.0


# Default config for backward compatibility
DEFAULT_RETRY_CONFIG = RetryConfig()


async def run_subprocess(
    cmd,
    correlation_id,
    retry_config: RetryConfig | None = None,
    env: dict | None = None,
):
    """Run a subprocess command with retry logic for transient errors.

    Args:
        cmd: Command and arguments to execute
        correlation_id: ID for tracking this operation in logs
        retry_config: Configuration for retry behavior. If None, uses defaults.
        env: Environment variables for the subprocess. If None, inherits parent env.

    Returns:
        Tuple of (process, stdout, stderr)

    Raises:
        SubprocessError: If subprocess fails after all retries
        SubprocessCrashError: If subprocess crashes and retry_on_crash is False,
            or if all crash retries are exhausted
        FileNotFoundError: If the command executable is not found (not retriable)
        PermissionError: If permission is denied (not retriable)
    """
    config = retry_config or DEFAULT_RETRY_CONFIG
    logger.debug(f"{correlation_id}:Waiting for conversion to complete...")

    current_iteration = 0
    last_error: Exception | None = None
    last_stderr: bytes = b""
    last_stdout: bytes = b""
    last_return_code: int = 0

    while current_iteration < config.max_retries:
        current_iteration += 1

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            logger.debug(
                f"{correlation_id}:Communicating with subprocess:"
                f"Iteration {current_iteration}: {process.pid}"
            )

            # Exponential timeout backoff
            timeout = config.base_timeout * 2 ** (current_iteration - 1)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)

            # Check for non-zero exit code
            # After communicate() completes, returncode is guaranteed to be set
            assert process.returncode is not None
            if process.returncode != 0:
                last_return_code = process.returncode
                last_stderr = stderr
                last_stdout = stdout

                if config.retry_on_crash:
                    logger.warning(
                        f"{correlation_id}:Subprocess crashed with exit code {process.returncode} "
                        f"on iteration {current_iteration}/{config.max_retries}. "
                        f"Stderr: {stderr.decode(errors='replace')[:500]}"
                    )
                    if current_iteration < config.max_retries:
                        logger.info(
                            f"{correlation_id}:Retrying after {config.retry_delay}s delay..."
                        )
                        await asyncio.sleep(config.retry_delay)
                        continue
                    # All retries exhausted - break out of loop to raise error
                    break
                else:
                    # Not configured to retry on crash - return as before for backward compat
                    return process, stdout, stderr

            return process, stdout, stderr

        except asyncio.TimeoutError as e:
            # Timeout is retriable - kill process and retry
            last_error = e
            logger.warning(
                f"{correlation_id}:Subprocess timeout on iteration {current_iteration}/{config.max_retries}. "
                f"Retrying..."
            )
            await try_to_terminate_process(correlation_id, process)

        except (FileNotFoundError, PermissionError) as e:
            # Non-retriable errors - fail immediately with context
            raise SubprocessError(
                f"{correlation_id}:Command failed with non-retriable error: {e}\n"
                f"Command: {' '.join(cmd)}"
            ) from e

        except Exception as e:
            # Unexpected errors - log and fail immediately
            logger.error(
                f"{correlation_id}:Unexpected error in subprocess: {e}",
                exc_info=True,
            )
            await try_to_terminate_process(correlation_id, process)
            raise SubprocessError(
                f"{correlation_id}:Unexpected error while running subprocess: {e}\n"
                f"Command: {' '.join(cmd)}"
            ) from e

    # All retries exhausted
    if last_return_code != 0:
        # Crashed subprocess after all retries
        raise SubprocessCrashError(
            f"{correlation_id}:Subprocess crashed after {config.max_retries} attempts\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit code: {last_return_code}\n"
            f"Stderr: {last_stderr.decode(errors='replace')[:1000]}",
            return_code=last_return_code,
            stderr=last_stderr,
            stdout=last_stdout,
        )
    else:
        # Timeout or other error
        raise SubprocessError(
            f"{correlation_id}:Subprocess failed after {config.max_retries} attempts\n"
            f"Command: {' '.join(cmd)}\n"
            f"Last error: {last_error}"
        )


async def try_to_terminate_process(correlation_id, process):
    """Attempt to gracefully terminate a subprocess, then force kill if needed.

    Args:
        correlation_id: ID for tracking this operation in logs
        process: The asyncio subprocess to terminate
    """
    try:
        # Try graceful termination first
        process.terminate()
        await asyncio.sleep(2.0)

        # Force kill if still running
        if process.returncode is None:
            process.kill()
            logger.debug(f"{correlation_id}:Process force killed")

    except ProcessLookupError:
        # Process already terminated - this is fine
        logger.debug(f"{correlation_id}:Process already terminated")

    except Exception as e:
        # Log unexpected errors but don't fail
        logger.warning(
            f"{correlation_id}:Error while terminating subprocess: {e}",
            exc_info=True,
        )
