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
    handle_course_authoring_rules,
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
    handle_voiceover_backfill_dry,
    handle_voiceover_cache_list,
    handle_voiceover_compare,
    handle_voiceover_identify_rev,
    handle_voiceover_trace_show,
    handle_voiceover_transcribe,
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
        include_disabled: bool = False,
    ) -> str:
        """Generate a structured JSON outline for a course.

        Args:
            spec_file: Path to the course spec file (absolute, or
                relative to the data directory).
            language: Language code ("en" or "de").
            include_disabled: If True, include sections marked
                enabled="false" in the output with a "disabled": true
                marker. Default: disabled sections are omitted.
        """
        return await handle_course_outline(
            spec_file,
            data_dir,
            language=language,
            include_disabled=include_disabled,
        )

    @mcp.tool()
    async def validate_spec(
        course_spec: str,
        include_disabled: bool = False,
    ) -> str:
        """Validate a course specification XML file.

        Checks that all referenced topic IDs resolve to exactly one
        existing topic directory, that there are no duplicate topic
        references, and that referenced dir-group paths exist.

        Args:
            course_spec: Path to the course spec file (absolute, or
                relative to the data directory).
            include_disabled: If True, also validate sections marked
                enabled="false". Each finding from a disabled section
                has "(disabled)" appended to its message. Default:
                disabled sections are dropped at parse time and
                therefore invisible to validation.
        """
        return await handle_validate_spec(course_spec, data_dir, include_disabled=include_disabled)

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

    @mcp.tool()
    async def course_authoring_rules(
        course_spec: str | None = None,
        slide_path: str | None = None,
    ) -> str:
        """Return merged authoring rules for a course or slide file.

        Reads per-course ``.authoring.md`` files from the
        ``course-specs/`` directory and returns merged rules
        (common + course-specific).  Provide at least one of
        ``course_spec`` or ``slide_path``.

        Args:
            course_spec: Course spec path or slug (e.g.,
                ``"machine-learning-azav"``).
            slide_path: Path to a slide file (absolute, or relative to
                the data directory).  Resolves to the course(s) that
                reference the topic containing this file.
        """
        return await handle_course_authoring_rules(
            data_dir,
            course_spec=course_spec,
            slide_path=slide_path,
        )

    @mcp.tool()
    async def voiceover_transcribe(
        video: str,
        lang: str | None = None,
        backend: str = "faster-whisper",
        whisper_model: str = "large-v3",
        device: str = "auto",
        no_cache: bool = False,
        refresh_cache: bool = False,
        cache_root: str | None = None,
    ) -> str:
        """Transcribe a video via the artifact cache and return a summary.

        Reads the cache at ``.clm/voiceover-cache/transcripts/`` first;
        computes + caches on miss.  Returns a JSON summary (segment
        count, duration, first/last segment) — not the full transcript,
        to keep MCP round-trips small.  For the full transcript, call
        ``clm voiceover transcribe`` from the shell.

        Args:
            video: Path to the video (absolute or relative to data_dir).
            lang: Whisper language hint ("de", "en").  Omit for auto.
            backend: "faster-whisper" | "cohere" | "granite".
            whisper_model: Whisper model size (e.g. "large-v3").
            device: "auto" | "cpu" | "cuda".
            no_cache: Disable cache reads (writes still happen).
            refresh_cache: Force recompute + overwrite cache.
            cache_root: Override ``.clm/voiceover-cache`` location.
        """
        return await handle_voiceover_transcribe(
            video,
            data_dir,
            lang=lang,
            backend=backend,
            whisper_model=whisper_model,
            device=device,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )

    @mcp.tool()
    async def voiceover_identify_rev(
        slide_file: str,
        videos: list[str],
        lang: str,
        top: int = 5,
        limit: int = 50,
        since: str | None = None,
        no_cache: bool = False,
        refresh_cache: bool = False,
        cache_root: str | None = None,
    ) -> str:
        """Identify the git revision a recording was made against.

        Builds an OCR fingerprint from keyframe transitions and ranks
        historical commits of ``slide_file`` by fuzzy longest-common-
        subsequence similarity to the fingerprint.  Narrative-heavy
        commit endpoints get a small prior.

        Args:
            slide_file: Path to the slide file.
            videos: One or more video file paths.
            lang: "de" or "en".
            top: Number of top-ranked revisions to return.
            limit: Maximum commits to score (most recent first).
            since: git-log ``--since`` filter (e.g. "6 months ago").
            no_cache / refresh_cache / cache_root: cache controls
                (see ``voiceover_transcribe``).
        """
        return await handle_voiceover_identify_rev(
            slide_file,
            videos,
            data_dir,
            lang=lang,
            top=top,
            limit=limit,
            since=since,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )

    @mcp.tool()
    async def voiceover_compare(
        source: str,
        target: str,
        lang: str,
        model: str | None = None,
        api_base: str | None = None,
    ) -> str:
        """Compare voiceover content between two slide files (read-only).

        For each matched slide pair, the LLM labels every bullet as
        ``covered`` / ``rewritten`` / ``added`` / ``dropped`` /
        ``manual_review``.  Neither file is modified.  ``source`` is
        usually produced by ``clm voiceover sync-at-rev`` against the
        recording's identified revision; ``target`` is the current HEAD.

        Args:
            source: Older slide file (usually from sync-at-rev).
            target: Current slide file.
            lang: "de" or "en".
            model: Override the judge LLM model.
            api_base: Override the LLM API base URL.
        """
        return await handle_voiceover_compare(
            source,
            target,
            data_dir,
            lang=lang,
            model=model,
            api_base=api_base,
        )

    @mcp.tool()
    async def voiceover_backfill_dry(
        slide_file: str,
        videos: list[str],
        lang: str,
        rev: str | None = None,
        auto: bool = True,
        force_rev: bool = False,
        top: int = 5,
        tag: str = "voiceover",
        whisper_model: str = "large-v3",
        backend: str = "faster-whisper",
        device: str = "auto",
        model: str | None = None,
        api_base: str | None = None,
    ) -> str:
        """Preview a backfill: identify-rev → sync-at-rev → port (no writes).

        Runs ``clm voiceover backfill --dry-run`` as a subprocess and
        returns its stdout/stderr plus the unified-diff preview.  The
        working-copy slide file is never mutated; ``--apply`` is
        intentionally CLI-only.

        Args:
            slide_file: Slide file at HEAD.
            videos: Recording video file paths.
            lang: "de" or "en".
            rev: Skip identify-rev and use this SHA directly.
            auto: Pick the top-ranked rev automatically (default true).
            force_rev: Accept the top rev below the confidence threshold.
            top / tag / whisper_model / backend / device / model /
                api_base: passed through to backfill.
        """
        return await handle_voiceover_backfill_dry(
            slide_file,
            videos,
            data_dir,
            lang=lang,
            rev=rev,
            auto=auto,
            force_rev=force_rev,
            top=top,
            tag=tag,
            whisper_model=whisper_model,
            backend=backend,
            device=device,
            model=model,
            api_base=api_base,
        )

    @mcp.tool()
    async def voiceover_cache_list(cache_root: str | None = None) -> str:
        """List entries in the voiceover artifact cache.

        Args:
            cache_root: Override the default ``.clm/voiceover-cache``
                location.  Omit to use the project default.
        """
        return await handle_voiceover_cache_list(data_dir, cache_root=cache_root)

    @mcp.tool()
    async def voiceover_trace_show(path: str) -> str:
        """Read a voiceover trace log and return its entries as JSON.

        Trace logs live under ``.clm/voiceover-traces/*.jsonl`` and
        record every per-slide LLM merge input/output from a ``sync``
        invocation.

        Args:
            path: Path to the trace JSONL file (absolute or relative to
                the data directory).
        """
        return await handle_voiceover_trace_show(path, data_dir)

    return mcp


def run_server(data_dir: Path) -> None:
    """Run the CLM MCP server on stdio transport.

    Args:
        data_dir: Root data directory.
    """
    server = create_server(data_dir)
    server.run(transport="stdio")
