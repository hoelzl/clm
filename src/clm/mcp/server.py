"""MCP server for CLM slide authoring tools.

Uses the ``mcp`` Python SDK with stdio transport. Tool names mirror the
CLI verb-group structure (``topic_resolve``, ``slides_search``,
``slides_normalize``, ``voiceover_extract``, ``validate``, …); the flat
pre-1.8 names (``resolve_topic``, ``validate_slides``, …) were renamed in
CLM 1.8.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from clm.mcp.tools import (
    handle_course_authoring_rules,
    handle_course_context,
    handle_course_outline,
    handle_extract_voiceover,
    handle_get_language_view,
    handle_harvest_backfill_dry,
    handle_harvest_cache_list,
    handle_harvest_compare,
    handle_harvest_identify_rev,
    handle_harvest_report,
    handle_harvest_task,
    handle_harvest_trace_show,
    handle_harvest_transcribe,
    handle_inline_voiceover,
    handle_normalize_slides,
    handle_resolve_topic,
    handle_search_slides,
    handle_suggest_sync,
    handle_sync_report,
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
    async def topic_resolve(
        topic_id: str,
        course_spec: str | None = None,
        module: str | None = None,
    ) -> str:
        """Resolve a topic ID or glob pattern to its filesystem path.

        Args:
            topic_id: Topic identifier (e.g. "what_is_ml") or glob
                pattern (e.g. "what_is_ml*").
            course_spec: Optional path to a course spec file to scope
                resolution to topics referenced by that course.
            module: Optional module directory name (e.g.
                "module_545_ml_azav_cohort_2026_04"). When set,
                resolution is restricted to topics in that module —
                useful when a topic ID exists in multiple modules.
        """
        return await handle_resolve_topic(
            topic_id,
            data_dir,
            course_spec=course_spec,
            module=module,
        )

    @mcp.tool()
    async def slides_search(
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

        The outline is the **section -> source-deck mapping**: a list of
        sections, each with a ``topics`` array whose entries carry the topic
        ``directory`` and a ``slides`` list of ``{file, title}`` (the source
        ``.py`` deck files). Use this instead of parsing the spec XML or
        grepping ``slides/`` to learn which files back each section. (The CLI
        ``clm course decks <spec> --json`` returns the same mapping as a flat,
        build-resolution-accurate ``topics`` array.)

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
    async def course_context(
        spec_file: str,
        language: str = "en",
        level: str = "titles",
        through: str | None = None,
        from_section: str | None = None,
        before: str | None = None,
        upto: str | None = None,
        include_disabled: bool = False,
        model: str | None = None,
        no_cache: bool = False,
    ) -> str:
        """Course context for an LLM authoring or revising material.

        Returns a "what has been taught up to here" view scoped to a cut
        point, so an assistant writing later material can reference prior
        topics and avoid re-teaching them. Three depths via ``level``.

        Args:
            spec_file: Path to the course spec file (absolute, or relative
                to the data directory).
            language: Language code ("en" or "de").
            level: "titles" (structure only, no LLM — the default, so a
                call never silently triggers a paid LLM request),
                "summary" (per-topic LLM summaries, cached), or "full"
                (raw extracted markdown+code, no LLM).
            through: Include sections up to and including this one (1-based
                number or section id). Mutually exclusive with before/upto.
            from_section: Start the section window here (pairs with through).
            before: Include everything authored strictly before this topic id.
            upto: Include everything up to and including this topic id.
            include_disabled: Include sections marked enabled="false", tagged
                with "disabled": true.
            model: Override the LLM model identifier (level="summary" only).
            no_cache: Skip the summary cache (level="summary" only).
        """
        return await handle_course_context(
            spec_file,
            data_dir,
            language=language,
            level=level,
            through=through,
            from_section=from_section,
            before=before,
            upto=upto,
            include_disabled=include_disabled,
            model=model,
            no_cache=no_cache,
        )

    @mcp.tool()
    async def validate(
        path: str,
        kind: str | None = None,
        checks: list[str] | None = None,
        include_disabled: bool = False,
    ) -> str:
        """Validate a course spec file or slide files.

        Mirrors the unified ``clm validate`` CLI command. Dispatches by
        input type: an ``.xml`` path is validated as a course spec; a
        ``.py`` file or a directory is validated as slides. Override the
        inference with ``kind="spec"`` or ``kind="slides"`` for ambiguous
        cases (e.g. an ``.xml`` you nonetheless want slide-validated).

        Spec validation checks that all referenced topic IDs resolve to
        exactly one existing topic directory, that there are no duplicate
        topic references, and that referenced dir-group paths exist.
        Slide validation runs deterministic checks (format, pairing,
        tags) and extracts structured review_material for content-quality
        checks that require LLM judgment.

        Args:
            path: Path to a course spec (.xml), slide file (.py), or
                topic directory (absolute, or relative to the data
                directory).
            kind: Force a validator: "spec" or "slides". Default
                inference: .xml → spec, .py / directory → slides.
            checks: Slides-only. Which checks to run. Deterministic:
                format, pairing, tags. Review: code_quality, voiceover,
                completeness. Default: all checks except ``voiceover``
                (opt-in per deck, issue #176) — pass
                ``checks=["voiceover"]`` to run it. Ignored for spec
                validation.
            include_disabled: Spec-only. If True, also validate sections
                marked enabled="false"; each finding from a disabled
                section has "(disabled)" appended. Ignored for slide
                validation.
        """
        resolved_kind = (kind or "").lower()
        if resolved_kind not in ("spec", "slides"):
            resolved_kind = "spec" if path.lower().endswith(".xml") else "slides"
        if resolved_kind == "spec":
            return await handle_validate_spec(path, data_dir, include_disabled=include_disabled)
        return await handle_validate_slides(path, data_dir, checks=checks)

    @mcp.tool()
    async def slides_normalize(
        path: str,
        operations: list[str] | None = None,
        dry_run: bool = False,
        canonicalize_start_completed: bool = False,
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
            canonicalize_start_completed: Force start/completed cohesion
                pairs into the canonical DE/EN interleave (even when DE/EN
                code differs) so a subsequent split/unify round-trips
                byte-for-byte. Only affects the interleaving operation.
        """
        return await handle_normalize_slides(
            path,
            data_dir,
            operations=operations,
            dry_run=dry_run,
            canonicalize_start_completed=canonicalize_start_completed,
        )

    @mcp.tool()
    async def slides_language_view(
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
    async def slides_suggest_sync(
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
    async def slides_sync_report(file: str) -> str:
        """Tiered reconciliation report for a split DE/EN deck pair (the agent contract).

        Runs the same deterministic engine as ``clm slides sync --dry-run`` on a
        *split* pair (``<deck>.de.<ext>`` + ``<deck>.en.<ext>``) and returns its work
        partitioned into the three tiers an agent acts on differently:

        - ``mechanical`` — the engine applies these deterministically with no model
          (move / remove / retag / a verbatim neutral-cell propagation); trust them.
        - ``assisted`` — a scoped model task the engine has framed (translate a new
          slide, reconcile an id'd-cell edit, confirm a cold-pair correspondence);
          each item carries the source (and, for an edit, target) cell bytes so you
          can act without re-reading the file.
        - ``ambiguity`` — the engine refuses to guess (a both-sided conflict, a
          structural issue); your judgement, stated as *what* is ambiguous.

        The block also exposes ``is_clean`` / ``needs_model`` / ``needs_agent``.
        Read-only: nothing is written and no model is called. This is the split-pair
        analogue of ``slides_suggest_sync`` (which targets a single bilingual file).

        Args:
            file: A deck half (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``) or the
                bilingual deck stem (``<deck>.<ext>``), absolute or relative to the
                data directory; the twin / both halves are derived from disk.
        """
        return await handle_sync_report(file, data_dir)

    @mcp.tool()
    async def voiceover_extract(
        file: str,
        force: bool = False,
        dry_run: bool = False,
        both: bool = False,
        single: bool = False,
    ) -> str:
        """Extract voiceover cells from a slide file to a companion file.

        Moves voiceover and notes cells to a companion voiceover_*.py
        file, linked via slide_id/for_slide metadata.  Content cells
        without slide_id get auto-generated IDs before extraction.
        Refuses to overwrite an existing companion unless force is set
        (returns an ``{"error": ...}`` object in that case).

        On a split half (<deck>.de.py / <deck>.en.py) whose twin exists on
        disk, BOTH companions are extracted in one EN-authority paired op by
        default (the result carries ``"paired": true``). Pass single=True to
        extract only this half; both=True forces the paired form.

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            force: Overwrite an existing companion (rebuilds it from the
                slide's voiceover cells, discarding companion-only content).
            dry_run: If True, preview without writing files.
            both: Force the paired extract (both companions of a split deck).
            single: Extract only this file's companion, even on a split half.
        """
        return await handle_extract_voiceover(
            file, data_dir, force=force, dry_run=dry_run, both=both, single=single
        )

    @mcp.tool()
    async def voiceover_inline(
        file: str,
        dry_run: bool = False,
    ) -> str:
        """Inline voiceover cells from a companion file back into a slide file.

        Merges voiceover cells from the companion voiceover_*.py file
        back into the slide file, matching via for_slide/slide_id
        metadata.  Deletes the companion only when every cell is placed;
        if any cell is unmatched the companion is kept with the leftovers
        (see ``companion_retained`` / ``unmatched_cells`` in the result).

        Args:
            file: Path to the slide file (absolute, or relative to the
                data directory).
            dry_run: If True, preview without modifying files.
        """
        return await handle_inline_voiceover(file, data_dir, dry_run=dry_run)

    @mcp.tool()
    async def authoring_rules(
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
    async def harvest_transcribe(
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
        ``clm harvest transcribe`` from the shell.

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
        return await handle_harvest_transcribe(
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
    async def harvest_identify_rev(
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
                (see ``harvest_transcribe``).
        """
        return await handle_harvest_identify_rev(
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
    async def harvest_compare(
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
        usually produced by ``clm harvest sync-at-rev`` against the
        recording's identified revision; ``target`` is the current HEAD.

        Args:
            source: Older slide file (usually from sync-at-rev).
            target: Current slide file.
            lang: "de" or "en".
            model: Override the judge LLM model.
            api_base: Override the LLM API base URL.
        """
        return await handle_harvest_compare(
            source,
            target,
            data_dir,
            lang=lang,
            model=model,
            api_base=api_base,
        )

    @mcp.tool()
    async def harvest_backfill_dry(
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

        Runs ``clm harvest backfill --dry-run`` as a subprocess and
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
        return await handle_harvest_backfill_dry(
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
    async def harvest_cache_list(cache_root: str | None = None) -> str:
        """List entries in the voiceover artifact cache.

        Args:
            cache_root: Override the default ``.clm/voiceover-cache``
                location.  Omit to use the project default.
        """
        return await handle_harvest_cache_list(data_dir, cache_root=cache_root)

    @mcp.tool()
    async def harvest_trace_show(path: str) -> str:
        """Read a voiceover trace log and return its entries as JSON.

        Trace logs live under ``.clm/voiceover-traces/*.jsonl`` and
        record every per-slide LLM merge input/output from a ``sync``
        invocation.

        Args:
            path: Path to the trace JSONL file (absolute or relative to
                the data directory).
        """
        return await handle_harvest_trace_show(path, data_dir)

    @mcp.tool()
    async def harvest_report(
        slides: str,
        videos: list[str],
        lang: str,
        transcript: str | None = None,
        alignment: str | None = None,
        whisper_model: str = "large-v3",
        backend: str = "faster-whisper",
        device: str = "auto",
        no_cache: bool = False,
        refresh_cache: bool = False,
        cache_root: str | None = None,
    ) -> str:
        """What did the recording say, slide by slide? (read-only)

        The MCP twin of ``clm harvest report --json``: runs the cached
        deterministic tier (transcribe → detect transitions → OCR-match →
        align) over the videos and joins the result with the v3 deck
        bundle — one item per slide, keyed ``id:<slide_id>``, with the
        aligned transcript, the voiceover baseline on both language sides,
        and a structural novelty class (no_existing_vo |
        transcript_adds_material | covered | unmatched_slide), plus
        unmatched_speech per unassigned segment. No model, no key, no
        writes.

        Args:
            slides: The recorded-language deck half (absolute or relative
                to the data directory).
            videos: Recording video file paths.
            lang: The recorded (spoken) language ("de" or "en").
            transcript: Skip ASR: load a precomputed transcript JSON
                (from ``clm harvest transcribe -o``). Single-video only.
            alignment: Skip ASR, detection, and matching: load a
                precomputed alignment JSON (cache artifact shape).
                Single-video only.
            whisper_model / backend / device: ASR knobs.
            no_cache / refresh_cache / cache_root: cache controls
                (see ``harvest_transcribe``).
        """
        return await handle_harvest_report(
            slides,
            videos,
            data_dir,
            lang=lang,
            transcript=transcript,
            alignment=alignment,
            whisper_model=whisper_model,
            backend=backend,
            device=device,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )

    @mcp.tool()
    async def harvest_task(
        slides: str,
        videos: list[str],
        lang: str,
        slide: str | None = None,
        kind: str = "curate",
        transcript: str | None = None,
        alignment: str | None = None,
        whisper_model: str = "large-v3",
        backend: str = "faster-whisper",
        device: str = "auto",
        no_cache: bool = False,
        refresh_cache: bool = False,
        cache_root: str | None = None,
    ) -> str:
        """Frame slide judgment tasks for the driving agent (read-only).

        The MCP twin of ``clm harvest task``: assembles the same report as
        ``harvest_report``, then frames per-slide curation/translation
        judgment as task documents — caller instructions, structured
        inputs (baseline voiceover on both sides, the aligned transcript,
        slide content), the bullet-list ``answer_schema``, and the
        freshness tokens (``baseline_fingerprints``,
        ``video_fingerprint``). Read-only: writes go through
        ``clm harvest accept`` on the CLI — by design there is no MCP
        write path.

        Args:
            slides: The recorded-language deck half (absolute or relative
                to the data directory).
            videos: Recording video file paths.
            lang: The recorded (spoken) language ("de" or "en").
            slide: Frame one slide (bare id or ``id:...`` handle). Omit
                to frame every actionable item.
            kind: "curate" (merge the recorded language) or "translate"
                (frame the twin side).
            transcript / alignment: precomputed-input overrides (see
                ``harvest_report``).
            whisper_model / backend / device: ASR knobs.
            no_cache / refresh_cache / cache_root: cache controls
                (see ``harvest_transcribe``).
        """
        return await handle_harvest_task(
            slides,
            videos,
            data_dir,
            lang=lang,
            slide=slide,
            kind=kind,
            transcript=transcript,
            alignment=alignment,
            whisper_model=whisper_model,
            backend=backend,
            device=device,
            no_cache=no_cache,
            refresh_cache=refresh_cache,
            cache_root=cache_root,
        )

    return mcp


def run_server(data_dir: Path) -> None:
    """Run the CLM MCP server on stdio transport.

    Args:
        data_dir: Root data directory.
    """
    server = create_server(data_dir)
    server.run(transport="stdio")
