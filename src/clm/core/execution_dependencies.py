"""Execution dependency resolution for multi-target output.

This module handles the execution dependencies between different output types.
For example, `completed` HTML reuses cached execution results from `speaker` HTML.
When a user requests only `completed` HTML, the system must still run `speaker` HTML
to populate the cache.

The abstraction makes these dependencies explicit and extensible.
"""

import logging
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clm.core.output_target import OutputTarget

logger = logging.getLogger(__name__)


class ExecutionRequirement(Enum):
    """Categorizes outputs by their notebook execution requirements.

    This abstraction makes explicit which outputs need execution and which
    can reuse cached results. It ensures the system correctly handles cases
    where only cache-consumers are requested (e.g., only 'completed' HTML).
    """

    # No execution needed - cells are cleared or content is static
    NONE = auto()

    # Produces execution results and populates the cache
    # Must run before REUSES_CACHE outputs
    POPULATES_CACHE = auto()

    # Consumes cached execution results
    # Requires POPULATES_CACHE to have run (explicitly or implicitly)
    REUSES_CACHE = auto()


# Output classification by execution requirement
# Key: (format, kind) -> ExecutionRequirement
EXECUTION_REQUIREMENTS: dict[tuple[str, str], ExecutionRequirement] = {
    # Code-along: cells are cleared, no execution needed
    ("html", "code-along"): ExecutionRequirement.NONE,
    ("notebook", "code-along"): ExecutionRequirement.NONE,
    ("code", "code-along"): ExecutionRequirement.NONE,
    # Speaker: executes and caches (for HTML)
    ("html", "speaker"): ExecutionRequirement.POPULATES_CACHE,
    ("notebook", "speaker"): ExecutionRequirement.NONE,  # Just filtered, no execution
    ("code", "speaker"): ExecutionRequirement.NONE,
    # Completed: reuses cache (for HTML), no execution for others
    ("html", "completed"): ExecutionRequirement.REUSES_CACHE,
    ("notebook", "completed"): ExecutionRequirement.NONE,
    ("code", "completed"): ExecutionRequirement.NONE,
}


def get_execution_requirement(format_: str, kind: str) -> ExecutionRequirement:
    """Get the execution requirement for a format/kind combination.

    Args:
        format_: Output format (e.g., "html", "notebook", "code")
        kind: Output kind (e.g., "code-along", "completed", "speaker")

    Returns:
        The ExecutionRequirement for this combination
    """
    return EXECUTION_REQUIREMENTS.get((format_, kind), ExecutionRequirement.NONE)


class ExecutionDependencyResolver:
    """Ensures execution dependencies are satisfied across targets.

    If any target requests an output that REUSES_CACHE, this resolver
    ensures that a corresponding POPULATES_CACHE operation runs first,
    even if no target explicitly requests it.
    """

    # Maps cache-consuming outputs to their cache-producing counterparts
    CACHE_PROVIDERS: dict[tuple[str, str], tuple[str, str]] = {
        # (consumer_format, consumer_kind) -> (provider_format, provider_kind)
        ("html", "completed"): ("html", "speaker"),
    }

    def resolve_implicit_executions(
        self,
        requested_outputs: set[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        """Determine implicit executions needed to satisfy dependencies.

        When a user requests outputs that REUSE_CACHE but doesn't request
        the corresponding POPULATES_CACHE outputs, this method identifies
        which additional outputs must be executed (but not written to disk)
        to populate the cache.

        Args:
            requested_outputs: Set of (language, format, kind) tuples
                              that were explicitly requested

        Returns:
            Set of additional (language, format, kind) tuples that must
            be executed to populate the cache, but whose outputs should
            not be written to disk unless also explicitly requested.
        """
        implicit_executions: set[tuple[str, str, str]] = set()

        for lang, fmt, kind in requested_outputs:
            req = get_execution_requirement(fmt, kind)

            if req == ExecutionRequirement.REUSES_CACHE:
                # Check if a cache provider is already requested
                provider = self.CACHE_PROVIDERS.get((fmt, kind))
                if provider:
                    provider_fmt, provider_kind = provider
                    provider_output = (lang, provider_fmt, provider_kind)

                    if provider_output not in requested_outputs:
                        # Need implicit execution
                        implicit_executions.add(provider_output)
                        logger.info(
                            f"Adding implicit execution for {provider_output} "
                            f"to satisfy cache dependency of ({lang}, {fmt}, {kind})"
                        )

        return implicit_executions

    def collect_requested_outputs(
        self,
        targets: list["OutputTarget"],
    ) -> set[tuple[str, str, str]]:
        """Collect all (lang, format, kind) tuples requested by all targets.

        Args:
            targets: List of output targets to analyze

        Returns:
            Set of all (language, format, kind) combinations requested
        """
        requested: set[tuple[str, str, str]] = set()
        for target in targets:
            for lang in target.languages:
                for fmt in target.formats:
                    for kind in target.kinds:
                        if target.should_generate(lang, fmt, kind):
                            requested.add((lang, fmt, kind))
        return requested

    def get_all_required_executions(
        self,
        targets: list["OutputTarget"],
    ) -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]]]:
        """Get both explicit and implicit execution requirements.

        Args:
            targets: List of output targets to analyze

        Returns:
            Tuple of (explicit_outputs, implicit_executions) where:
            - explicit_outputs: What the user requested (write to disk)
            - implicit_executions: Additional executions needed for cache
              (execute but don't write unless also explicit)
        """
        explicit = self.collect_requested_outputs(targets)
        implicit = self.resolve_implicit_executions(explicit)
        return explicit, implicit
