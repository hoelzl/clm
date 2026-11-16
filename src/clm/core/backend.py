import asyncio
import logging
from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clm.core.image_registry import ImageRegistry
from clm.core.output_write_registry import OutputWriteRegistry

if TYPE_CHECKING:
    from clm.core.build_data_classes import BuildWarning
    from clm.core.messaging.base_classes import Payload
    from clm.core.operation import Operation
    from clm.core.utils.copy_dir_group_data import CopyDirGroupData
    from clm.core.utils.copy_file_data import CopyFileData
    from clm.core.utils.file import File

logger = logging.getLogger(__name__)


class JobsPendingTimeoutError(TimeoutError):
    """Raised when ``wait_for_completion`` times out with jobs still pending.

    Subclasses :class:`TimeoutError` so existing callers/tests that catch
    ``TimeoutError`` (or match on the message) keep working, while giving
    the build orchestration a typed signal that the build did *not*
    complete and must exit non-zero (issue #143, sub-bug A). The pending
    job descriptors are attached so the orchestration can record one
    infrastructure :class:`~clm.core.build_data_classes.BuildError` per
    stuck job in the build summary.
    """

    def __init__(self, message: str, pending_jobs: list[dict] | None = None):
        super().__init__(message)
        self.pending_jobs: list[dict] = pending_jobs or []


class SourceFileVanishedError(FileNotFoundError):
    """A course source file disappeared after the course was loaded (#1270).

    Raised while an operation reads its inputs (e.g. a notebook's slide file
    or a topic supporting file shipped in the payload) when the file was
    enumerated at course-load time but has since been deleted or moved —
    typically an author or an agent editing the course while the build runs.
    That is a content problem, not an infrastructure failure: the build
    reporter classifies an abort caused only by these as a *user* error.

    Subclasses :class:`FileNotFoundError` (and keeps its ``errno`` /
    ``filename``) so existing ``except FileNotFoundError`` handlers still
    catch it.
    """

    @classmethod
    def from_os_error(cls, exc: OSError) -> "SourceFileVanishedError":
        if exc.errno is not None:
            return cls(exc.errno, exc.strerror, exc.filename)
        return cls(str(exc))


class SubmissionDiscardedError(RuntimeError):
    """A job submission was dropped because its build stage aborted (#1270).

    Raised by :meth:`Backend.execute_operation` for a submission that was in
    flight when :meth:`Backend.discard_pending_submissions` was called. Its
    caller has normally been cancelled already; raising (rather than
    returning as if the job ran) makes sure a caller that was not cancelled
    never mistakes the dropped job for a produced output. Collateral of the
    abort, never its cause.
    """


@define
class Backend(AbstractAsyncContextManager):
    output_write_registry: OutputWriteRegistry = field(factory=OutputWriteRegistry)
    image_registry: ImageRegistry = field(factory=ImageRegistry)

    @abstractmethod
    async def execute_operation(self, operation: "Operation", payload: "Payload") -> None: ...

    async def __aenter__(self) -> "Backend":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    @abstractmethod
    async def wait_for_completion(self, all_submitted: asyncio.Event | None = None) -> bool: ...

    @abstractmethod
    async def copy_file_to_output(self, copy_data: "CopyFileData"): ...

    @abstractmethod
    async def copy_dir_group_to_output(self, copy_data: "CopyDirGroupData") -> list["BuildWarning"]:
        """Copy a directory group to the output directory.

        Args:
            copy_data: Data for the copy operation including source dirs and output path.

        Returns:
            List of BuildWarning objects for any issues encountered (e.g., missing directories).
        """
        ...

    @abstractmethod
    async def delete_dependencies(self, file: "File") -> None: ...

    @abstractmethod
    async def delete_file(self, path: Path) -> None: ...

    def discard_pending_submissions(self) -> None:
        """Drop every job submission that has not reached the queue yet.

        Called when a stage has failed and the build is aborting (#1270):
        operations already in flight must not enqueue new worker jobs after
        the abort is decided. Submissions started *after* this call proceed
        normally, so a long-lived backend (watch mode) keeps working.

        The default does nothing (backends without deferred submission).
        """
        return None

    async def cancel_jobs_for_file(self, file_path: Path) -> int:
        """Cancel all pending jobs for a given input file.

        This is used in watch mode when a file is modified to cancel any
        pending jobs before submitting new ones with updated content.

        Args:
            file_path: Path to the input file

        Returns:
            Number of jobs cancelled
        """
        # Default implementation does nothing (for backends that don't support job cancellation)
        return 0
