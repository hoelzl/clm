"""Base formatter for status output."""

from abc import ABC, abstractmethod

from clm.cli.status.models import StatusInfo


class StatusFormatter(ABC):
    """Base class for status formatters."""

    @abstractmethod
    def format(
        self, status: StatusInfo, workers_only: bool = False, jobs_only: bool = False
    ) -> str:
        """Format status information for output.

        Args:
            status: Status information to format
            workers_only: Only show worker information
            jobs_only: Only show job queue information

        Returns:
            Formatted string ready for output
        """
        pass

    @abstractmethod
    def get_exit_code(self, status: StatusInfo) -> int:
        """Get appropriate exit code for status.

        Args:
            status: Status information

        Returns:
            Exit code (0=healthy, 1=warning, 2=error)
        """
        pass
