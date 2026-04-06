"""MCP server for CLM slide authoring tools.

Uses the ``mcp`` Python SDK with stdio transport.  The server
exposes ``resolve_topic``, ``search_slides``, and ``course_outline``
as MCP tools.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from clm.mcp.tools import (
    handle_course_outline,
    handle_resolve_topic,
    handle_search_slides,
    handle_validate_spec,
)

logger = logging.getLogger(__name__)


def create_server(data_dir: Path) -> FastMCP:
    """Create and configure the CLM MCP server.

    Args:
        data_dir: Root data directory (contains ``slides/``,
            ``course-specs/``, etc.).

    Returns:
        A configured :class:`FastMCP` instance ready to run.
    """
    mcp = FastMCP("clm")

    @mcp.tool()
    async def resolve_topic(
        topic_id: str,
        course_spec: str | None = None,
    ) -> str:
        """Resolve a topic ID or glob pattern to its filesystem path.

        Args:
            topic_id: Topic identifier (e.g. "what_is_ml") or glob
                pattern (e.g. "what_is_ml*").
            course_spec: Optional path to a course spec file to scope
                resolution to topics referenced by that course.
        """
        return await handle_resolve_topic(topic_id, data_dir, course_spec=course_spec)

    @mcp.tool()
    async def search_slides(
        query: str,
        course_spec: str | None = None,
        language: str | None = None,
        max_results: int = 10,
    ) -> str:
        """Fuzzy search across topic names and slide file titles.

        Args:
            query: Search query (e.g. "decorators", "RAG introduction").
            course_spec: Optional course spec path to limit search scope.
            language: Search titles in this language only ("de" or "en").
            max_results: Maximum number of results to return.
        """
        return await handle_search_slides(
            query,
            data_dir,
            course_spec=course_spec,
            language=language,
            max_results=max_results,
        )

    @mcp.tool()
    async def course_outline(
        spec_file: str,
        language: str = "en",
    ) -> str:
        """Generate a structured JSON outline for a course.

        Args:
            spec_file: Path to the course spec file (absolute, or
                relative to the data directory).
            language: Language code ("en" or "de").
        """
        return await handle_course_outline(spec_file, data_dir, language=language)

    @mcp.tool()
    async def validate_spec(
        course_spec: str,
    ) -> str:
        """Validate a course specification XML file.

        Checks that all referenced topic IDs resolve to exactly one
        existing topic directory, that there are no duplicate topic
        references, and that referenced dir-group paths exist.

        Args:
            course_spec: Path to the course spec file (absolute, or
                relative to the data directory).
        """
        return await handle_validate_spec(course_spec, data_dir)

    return mcp


def run_server(data_dir: Path) -> None:
    """Run the CLM MCP server on stdio transport.

    Args:
        data_dir: Root data directory.
    """
    server = create_server(data_dir)
    server.run(transport="stdio")
