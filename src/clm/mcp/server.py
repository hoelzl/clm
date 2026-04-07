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
    handle_extract_voiceover,
    handle_get_language_view,
    handle_inline_voiceover,
    handle_normalize_slides,
    handle_resolve_topic,
    handle_search_slides,
    handle_suggest_sync,
    handle_validate_slides,
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

    @mcp.tool()
    async def validate_slides(
        path: str,
        checks: list[str] | None = None,
    ) -> str:
        """Validate slide files for format, tag, and pairing correctness.

        Runs deterministic checks (format, pairing, tags) and extracts
        structured review_material for content-quality checks that
        require LLM judgment.  Call this after completing edits to a
        slide file.

        Args:
            path: Path to a slide file, topic directory, or course spec
                XML (absolute, or relative to the data directory).
            checks: Which checks to run.  Deterministic: format, pairing,
                tags.  Review: code_quality, voiceover, completeness.
                Default: all.
        """
        return await handle_validate_slides(path, data_dir, checks=checks)

    @mcp.tool()
    async def normalize_slides(
        path: str,
        operations: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """Normalize slide files by applying mechanical fixes.

        Applies tag migration (alt->completed after start cells),
        workshop tag insertion, and interleaving normalization.
        Use dry_run=True to preview changes without modifying files.

        Args:
            path: Path to a slide file, topic directory, or course spec
                XML (absolute, or relative to the data directory).
            operations: Which operations to apply: tag_migration,
                workshop_tags, interleaving, all.  Default: all.
            dry_run: If True, preview changes without modifying files.
        """
        return await handle_normalize_slides(path, data_dir, operations=operations, dry_run=dry_run)

    @mcp.tool()
    async def get_language_view(
        file: str,
        language: str,
        include_voiceover: bool = False,
        include_notes: bool = False,
    ) -> str:
        """Extract a single-language view of a bilingual slide file.

        Returns the file content with only cells for the specified
        language (plus language-independent cells).  Each cell is
        preceded by an ``[original line N]`` annotation so edits can
        be mapped back to the bilateral file.

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            language: Which language to extract ("de" or "en").
            include_voiceover: Include voiceover cells (default false).
            include_notes: Include speaker-notes cells (default false).
        """
        return await handle_get_language_view(
            file,
            data_dir,
            language=language,
            include_voiceover=include_voiceover,
            include_notes=include_notes,
        )

    @mcp.tool()
    async def suggest_sync(
        file: str,
        source_language: str | None = None,
    ) -> str:
        """Compare a slide file against git HEAD and suggest sync updates.

        Detects cells modified in one language without corresponding
        changes in the other language.  Uses slide_id metadata for
        precise DE/EN pairing when available; falls back to positional
        pairing.  Does NOT modify the file.

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            source_language: The language that was edited ("de" or "en").
                If omitted, auto-detects which language has more changes.
        """
        return await handle_suggest_sync(file, data_dir, source_language=source_language)

    @mcp.tool()
    async def extract_voiceover(
        file: str,
        dry_run: bool = False,
    ) -> str:
        """Extract voiceover cells from a slide file to a companion file.

        Moves voiceover and notes cells to a companion voiceover_*.py
        file, linked via slide_id/for_slide metadata.  Content cells
        without slide_id get auto-generated IDs before extraction.

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            dry_run: If True, preview without writing files.
        """
        return await handle_extract_voiceover(file, data_dir, dry_run=dry_run)

    @mcp.tool()
    async def inline_voiceover(
        file: str,
        dry_run: bool = False,
    ) -> str:
        """Inline voiceover cells from a companion file back into a slide file.

        Merges voiceover cells from the companion voiceover_*.py file
        back into the slide file, matching via for_slide/slide_id
        metadata.  Deletes the companion file after successful inlining.

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            dry_run: If True, preview without modifying files.
        """
        return await handle_inline_voiceover(file, data_dir, dry_run=dry_run)

    return mcp


def run_server(data_dir: Path) -> None:
    """Run the CLM MCP server on stdio transport.

    Args:
        data_dir: Root data directory.
    """
    server = create_server(data_dir)
    server.run(transport="stdio")
