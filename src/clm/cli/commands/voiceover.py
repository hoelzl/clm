"""Voiceover command for synchronizing video recordings with slide speaker notes.

This module provides the ``clm voiceover`` command group with subcommands
for the full sync pipeline and individual diagnostic steps.

Requires the ``[voiceover]`` extra.
"""

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


@click.group("voiceover")
@click.option(
    "--cache-root",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the cache location (default: ./.clm/voiceover-cache).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Disable the artifact cache for this invocation.",
)
@click.option(
    "--refresh-cache",
    is_flag=True,
    default=False,
    help="Force recomputation and overwrite existing cache entries.",
)
@click.pass_context
def voiceover_group(ctx, cache_root, no_cache, refresh_cache):
    """Video-to-speaker-notes synchronization.

    Transcribe a video recording and align the transcript to slides,
    then insert or update voiceover/notes cells in the .py slide file.

    Requires: pip install clm[voiceover]
    """
    from clm.voiceover.cache import CachePolicy

    ctx.ensure_object(dict)
    ctx.obj["cache_policy"] = CachePolicy(
        enabled=not no_cache,
        refresh=refresh_cache,
        cache_root=cache_root,
    )


@voiceover_group.command()
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Video language.")
@click.option(
    "--mode",
    default="polished",
    type=click.Choice(["verbatim", "polished"]),
    help="Polished (default) runs LLM cleanup; verbatim keeps transcript as-is.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite existing voiceover cells instead of merging (old behavior).",
)
@click.option("--whisper-model", default="large-v3", help="Whisper model size.")
@click.option(
    "--backend",
    "backend_name",
    default="faster-whisper",
    type=click.Choice(["faster-whisper", "cohere", "granite"]),
    help="Transcription backend.",
)
@click.option(
    "--device",
    default="auto",
    type=click.Choice(["auto", "cpu", "cuda"]),
    help="Device for transcription: auto (default), cpu, or cuda.",
)
@click.option(
    "--tag",
    default="voiceover",
    help="Cell tag for inserted cells: 'voiceover' (default) or 'notes'.",
)
@click.option("--slides-range", default=None, help="Slide range to update (e.g. '5-20').")
@click.option("--dry-run", is_flag=True, help="Show mapping without writing changes.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
@click.option("--keep-audio", is_flag=True, help="Keep extracted audio files.")
@click.option("--model", default=None, help="LLM model for polished/merge mode.")
@click.option(
    "--transcript",
    "transcript_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Skip ASR and load a precomputed transcript from PATH (JSON produced "
        "by `clm voiceover transcribe -o ...`). Must be a single-part JSON; "
        "combine with a single VIDEO argument."
    ),
)
@click.option(
    "--alignment",
    "alignment_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Skip ASR, detection, and matching; load a precomputed alignment "
        "from PATH. The alignment JSON is produced by a prior sync run "
        "(cached under .clm/voiceover-cache/alignments/)."
    ),
)
@click.pass_context
def sync(
    ctx,
    slides,
    videos,
    lang,
    mode,
    overwrite,
    whisper_model,
    backend_name,
    device,
    tag,
    slides_range,
    dry_run,
    output,
    keep_audio,
    model,
    transcript_override,
    alignment_override,
):
    """Synchronize speaker notes from one or more video recordings.

    Transcribes each VIDEO part, detects slide transitions, matches them
    to SLIDES, and merges voiceover cells in the .py file (preserving
    existing content). Use --overwrite to replace instead of merge.

    Multiple video parts are processed independently and merged into a
    single timeline using running offsets. Part ordering is authoritative
    -- pass parts in the order they should be stitched.

    \b
    Examples:
        clm voiceover sync slides.py video.mp4 --lang de
        clm voiceover sync slides.py "Teil 1.mp4" "Teil 2.mp4" --lang de
        clm voiceover sync slides.py video.mp4 --lang de --overwrite
    """
    # Validate: --mode verbatim without --overwrite is an error
    if mode == "verbatim" and not overwrite:
        raise click.UsageError(
            "Cannot use --mode verbatim with merge (the default). "
            "Verbatim mode has no noise filter, so merging raw transcript "
            "into existing voiceover would be unsafe. "
            "Use --overwrite to replace existing voiceover cells, or "
            "use --mode polished (the default) for merge."
        )

    from clm.notebooks.slide_parser import parse_slides
    from clm.notebooks.slide_writer import write_narrative
    from clm.voiceover.aligner import align_transcript
    from clm.voiceover.cache import (
        CachePolicy,
        cached_alignment,
        cached_detect,
        cached_timeline,
        cached_transcribe,
    )
    from clm.voiceover.keyframes import TransitionEvent, detect_transitions
    from clm.voiceover.matcher import match_events_to_slides
    from clm.voiceover.timeline import (
        build_parts,
        merge_transcripts,
        offset_events,
        offset_transcript,
    )
    from clm.voiceover.transcribe import transcribe_video

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())

    video_paths = [Path(v) for v in videos]
    multi_part = len(video_paths) > 1

    # Parse slides
    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)
    console.print(f"  Found {len(slide_groups)} slide groups")

    # --alignment short-circuits everything before the merge step.
    if alignment_override is not None:
        if multi_part:
            raise click.UsageError(
                "--alignment is incompatible with multi-part videos; "
                "the override encodes a single pre-computed alignment."
            )
        console.print(f"[bold]Loading alignment:[/bold] {alignment_override}")
        alignment = _load_alignment_override(alignment_override)
    else:
        # Probe durations and build parts
        if multi_part:
            console.print(f"[bold]Probing {len(video_paths)} video parts...[/bold]")
        parts = build_parts(video_paths)
        total_duration = sum(p.duration for p in parts)
        if multi_part:
            for part in parts:
                console.print(
                    f"  Part {part.index}: {part.path.name} "
                    f"({part.duration:.0f}s, offset={part.offset:.0f}s)"
                )
            console.print(f"  Total duration: {total_duration:.0f}s")

        if transcript_override is not None and multi_part:
            raise click.UsageError(
                "--transcript is incompatible with multi-part videos; "
                "supply a precomputed single-part transcript only."
            )

        # Per-part transcription and transition detection
        all_transcripts = []
        all_events: list[TransitionEvent] = []

        for part in parts:
            part_label = f" (part {part.index})" if multi_part else ""
            if transcript_override is not None:
                console.print(f"[bold]Loading transcript:[/bold] {transcript_override}")
                transcript = _load_transcript_override(transcript_override)
            else:
                console.print(
                    f"[bold]Transcribing{part_label}:[/bold] {part.path.name} "
                    f"(backend={backend_name}, device={device})"
                )

                def _do_transcribe(part=part):
                    return transcribe_video(
                        part.path,
                        language=lang,
                        backend_name=backend_name,
                        model_size=whisper_model,
                        device=device,
                        keep_audio=keep_audio,
                    )

                transcript, tx_hit = cached_transcribe(
                    part.path,
                    policy=policy,
                    base_dir=slides.parent,
                    transcribe_fn=_do_transcribe,
                    backend_name=backend_name,
                    model_size=whisper_model,
                    language=lang,
                    device=device,
                )
                if tx_hit:
                    console.print("  [dim]transcript: cache hit[/dim]")
            console.print(
                f"  {len(transcript.segments)} segments, "
                f"{transcript.duration:.0f}s, language={transcript.language}"
            )

            console.print(f"[bold]Detecting slide transitions{part_label}...[/bold]")

            def _do_detect(part=part):
                return detect_transitions(part.path)[0]

            events, det_hit = cached_detect(
                part.path,
                policy=policy,
                base_dir=slides.parent,
                detect_fn=_do_detect,
            )
            if det_hit:
                console.print("  [dim]transitions: cache hit[/dim]")
            console.print(f"  {len(events)} transitions detected")

            # Apply offsets and tag with part index
            all_transcripts.append(offset_transcript(transcript, part))
            all_events.extend(offset_events(events, part))

        # Merge transcripts
        merged_transcript = merge_transcripts(all_transcripts)
        if multi_part:
            console.print(
                f"[bold]Merged:[/bold] {len(merged_transcript.segments)} segments, "
                f"{merged_transcript.duration:.0f}s total"
            )

        # Match to slides (cache scoped to single-part runs only; multi-part
        # timelines stitch across several videos and need a composite key we
        # don't yet model).
        console.print("[bold]Matching transitions to slides...[/bold]")
        timeline_cfg = {
            "lang": lang,
            "frame_offset": 1.0,
            "multi_part": multi_part,
        }

        def _run_match():
            return match_events_to_slides(
                all_events,
                slide_groups,
                video_paths[0],
                video_paths=video_paths if multi_part else None,
                total_duration=total_duration,
                lang=lang,
            ).timeline

        if multi_part:
            timeline = _run_match()
        else:
            timeline, tl_hit = cached_timeline(
                video_paths[0],
                slides,
                policy=policy,
                base_dir=slides.parent,
                timeline_fn=_run_match,
                cfg=timeline_cfg,
            )
            if tl_hit:
                console.print("  [dim]timeline: cache hit[/dim]")
        console.print(f"  {len(timeline)} timeline entries")

        # Align transcript to slides
        console.print("[bold]Aligning transcript to slides...[/bold]")
        alignment_cfg = {"lang": lang, "multi_part": multi_part}

        def _run_align():
            return align_transcript(merged_transcript, timeline)

        if multi_part:
            alignment = _run_align()
        else:
            alignment, al_hit = cached_alignment(
                video_paths[0],
                slides,
                policy=policy,
                base_dir=slides.parent,
                alignment_fn=_run_align,
                cfg=alignment_cfg,
            )
            if al_hit:
                console.print("  [dim]alignment: cache hit[/dim]")

    # Apply slide range filter
    slide_indices = set(alignment.slide_notes.keys())
    if slides_range:
        start, end = _parse_range(slides_range)
        slide_indices = {i for i in slide_indices if start <= i <= end}

    # Build notes map (raw transcript text per slide)
    notes_map: dict[int, str] = {}
    for idx in sorted(slide_indices):
        text = alignment.get_notes_text(idx)
        if text:
            notes_map[idx] = text

    if overwrite:
        # Old behavior: polish or verbatim, then overwrite
        if mode == "polished" and notes_map:
            import asyncio

            console.print("[bold]Polishing notes with LLM...[/bold]")
            notes_map = asyncio.run(_polish_notes(notes_map, slide_groups, model=model, lang=lang))

        _display_notes_summary(notes_map, slide_groups)

        if dry_run:
            console.print("\n[yellow]Dry run — no changes written.[/yellow]")
            return

        dest = write_narrative(slides, notes_map, lang, tag=tag, output_path=output)
        console.print(f"\n[green]{tag.capitalize()} cells written to {dest}[/green]")
    else:
        # Merge mode (default): merge transcript into existing voiceover
        import asyncio

        asyncio.run(
            _merge_notes(
                slides=slides,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang=lang,
                tag=tag,
                model=model,
                dry_run=dry_run,
                output=output,
                multi_part=multi_part,
                alignment=alignment,
            )
        )


async def _merge_notes(
    *,
    slides: Path,
    notes_map: dict[int, str],
    slide_groups: list,
    lang: str,
    tag: str,
    model: str | None,
    dry_run: bool,
    output: Path | None,
    multi_part: bool,
    alignment,
) -> None:
    """Merge transcript into existing voiceover cells."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from clm.infrastructure.llm.client import (
        _langfuse_configured,
        flush_langfuse,
    )
    from clm.notebooks.slide_writer import write_narrative
    from clm.voiceover.merge import (
        DEFAULT_MERGE_MODEL,
        MergeResult,
        SlideInput,
        build_batches,
        merge_batch,
    )
    from clm.voiceover.trace_log import TraceLog

    merge_model = model or DEFAULT_MERGE_MODEL

    # Read existing voiceover baseline per slide from the parsed slide groups
    slide_inputs: list[SlideInput] = []
    for idx in sorted(notes_map.keys()):
        transcript_text = notes_map[idx]

        # Find the slide group and read its existing notes
        baseline = ""
        slide_content = ""
        for sg in slide_groups:
            if sg.index == idx:
                slide_content = sg.text_content
                # Read existing notes matching the target tag
                baseline = _extract_baseline(sg, tag)
                break

        # Detect boundary hint: slide has segments from multiple parts
        boundary_hint = False
        if multi_part and idx in alignment.slide_notes:
            boundary_hint = _has_boundary(alignment, idx)

        slide_id = f"{slides.stem}/{idx}"

        # Skip slides where both baseline and transcript are empty
        if not baseline.strip() and not transcript_text.strip():
            continue

        slide_inputs.append(
            SlideInput(
                slide_id=slide_id,
                baseline=baseline,
                transcript=transcript_text,
                slide_content=slide_content,
                boundary_hint=boundary_hint,
            )
        )

    if not slide_inputs:
        console.print("[yellow]No slides to merge.[/yellow]")
        return

    # Create trace log
    trace = TraceLog.create(slides.name, base_dir=slides.parent)

    # Build batches and run merge
    batches = build_batches(slide_inputs)
    console.print(
        f"[bold]Merging {len(slide_inputs)} slides "
        f"({len(batches)} batch{'es' if len(batches) != 1 else ''}) "
        f"with {merge_model}...[/bold]"
    )

    # Langfuse session context (shared across all batches in this invocation)
    use_langfuse = _langfuse_configured()
    session_id = (
        f"voiceover-sync-{slides.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    )
    git_user = _get_git_user_name() if use_langfuse else None

    all_results: list[MergeResult] = []
    for batch_idx, batch in enumerate(batches):
        if len(batches) > 1:
            console.print(
                f"  Batch {batch_idx + 1}/{len(batches)} "
                f"({len(batch)} slide{'s' if len(batch) != 1 else ''})..."
            )

        # Build per-batch Langfuse context
        langfuse_ctx = None
        trace_id = None
        if use_langfuse:
            trace_id = str(uuid4())
            langfuse_ctx = {
                "name": "voiceover_merge_batch",
                "trace_id": trace_id,
                "metadata": {
                    "langfuse_session_id": session_id,
                    "langfuse_tags": ["voiceover-sync", lang, "merge"],
                    "langfuse_user_id": git_user,
                    "langfuse_metadata": {
                        "slide_ids": [s.slide_id for s in batch],
                        "language": lang,
                        "topic": slides.stem,
                        "batch_char_count": sum(
                            len(s.baseline) + len(s.transcript) + len(s.slide_content)
                            for s in batch
                        ),
                    },
                },
            }

        results = await merge_batch(
            batch,
            language=lang,
            model=merge_model,
            langfuse_context=langfuse_ctx,
        )
        all_results.extend(results)

        # Log each result to the trace log
        for slide_input, result in zip(batch, results, strict=True):
            trace.log_merge_call(
                slide_id=result.slide_id,
                language=lang,
                baseline=slide_input.baseline,
                transcript=slide_input.transcript,
                llm_merged=result.merged_bullets,
                rewrites=result.rewrites,
                dropped_from_transcript=result.dropped_from_transcript,
                langfuse_trace_id=trace_id,
            )

    # Flush Langfuse traces before exiting (best-effort)
    if use_langfuse:
        flush_langfuse()

    # Build merged notes_map from results
    merged_map: dict[int, str] = {}
    rewrite_count = 0
    for result in all_results:
        # Extract slide index from slide_id (format: "stem/idx")
        try:
            idx = int(result.slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            logger.warning("Cannot parse slide index from %s", result.slide_id)
            continue
        if result.merged_bullets.strip():
            merged_map[idx] = result.merged_bullets
        if result.rewrites:
            rewrite_count += len(result.rewrites)

    # Display results
    _display_merge_summary(all_results, slide_groups)

    if rewrite_count:
        console.print(
            f"\n[yellow]Warning: {rewrite_count} baseline rewrite(s) detected. "
            f"Review the diff carefully.[/yellow]"
        )

    if dry_run:
        # Emit unified diff
        _emit_dry_run_diff(slides, merged_map, lang, tag, all_results)
        console.print(f"\n[dim]Trace log: {trace.path}[/dim]")
        console.print("[yellow]Dry run — no changes written.[/yellow]")
        return

    # Write merged cells
    dest = write_narrative(slides, merged_map, lang, tag=tag, output_path=output)
    console.print(f"\n[dim]Trace log: {trace.path}[/dim]")
    console.print(f"[green]{tag.capitalize()} cells written to {dest}[/green]")


def _extract_baseline(slide_group, tag: str) -> str:
    """Extract existing voiceover/notes text for the given tag from a slide group."""
    parts = []
    for cell in slide_group.notes_cells:
        # Only include cells matching the target tag
        if tag in cell.metadata.tags:
            text = cell.text_content()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _has_boundary(alignment, slide_idx: int) -> bool:
    """Check if a slide has transcript segments from multiple video parts.

    Conservative heuristic: in multi-part mode, always returns True.
    This means the merge prompt is extra suspicious of greeting/sign-off
    noise near all slides, which is the safe default.
    """
    if slide_idx not in alignment.slide_notes:
        return False
    return True


def _display_merge_summary(results: list, slide_groups: list):
    """Display a summary table of merge results."""
    table = Table(title="Merge Results")
    table.add_column("Slide", style="cyan")
    table.add_column("Title")
    table.add_column("Length", style="green")
    table.add_column("Rewrites", style="yellow")
    table.add_column("Preview")

    for result in results:
        try:
            idx = int(result.slide_id.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            idx = -1

        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:30]
                break

        text = result.merged_bullets
        preview = text[:50].replace("\n", " ") + ("..." if len(text) > 50 else "")
        rewrites_str = str(len(result.rewrites)) if result.rewrites else ""

        table.add_row(
            str(idx),
            title,
            f"{len(text)} chars",
            rewrites_str,
            preview,
        )

    console.print(table)


def _emit_dry_run_diff(
    slides: Path,
    merged_map: dict[int, str],
    lang: str,
    tag: str,
    results: list,
):
    """Emit a unified diff of baseline -> merged for dry-run mode."""
    from clm.notebooks.slide_writer import update_narrative

    original_text = slides.read_text(encoding="utf-8")
    updated_text = update_narrative(original_text, merged_map, lang, tag=tag)

    if original_text == updated_text:
        console.print("\n[dim]No changes — merged output matches baseline.[/dim]")
        return

    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        updated_text.splitlines(keepends=True),
        fromfile=f"a/{slides.name}",
        tofile=f"b/{slides.name}",
    )

    console.print()
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(line)

    # Annotate rewrites
    for result in results:
        if result.rewrites:
            try:
                idx = int(result.slide_id.rsplit("/", 1)[-1])
            except (ValueError, IndexError):
                idx = -1
            for rw in result.rewrites:
                console.print(
                    f"[yellow]  Warning: slide {idx}: baseline rewrite: "
                    f"{rw.get('original', '?')} -> {rw.get('revised', '?')}[/yellow]"
                )


@voiceover_group.command()
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", default=None, help="Language hint (e.g. 'de', 'en').")
@click.option("--whisper-model", default="large-v3", help="Whisper model size.")
@click.option(
    "--backend",
    "backend_name",
    default="faster-whisper",
    type=click.Choice(["faster-whisper", "cohere", "granite"]),
    help="Transcription backend.",
)
@click.option(
    "--device",
    default="auto",
    type=click.Choice(["auto", "cpu", "cuda"]),
    help="Device for transcription: auto (default), cpu, or cuda.",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def transcribe(ctx, video, lang, whisper_model, backend_name, device, output):
    """Transcribe a video file and output the transcript."""
    from clm.voiceover.cache import CachePolicy, cached_transcribe
    from clm.voiceover.transcribe import transcribe_video

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())

    console.print(f"[bold]Transcribing:[/bold] {video} (backend={backend_name}, device={device})")
    transcript, hit = cached_transcribe(
        video,
        policy=policy,
        transcribe_fn=lambda: transcribe_video(
            video,
            language=lang,
            backend_name=backend_name,
            model_size=whisper_model,
            device=device,
        ),
        backend_name=backend_name,
        model_size=whisper_model,
        language=lang,
        device=device,
    )
    if hit:
        console.print("  [dim]cache hit[/dim]")

    data = {
        "language": transcript.language,
        "duration": transcript.duration,
        "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments],
    }

    if output:
        output.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Transcript written to {output}[/green]")
    else:
        console.print_json(json.dumps(data, ensure_ascii=False))


@voiceover_group.command()
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def detect(ctx, video, output):
    """Detect slide transitions in a video file."""
    from clm.voiceover.cache import CachePolicy, cached_detect
    from clm.voiceover.keyframes import detect_transitions

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())

    console.print(f"[bold]Detecting transitions:[/bold] {video}")
    events, hit = cached_detect(
        video,
        policy=policy,
        detect_fn=lambda: detect_transitions(video)[0],
    )
    if hit:
        console.print("  [dim]cache hit[/dim]")

    data = [
        {
            "timestamp": e.timestamp,
            "peak_diff": e.peak_diff,
            "confidence": e.confidence,
        }
        for e in events
    ]

    console.print(f"  {len(events)} transitions detected")

    if output:
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        console.print(f"[green]Transitions written to {output}[/green]")
    else:
        table = Table(title="Detected Transitions")
        table.add_column("Time", style="cyan")
        table.add_column("Peak Diff", style="green")
        table.add_column("Confidence", style="yellow")
        for e in events:
            table.add_row(
                f"{e.timestamp:.1f}s",
                f"{e.peak_diff:.4f}",
                f"{e.confidence:.1f}",
            )
        console.print(table)


@voiceover_group.command()
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def identify(ctx, video, slides, lang, output):
    """Identify which slides appear in a video (OCR + matching)."""
    from clm.notebooks.slide_parser import parse_slides
    from clm.voiceover.cache import CachePolicy, cached_detect, cached_timeline
    from clm.voiceover.keyframes import detect_transitions
    from clm.voiceover.matcher import match_events_to_slides

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())

    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)

    console.print(f"[bold]Detecting transitions:[/bold] {video}")
    events, det_hit = cached_detect(
        video,
        policy=policy,
        base_dir=slides.parent,
        detect_fn=lambda: detect_transitions(video)[0],
    )
    if det_hit:
        console.print("  [dim]transitions: cache hit[/dim]")

    console.print(
        f"[bold]Matching {len(events)} transitions to {len(slide_groups)} slides...[/bold]"
    )
    timeline_cfg = {"lang": lang, "frame_offset": 1.0, "multi_part": False}

    def _run_match():
        return match_events_to_slides(events, slide_groups, video, lang=lang).timeline

    timeline, tl_hit = cached_timeline(
        video,
        slides,
        policy=policy,
        base_dir=slides.parent,
        timeline_fn=_run_match,
        cfg=timeline_cfg,
    )
    if tl_hit:
        console.print("  [dim]timeline: cache hit[/dim]")

    data = [
        {
            "slide_index": e.slide_index,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "match_score": e.match_score,
        }
        for e in timeline
    ]

    if output:
        output.write_text(json.dumps(data, indent=2), encoding="utf-8")
        console.print(f"[green]Timeline written to {output}[/green]")
    else:
        table = Table(title="Slide Timeline")
        table.add_column("Slide", style="cyan")
        table.add_column("Start", style="green")
        table.add_column("End", style="green")
        table.add_column("Score", style="yellow")
        table.add_column("Title")
        for e in timeline:
            title = ""
            for sg in slide_groups:
                if sg.index == e.slide_index:
                    title = sg.title[:40]
                    break
            table.add_row(
                str(e.slide_index),
                f"{e.start_time:.1f}s",
                f"{e.end_time:.1f}s",
                f"{e.match_score:.0f}",
                title,
            )
        console.print(table)


@voiceover_group.command("identify-rev")
@click.argument("slide_file", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]))
@click.option(
    "--top",
    default=5,
    show_default=True,
    type=int,
    help="How many top-ranked revisions to display.",
)
@click.option(
    "--since",
    default=None,
    help="git log --since filter (e.g. '6 months ago', '2025-01-01').",
)
@click.option(
    "--limit",
    default=50,
    show_default=True,
    type=int,
    help="Maximum number of commits to score (most recent first).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of a table.",
)
@click.pass_context
def identify_rev_cmd(ctx, slide_file, videos, lang, top, since, limit, as_json):
    """Identify which historical SLIDE_FILE revision a VIDEO was recorded against.

    Builds an OCR fingerprint from the video's keyframe transitions and
    scores each recent git revision of SLIDE_FILE by fuzzy longest-
    common-subsequence matching. Narrative-heavy commit endpoints (likely
    recording-session markers) receive a multiplicative prior.

    Useful as a standalone diagnostic before running the full backfill
    pipeline, or when handing a revision to `clm voiceover sync` manually.

    \b
    Examples:
        clm voiceover identify-rev slides/topic_045/slides.py part1.mp4 part2.mp4 --lang de
        clm voiceover identify-rev slides/topic_045/slides.py recording.mp4 --lang en --top 10
    """
    from clm.voiceover.cache import CachePolicy, cached_detect
    from clm.voiceover.keyframes import detect_transitions, get_frame_at
    from clm.voiceover.matcher import ocr_frame
    from clm.voiceover.rev_scorer import DEFAULT_ACCEPT_THRESHOLD, score_revisions

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())

    console.print(f"[bold]Building video fingerprint from {len(videos)} part(s)...[/bold]")
    labels: list[str] = []
    ocr_lang = "deu+eng" if lang == "de" else "eng+deu"
    for video in videos:

        def _do_detect(v=video):
            return detect_transitions(v)[0]

        events, det_hit = cached_detect(
            video,
            policy=policy,
            base_dir=slide_file.parent,
            detect_fn=_do_detect,
        )
        if det_hit:
            console.print(f"  [dim]{video.name}: transitions cache hit[/dim]")
        for event in events:
            try:
                frame = get_frame_at(video, event.timestamp, offset=1.0)
                text = ocr_frame(frame, lang=ocr_lang).strip()
            except (ValueError, FileNotFoundError) as e:
                logger.warning("Skipping frame at %.1fs: %s", event.timestamp, e)
                continue
            if text:
                labels.append(text)

    if not labels:
        raise click.ClickException("video fingerprint is empty (no OCR text extracted)")

    console.print(
        f"[bold]Scoring up to {limit} candidate revisions against {len(labels)} keyframes...[/bold]"
    )
    scored = score_revisions(slide_file, labels, lang=lang, limit=limit, since=since)
    top_n = scored[:top]

    if not top_n:
        console.print("[yellow]No historical revisions found for this file.[/yellow]")
        return

    if as_json:
        payload = [
            {
                "rev": r.rev,
                "date": r.date.isoformat() if r.date else None,
                "subject": r.subject,
                "base_score": r.base_score,
                "narrative_prior": r.narrative_prior,
                "score": r.score,
                "is_narrative_candidate": r.is_narrative_candidate,
                "run_id": r.run_id,
                "run_position": r.run_position,
            }
            for r in top_n
        ]
        click.echo(json.dumps(payload, indent=2))
        return

    table = Table(title=f"Top {len(top_n)} revisions for {slide_file.name}")
    table.add_column("SHA", style="cyan")
    table.add_column("Date")
    table.add_column("Base", justify="right")
    table.add_column("Prior", justify="right")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Endpoint")
    table.add_column("Subject")
    for r in top_n:
        endpoint = f"#{r.run_id} {r.run_position}" if r.is_narrative_candidate else ""
        table.add_row(
            r.rev[:10],
            r.date.strftime("%Y-%m-%d") if r.date else "",
            f"{r.base_score:.3f}",
            f"{r.narrative_prior:.2f}x",
            f"{r.score:.3f}",
            endpoint,
            (r.subject or "")[:50],
        )
    console.print(table)

    top_score = top_n[0].score
    if top_score < DEFAULT_ACCEPT_THRESHOLD:
        console.print()
        console.print(
            f"[yellow]Top score {top_score:.3f} is below the acceptance threshold "
            f"{DEFAULT_ACCEPT_THRESHOLD}. Re-run `sync`/`backfill` with an explicit "
            f"--rev if you want to force this revision.[/yellow]"
        )


@voiceover_group.command("sync-at-rev")
@click.argument("slide_file", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--rev", required=True, help="Git revision (SHA, tag, or branch) to export SLIDE_FILE at."
)
@click.option(
    "--output",
    "-o",
    "output",
    required=True,
    type=click.Path(path_type=Path),
    help="Destination for the sync output (must not be the working-copy slide file).",
)
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Video language.")
@click.option(
    "--mode",
    default="polished",
    type=click.Choice(["verbatim", "polished"]),
)
@click.option("--overwrite", is_flag=True, default=False)
@click.option("--whisper-model", default="large-v3")
@click.option(
    "--backend",
    "backend_name",
    default="faster-whisper",
    type=click.Choice(["faster-whisper", "cohere", "granite"]),
)
@click.option(
    "--device",
    default="auto",
    type=click.Choice(["auto", "cpu", "cuda"]),
)
@click.option("--tag", default="voiceover")
@click.option("--dry-run", is_flag=True)
@click.option("--keep-audio", is_flag=True)
@click.option("--model", default=None)
@click.option(
    "--transcript",
    "transcript_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
@click.option(
    "--alignment",
    "alignment_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
@click.option(
    "--scratch-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the exported slide file into this directory instead of a fresh scratch dir.",
)
@click.pass_context
def sync_at_rev_cmd(
    ctx,
    slide_file,
    videos,
    rev,
    output,
    lang,
    mode,
    overwrite,
    whisper_model,
    backend_name,
    device,
    tag,
    dry_run,
    keep_audio,
    model,
    transcript_override,
    alignment_override,
    scratch_dir,
):
    """Run ``clm voiceover sync`` against SLIDE_FILE as it existed at --rev.

    Exports the historical version of SLIDE_FILE to a scratch location
    via ``git show`` (never touches the working tree) and then runs the
    full sync pipeline against that file plus the VIDEO parts. Output is
    written to --output so the working-copy slide file is preserved.

    Typical use is the middle step of the backfill pipeline:

    \b
    1. ``clm voiceover identify-rev`` suggests a SHA
    2. ``clm voiceover sync-at-rev --rev <sha> -o scratch.py`` produces
       voiceover cells against the historical slides
    3. ``clm voiceover port-voiceover scratch.py slide.py`` ports forward

    \b
    Examples:
        clm voiceover sync-at-rev slides.py video.mp4 --rev abc1234 \\
            --lang de -o /tmp/slides-at-abc1234-with-voiceover.py
    """
    from clm.voiceover.backfill import (
        extract_slide_file_at_rev,
        plan_scratch_dir,
        resolve_rev,
    )

    target_output = Path(output)
    if target_output.resolve() == slide_file.resolve():
        raise click.UsageError(
            "--output must not equal SLIDE_FILE; sync-at-rev refuses to mutate the working copy."
        )

    try:
        full_rev = resolve_rev(slide_file, rev)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    scratch = Path(scratch_dir) if scratch_dir else plan_scratch_dir(slide_file)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        scratch_slide_path = extract_slide_file_at_rev(slide_file, full_rev, scratch)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    console.print(
        f"[bold]Exported {slide_file.name} @ {full_rev[:10]}[/bold] -> {scratch_slide_path}"
    )

    ctx.invoke(
        sync,
        slides=scratch_slide_path,
        videos=tuple(videos),
        lang=lang,
        mode=mode,
        overwrite=overwrite,
        whisper_model=whisper_model,
        backend_name=backend_name,
        device=device,
        tag=tag,
        slides_range=None,
        dry_run=dry_run,
        output=target_output,
        keep_audio=keep_audio,
        model=model,
        transcript_override=transcript_override,
        alignment_override=alignment_override,
    )


@voiceover_group.command("port-voiceover")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Slide language.")
@click.option("--dry-run", is_flag=True, help="Print a unified diff instead of writing TARGET.")
@click.option(
    "--tag",
    default="voiceover",
    help="Cell tag to read/write: 'voiceover' (default) or 'notes'.",
)
@click.option(
    "--model",
    default=None,
    help="Override the LLM model (defaults to anthropic/claude-sonnet-4-6).",
)
@click.option(
    "--api-base",
    default=None,
    help="Override the LLM API base URL (e.g. https://openrouter.ai/api/v1).",
)
def port_voiceover_cmd(source, target, lang, dry_run, tag, model, api_base):
    """Port voiceover from SOURCE slide file onto TARGET slide file.

    File-to-file transfer with no git involvement — use `clm voiceover
    backfill` when you want history-aware extraction. SOURCE typically
    comes from `clm voiceover sync-at-rev` against an older revision;
    TARGET is the current HEAD version.

    Slide matching uses slide_id as the primary key, falling back to
    fuzzy title match, then content fingerprint for ambiguous cases.
    New-at-HEAD and removed-at-HEAD slides are reported but never
    edited. The LLM is called per matched slide to merge prior bullets
    into any existing baseline.

    \b
    Examples:
        clm voiceover port-voiceover /tmp/slides-at-abc123.py slides.py --lang de
        clm voiceover port-voiceover old.py new.py --lang en --dry-run
    """
    notes_map = _port_voiceover_notes(
        source=source,
        target=target,
        lang=lang,
        model=model,
        api_base=api_base,
    )

    if not notes_map:
        console.print("\n[dim]Nothing to port (no matched slides had prior voiceover).[/dim]")
        return

    from clm.notebooks.slide_writer import update_narrative

    original_text = target.read_text(encoding="utf-8")
    updated_text = update_narrative(original_text, notes_map, lang, tag=tag)

    if dry_run:
        _emit_unified_diff(target, original_text, updated_text)
        return

    target.write_text(updated_text, encoding="utf-8")
    console.print(f"\n[bold green]Wrote {len(notes_map)} voiceover cells to {target}[/bold green]")


def _port_voiceover_notes(
    *,
    source: Path,
    target: Path,
    lang: str,
    model: str | None,
    api_base: str | None,
) -> dict[int, str]:
    """Run the per-slide match + polish_and_port loop.

    Returns a ``notes_map`` keyed by target-group index suitable for
    passing to :func:`clm.notebooks.slide_writer.update_narrative`.
    Shared by ``port-voiceover`` and ``backfill`` so both commands use
    identical LLM orchestration and reporting.
    """
    import asyncio

    from clm.notebooks.slide_parser import parse_slides
    from clm.voiceover.port import polish_and_port
    from clm.voiceover.slide_matcher import MatchKind, match_slides

    source_groups = parse_slides(source, lang, include_header=True)
    target_groups = parse_slides(target, lang, include_header=True)
    matches = match_slides(source_groups, target_groups)

    summary: dict[MatchKind, int] = {}
    for m in matches:
        summary[m.kind] = summary.get(m.kind, 0) + 1

    console.print(
        f"[bold]Matched {len(target_groups)} target slides "
        f"against {len(source_groups)} source slides[/bold]"
    )
    for kind in MatchKind:
        if summary.get(kind):
            console.print(f"  {kind.value}: {summary[kind]}")

    port_kwargs: dict = {}
    if model:
        port_kwargs["model"] = model
    if api_base:
        port_kwargs["api_base"] = api_base

    async def _run() -> dict[int, str]:
        notes_map: dict[int, str] = {}
        for match in matches:
            if match.kind is MatchKind.REMOVED_AT_HEAD:
                console.print(f"  [dim]removed at head:[/dim] {match.key} (source slide dropped)")
                continue
            if match.kind is MatchKind.NEW_AT_HEAD:
                console.print(f"  [dim]new at head:[/dim] {match.key} (no prior voiceover)")
                continue
            if match.kind is MatchKind.MANUAL_REVIEW:
                console.print(
                    f"  [yellow]manual review:[/yellow] {match.key} (ambiguous source match)"
                )
                continue

            assert match.target_index is not None
            assert match.target_group is not None
            assert match.source_group is not None

            baseline = match.target_group.notes_text
            prior = match.source_group.notes_text
            if not prior.strip() and not baseline.strip():
                continue

            result = await polish_and_port(
                baseline_bullets=baseline,
                prior_voiceover=prior,
                slide_content_head=match.target_group.text_content,
                slide_content_prior=(
                    match.source_group.text_content if match.content_changed else None
                ),
                language=lang,
                content_changed=match.content_changed,
                slide_id=f"{target.stem}/{match.target_index}",
                **port_kwargs,
            )
            if result.error:
                console.print(f"  [red]error porting {match.key}:[/red] {result.error}")
            elif match.content_changed:
                console.print(
                    f"  [yellow]modified:[/yellow] {match.key} "
                    f"(content similarity {match.content_similarity:.0f})"
                )
            else:
                console.print(f"  [green]unchanged:[/green] {match.key}")

            if result.bullets.strip():
                notes_map[match.target_index] = result.bullets
        return notes_map

    return asyncio.run(_run())


def _emit_unified_diff(target: Path, original_text: str, updated_text: str) -> None:
    """Render a coloured unified diff for ``original_text`` -> ``updated_text``."""
    if original_text == updated_text:
        console.print("\n[dim]No changes — merged output matches baseline.[/dim]")
        return
    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        updated_text.splitlines(keepends=True),
        fromfile=f"a/{target.name}",
        tofile=f"b/{target.name}",
    )
    console.print()
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("+"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(line)


@voiceover_group.command("backfill")
@click.argument("slide_file", type=click.Path(exists=True, path_type=Path))
@click.argument("videos", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Video language.")
@click.option("--rev", default=None, help="Skip identify-rev and use this git revision directly.")
@click.option(
    "--top",
    default=5,
    show_default=True,
    type=int,
    help="How many top-ranked revisions to consider in Step 1 (identify-rev).",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Pick the top-ranked revision automatically (Step 1) instead of requiring --rev.",
)
@click.option(
    "--force-rev",
    is_flag=True,
    default=False,
    help="Proceed even when the identified rev's score is below the accept threshold.",
)
@click.option("--dry-run", is_flag=True, help="Print the diff only; do not write the port.patch.")
@click.option(
    "--apply",
    "apply_patch",
    is_flag=True,
    default=False,
    help="Mutate SLIDE_FILE with the ported voiceover (default: write patch only).",
)
@click.option(
    "--keep-scratch",
    is_flag=True,
    default=False,
    help="Do not delete the .clm/voiceover-backfill/<topic>-<ts>/ scratch directory on exit.",
)
@click.option("--tag", default="voiceover", help="Cell tag: 'voiceover' (default) or 'notes'.")
@click.option("--whisper-model", default="large-v3")
@click.option(
    "--backend",
    "backend_name",
    default="faster-whisper",
    type=click.Choice(["faster-whisper", "cohere", "granite"]),
)
@click.option(
    "--device",
    default="auto",
    type=click.Choice(["auto", "cpu", "cuda"]),
)
@click.option(
    "--model",
    default=None,
    help="Override the LLM model for the polish + port steps.",
)
@click.option(
    "--api-base",
    default=None,
    help="Override the LLM API base URL (passed through to port-voiceover).",
)
@click.pass_context
def backfill_cmd(
    ctx,
    slide_file,
    videos,
    lang,
    rev,
    top,
    auto,
    force_rev,
    dry_run,
    apply_patch,
    keep_scratch,
    tag,
    whisper_model,
    backend_name,
    device,
    model,
    api_base,
):
    """Extract voiceover from old recordings onto the current SLIDE_FILE.

    Composes the three-step pipeline:

    \b
    1. identify-rev — find the git revision SLIDE_FILE was recorded at
       (skipped if --rev is supplied)
    2. sync-at-rev — export that revision to scratch and run sync
       against it
    3. port-voiceover — port the resulting voiceover cells onto the
       current HEAD SLIDE_FILE
    \b

    Patch-by-default: the command writes a unified diff to
    ``.clm/voiceover-backfill/<topic>-<ts>/port.patch`` and prints it.
    Pass --apply to mutate the working-copy SLIDE_FILE. --dry-run
    suppresses patch writing and shows the diff only.

    \b
    Examples:
        clm voiceover backfill slides.py video.mp4 --lang de --auto
        clm voiceover backfill slides.py video.mp4 --lang en --rev abc1234
        clm voiceover backfill slides.py "Teil 1.mp4" "Teil 2.mp4" \\
            --lang de --auto --apply
    """
    import shutil

    from clm.voiceover.backfill import (
        compute_port_patch,
        extract_slide_file_at_rev,
        plan_scratch_dir,
        resolve_rev,
    )

    if dry_run and apply_patch:
        raise click.UsageError("--dry-run and --apply are mutually exclusive.")

    scratch = plan_scratch_dir(slide_file)
    console.print(f"[bold]Scratch dir:[/bold] {scratch}")

    cleanup_scratch = not keep_scratch
    try:
        # Step 1 — identify-rev (unless the caller supplied --rev).
        if rev is None:
            picked_rev = _backfill_identify_rev(
                ctx=ctx,
                slide_file=slide_file,
                videos=list(videos),
                lang=lang,
                top=top,
                auto=auto,
                force_rev=force_rev,
            )
        else:
            try:
                picked_rev = resolve_rev(slide_file, rev)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
        console.print(f"[bold]Using revision:[/bold] {picked_rev[:10]}")

        # Step 2 — export the slide file at that rev and run sync into scratch.
        try:
            scratch_slides = extract_slide_file_at_rev(slide_file, picked_rev, scratch)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
        synced_output = scratch / f"{slide_file.stem}-at-{picked_rev[:10]}-synced.py"

        console.print(
            f"[bold]Step 2:[/bold] sync {slide_file.name}@{picked_rev[:10]} -> {synced_output.name}"
        )
        ctx.invoke(
            sync,
            slides=scratch_slides,
            videos=tuple(videos),
            lang=lang,
            mode="polished",
            overwrite=False,
            whisper_model=whisper_model,
            backend_name=backend_name,
            device=device,
            tag=tag,
            slides_range=None,
            dry_run=False,
            output=synced_output,
            keep_audio=False,
            model=model,
            transcript_override=None,
            alignment_override=None,
        )

        # Step 3 — port synced voiceover forward to HEAD.
        console.print(f"[bold]Step 3:[/bold] port {synced_output.name} -> {slide_file.name}")
        notes_map = _port_voiceover_notes(
            source=synced_output,
            target=slide_file,
            lang=lang,
            model=model,
            api_base=api_base,
        )

        if not notes_map:
            console.print("\n[yellow]Nothing to port — leaving SLIDE_FILE untouched.[/yellow]")
            return

        from clm.notebooks.slide_writer import update_narrative

        original_text = slide_file.read_text(encoding="utf-8")
        updated_text = update_narrative(original_text, notes_map, lang, tag=tag)
        patch_text = compute_port_patch(slide_file, updated_text, original_text=original_text)

        if not patch_text:
            console.print("\n[dim]No changes — merged output matches baseline.[/dim]")
            return

        _emit_unified_diff(slide_file, original_text, updated_text)

        if dry_run:
            console.print("\n[yellow]Dry run — no patch written.[/yellow]")
        else:
            patch_path = scratch / "port.patch"
            patch_path.write_text(patch_text, encoding="utf-8")
            console.print(f"\n[bold]Patch written:[/bold] {patch_path}")
            # We intentionally keep the scratch dir whenever a patch was
            # written, so the user can re-apply it later or audit the
            # scratch slides. Explicit --keep-scratch is honored for the
            # no-patch path.
            cleanup_scratch = False

        if apply_patch:
            slide_file.write_text(updated_text, encoding="utf-8")
            console.print(
                f"[bold green]Applied {len(notes_map)} voiceover cells to {slide_file}[/bold green]"
            )
        elif not dry_run:
            console.print(
                "[dim]Review the patch above and re-run with --apply to update SLIDE_FILE.[/dim]"
            )
    finally:
        if cleanup_scratch and scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)


def _backfill_identify_rev(
    *,
    ctx,
    slide_file: Path,
    videos: list[Path],
    lang: str,
    top: int,
    auto: bool,
    force_rev: bool,
) -> str:
    """Run identify-rev internally and pick a candidate SHA.

    Honors ``--auto`` (take the highest-scoring rev without prompting),
    ``--force-rev`` (bypass the accept threshold), and otherwise aborts
    with a message asking the caller to rerun with --rev.
    """
    from clm.voiceover.cache import CachePolicy, cached_detect
    from clm.voiceover.keyframes import detect_transitions, get_frame_at
    from clm.voiceover.matcher import ocr_frame
    from clm.voiceover.rev_scorer import DEFAULT_ACCEPT_THRESHOLD, score_revisions

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    ocr_lang = "deu+eng" if lang == "de" else "eng+deu"

    console.print("[bold]Step 1:[/bold] identifying recording revision...")
    labels: list[str] = []
    for video in videos:

        def _do_detect(v=video):
            return detect_transitions(v)[0]

        events, det_hit = cached_detect(
            video,
            policy=policy,
            base_dir=slide_file.parent,
            detect_fn=_do_detect,
        )
        if det_hit:
            console.print(f"  [dim]{video.name}: transitions cache hit[/dim]")
        for event in events:
            try:
                frame = get_frame_at(video, event.timestamp, offset=1.0)
                text = ocr_frame(frame, lang=ocr_lang).strip()
            except (ValueError, FileNotFoundError) as exc:
                logger.warning("Skipping frame at %.1fs: %s", event.timestamp, exc)
                continue
            if text:
                labels.append(text)

    if not labels:
        raise click.ClickException(
            "video fingerprint is empty (no OCR text extracted) — cannot identify revision"
        )

    scored = score_revisions(slide_file, labels, lang=lang, limit=50)
    top_n = scored[:top]
    if not top_n:
        raise click.ClickException("no historical revisions found for this file")

    table = Table(title=f"Top {len(top_n)} revisions for {slide_file.name}")
    table.add_column("SHA", style="cyan")
    table.add_column("Date")
    table.add_column("Base", justify="right")
    table.add_column("Prior", justify="right")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Subject")
    for r in top_n:
        table.add_row(
            r.rev[:10],
            r.date.strftime("%Y-%m-%d") if r.date else "",
            f"{r.base_score:.3f}",
            f"{r.narrative_prior:.2f}x",
            f"{r.score:.3f}",
            (r.subject or "")[:50],
        )
    console.print(table)

    picked = top_n[0]
    if picked.score < DEFAULT_ACCEPT_THRESHOLD and not force_rev:
        raise click.ClickException(
            f"top score {picked.score:.3f} is below the accept threshold "
            f"{DEFAULT_ACCEPT_THRESHOLD}. Re-run with --force-rev to accept it, "
            f"or pass --rev <sha> to pick a different candidate."
        )

    if not auto:
        raise click.ClickException(
            "multiple candidates shown above; re-run with --auto to pick the top-ranked "
            "revision, or --rev <sha> to select one explicitly."
        )

    return picked.rev


@voiceover_group.command("extract-training-data")
@click.argument("trace_log", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--base-dir",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Project root for resolving slide file paths. Defaults to trace log's project root.",
)
@click.option(
    "--tag",
    default="voiceover",
    help="Cell tag to read from slide files: 'voiceover' (default) or 'notes'.",
)
@click.option(
    "--no-check-git",
    is_flag=True,
    default=False,
    help="Skip git_head reachability check (useful for detached repos).",
)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
def extract_training_data(trace_log, base_dir, tag, no_check_git, output):
    """Extract training data from a voiceover merge trace log.

    Reads a JSONL trace log produced by `clm voiceover sync` and
    correlates each entry with the current slide file state to produce
    training triples. Each output line contains the LLM merge input,
    output, and the human-edited final version.

    \b
    Output fields per line:
        input.baseline    — existing voiceover before merge
        input.transcript  — raw transcript fed to the LLM
        llm_output        — what the LLM produced
        human_final       — current slide file state (after hand edits)
        delta_vs_llm      — unified diff (empty = no hand edits)

    \b
    Examples:
        clm voiceover extract-training-data .clm/voiceover-traces/slides_intro-20260412-012020.jsonl
        clm voiceover extract-training-data trace.jsonl -o training.jsonl
        clm voiceover extract-training-data trace.jsonl --no-check-git
    """
    from clm.voiceover.training_export import extract_training_data as do_extract

    console.print(f"[bold]Reading trace log:[/bold] {trace_log}")

    triples = do_extract(
        trace_log,
        base_dir=base_dir,
        tag=tag,
        check_git_head=not no_check_git,
    )

    if not triples:
        console.print("[yellow]No training triples extracted.[/yellow]")
        return

    # Count positive examples (no hand edits)
    positive = sum(1 for t in triples if not t.delta_vs_llm)
    edited = len(triples) - positive

    console.print(
        f"  Extracted {len(triples)} training triple(s): "
        f"{positive} positive (no edits), {edited} with hand edits"
    )

    # Serialize
    lines = [json.dumps(t.to_dict(), ensure_ascii=False) for t in triples]
    output_text = "\n".join(lines) + "\n"

    if output:
        output.write_text(output_text, encoding="utf-8")
        console.print(f"[green]Training data written to {output}[/green]")
    else:
        click.echo(output_text, nl=False)


def _parse_range(range_str: str) -> tuple[int, int]:
    """Parse a slide range string like '5-20' into (start, end)."""
    if "-" in range_str:
        parts = range_str.split("-", 1)
        return int(parts[0]), int(parts[1])
    n = int(range_str)
    return n, n


def _load_transcript_override(path: Path):
    """Load a JSON transcript produced by ``clm voiceover transcribe -o``.

    Accepts both the flat CLI output format (``{"language", "duration",
    "segments": [...]}``) and the canonical ``Transcript.to_dict()`` form,
    so users can pass either.
    """
    from clm.voiceover.transcribe import Transcript

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.UsageError(f"Transcript override {path} is not a JSON object.")
    if "segments" not in data or "language" not in data or "duration" not in data:
        raise click.UsageError(
            f"Transcript override {path} is missing required fields (segments, language, duration)."
        )
    return Transcript.from_dict(data)


def _load_alignment_override(path: Path):
    """Load a precomputed :class:`AlignmentResult` from JSON.

    Expects the shape written by :func:`clm.voiceover.cache._encode_alignment`
    (as stored under ``.clm/voiceover-cache/alignments/``). Accepts either
    the inner artifact object or the full cache payload (with ``artifact``
    wrapper).
    """
    from clm.voiceover.cache import _decode_alignment

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.UsageError(f"Alignment override {path} is not a JSON object.")
    # Cache files wrap the artifact; accept either form
    if "artifact" in data and isinstance(data["artifact"], dict):
        data = data["artifact"]
    if "slide_notes" not in data:
        raise click.UsageError(f"Alignment override {path} is missing the 'slide_notes' field.")
    return _decode_alignment(data)


async def _polish_notes(
    notes_map: dict[int, str],
    slide_groups: list,
    *,
    model: str | None = None,
    lang: str = "de",
) -> dict[int, str]:
    """Polish all notes via LLM."""
    from clm.notebooks.polish import polish_text

    kwargs: dict = {}
    if model:
        kwargs["model"] = model

    polished: dict[int, str] = {}
    for idx, text in notes_map.items():
        # Find slide content for context
        slide_content = ""
        for sg in slide_groups:
            if sg.index == idx:
                slide_content = sg.text_content
                break

        console.print(f"  Polishing slide {idx}...")
        polished[idx] = await polish_text(text, slide_content, **kwargs)

    return polished


def _get_git_user_name() -> str | None:
    """Return the git user.name, or None if unavailable."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _display_notes_summary(notes_map: dict[int, str], slide_groups: list):
    """Display a summary table of generated notes."""
    table = Table(title="Generated Notes")
    table.add_column("Slide", style="cyan")
    table.add_column("Title")
    table.add_column("Length", style="green")
    table.add_column("Preview")

    for idx in sorted(notes_map.keys()):
        text = notes_map[idx]
        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:30]
                break
        preview = text[:60].replace("\n", " ") + ("..." if len(text) > 60 else "")
        table.add_row(str(idx), title, f"{len(text)} chars", preview)

    console.print(table)


@voiceover_group.group("cache")
def cache_group():
    """Inspect and manage the voiceover artifact cache.

    The cache speeds up repeat runs of ``transcribe``/``detect``/
    ``identify``/``sync`` by persisting their intermediate outputs under
    ``.clm/voiceover-cache/``. Entries are keyed by cheap fingerprints of
    the video (path+mtime+size) and slide file (content hash), and stale
    entries are harmless — they become misses automatically when the
    corresponding inputs change.
    """


@cache_group.command("list")
@click.pass_context
def cache_list_cmd(ctx):
    """List cache entries (grouped by kind) with file sizes."""
    from clm.voiceover.cache import CachePolicy, iter_entries

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    root = policy.resolve_root()

    if not root.exists():
        console.print(f"[yellow]Cache is empty (no directory at {root}).[/yellow]")
        return

    entries = iter_entries(root)
    if not entries:
        console.print(f"[yellow]Cache is empty at {root}.[/yellow]")
        return

    table = Table(title=f"Voiceover cache at {root}")
    table.add_column("Kind", style="cyan")
    table.add_column("Key", style="magenta")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Path")
    total = 0
    for entry in entries:
        total += entry.size
        table.add_row(
            entry.subdir,
            entry.key,
            f"{entry.size:,} B",
            str(entry.path),
        )
    console.print(table)
    console.print(f"[dim]{len(entries)} entries, {total:,} bytes[/dim]")


@cache_group.command("prune")
@click.option(
    "--max-age-days",
    type=float,
    required=True,
    help="Delete cache entries older than this many days.",
)
@click.pass_context
def cache_prune_cmd(ctx, max_age_days):
    """Delete cache entries older than --max-age-days."""
    from clm.voiceover.cache import CachePolicy, prune

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    root = policy.resolve_root()

    removed = prune(root, max_age_days=max_age_days)
    console.print(f"[green]Pruned {removed} cache entries older than {max_age_days}d.[/green]")


@cache_group.command("clear")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def cache_clear_cmd(ctx, yes):
    """Delete every entry in the cache."""
    from clm.voiceover.cache import CachePolicy, clear

    policy: CachePolicy = ctx.obj.get("cache_policy", CachePolicy())
    root = policy.resolve_root()

    if not yes:
        click.confirm(
            f"Delete all cache entries under {root}?",
            abort=True,
        )

    removed = clear(root)
    console.print(f"[green]Removed {removed} cache entries from {root}.[/green]")


@voiceover_group.group("trace")
def trace_group():
    """Inspect voiceover merge trace logs.

    Trace logs are JSONL files under ``.clm/voiceover-traces/`` produced
    by every ``sync`` invocation. They record every LLM merge call with
    enough context to replay or evaluate it later (see schema
    ``clm.voiceover.trace/1``).
    """


@trace_group.command("show")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the full entries as JSON instead of a summary table.",
)
def trace_show_cmd(path, as_json):
    """Render a trace log in a human-readable summary."""
    from clm.voiceover.trace_log import read_trace_entries

    entries = read_trace_entries(path)
    if not entries:
        console.print(f"[yellow]No entries in {path}.[/yellow]")
        return

    if as_json:
        click.echo(json.dumps(entries, indent=2, ensure_ascii=False))
        return

    schema_tags = {e.get("schema", "<v0>") for e in entries}
    console.print(
        f"[bold]Trace log:[/bold] {path}  "
        f"([cyan]{len(entries)}[/cyan] entries; schema={', '.join(sorted(schema_tags))})"
    )

    table = Table()
    table.add_column("#", justify="right", style="dim")
    table.add_column("Slide", style="cyan")
    table.add_column("Lang")
    table.add_column("Baseline", justify="right", style="blue")
    table.add_column("Transcript", justify="right", style="magenta")
    table.add_column("Merged", justify="right", style="green")
    table.add_column("Rewrites", justify="right", style="yellow")
    table.add_column("Dropped", justify="right")
    table.add_column("Model")

    for i, entry in enumerate(entries):
        rewrites = entry.get("rewrites") or []
        dropped = entry.get("dropped_from_transcript") or []
        table.add_row(
            str(i + 1),
            str(entry.get("slide_id", "?")),
            str(entry.get("language", "")),
            str(len(entry.get("baseline", ""))),
            str(len(entry.get("transcript", ""))),
            str(len(entry.get("llm_merged", ""))),
            str(len(rewrites)) if rewrites else "",
            str(len(dropped)) if dropped else "",
            str(entry.get("model") or ""),
        )
    console.print(table)


@voiceover_group.group("debug", hidden=True)
def debug_group():
    """Diagnostic tools (unstable; hidden from --help).

    These commands expose internal signals used by higher-level features
    (e.g. the backfill / identify-rev pipeline). They are authoring-tool
    development aids, not part of the stable CLI surface, and may change
    or be removed without notice. Invoke by name explicitly:
    ``clm voiceover debug <subcommand>``.
    """


@debug_group.command("voiceover-commits")
@click.argument("slide_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--since",
    default=None,
    help="git log --since filter (e.g. '6 months ago', '2025-01-01').",
)
@click.option(
    "--limit",
    default=50,
    show_default=True,
    type=int,
    help="Maximum number of commits to examine (most recent first).",
)
@click.option(
    "--threshold",
    default=0.7,
    show_default=True,
    type=float,
    help="Narrative-churn ratio cutoff for 'heavy' classification.",
)
@click.option(
    "--floor",
    default=5,
    show_default=True,
    type=int,
    help="Minimum narrative-line-delta required for 'heavy' classification.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON instead of tables.",
)
def voiceover_commits_cmd(slide_file, since, limit, threshold, floor, as_json):
    """Walk git history and flag narrative-heavy commits for SLIDE_FILE.

    Throwaway spike informing the identify-rev scorer in the backfill
    feature. For each commit touching the file, computes narrative-cell
    (voiceover/notes) vs content-cell character churn. Consecutive
    narrative-heavy commits are collapsed into 'runs' and each run yields
    two candidate recording revisions: the pre-run parent (slides as they
    were going into the recording session) and the post-run tip (slides
    after note-taking finished).

    \b
    Examples:
        clm voiceover debug voiceover-commits slides/topic_045/slides.py
        clm voiceover debug voiceover-commits slides/topic_045/slides.py \\
            --since '6 months ago' --threshold 0.6
    """
    from clm.voiceover.narrative_commits import NarrativeRun, scan_slide_file

    metrics, runs = scan_slide_file(
        slide_file,
        since=since,
        limit=limit,
        threshold=threshold,
        floor=floor,
    )

    if not metrics:
        console.print(f"[yellow]No history found for {slide_file}[/yellow]")
        return

    if as_json:
        import json as jsonlib

        payload = {
            "slide_file": str(slide_file),
            "threshold": threshold,
            "floor": floor,
            "commits": [
                {
                    "sha": m.commit.sha,
                    "parent": m.commit.parent_sha,
                    "date": m.commit.date.isoformat(),
                    "subject": m.commit.subject,
                    "narrative_delta": m.narrative_delta,
                    "content_delta": m.content_delta,
                    "ratio": m.ratio,
                    "heavy": m.is_narrative_heavy,
                }
                for m in metrics
            ],
            "runs": [
                {
                    "id": r.run_id,
                    "length": len(r.commit_metrics),
                    "pre_run_sha": r.pre_run_sha,
                    "post_run_sha": r.post_run_sha,
                    "commits": [m.commit.sha for m in r.commit_metrics],
                    "narrative_delta_sum": sum(m.narrative_delta for m in r.commit_metrics),
                }
                for r in runs
            ],
        }
        click.echo(jsonlib.dumps(payload, indent=2))
        return

    sha_to_run: dict[str, NarrativeRun] = {m.commit.sha: r for r in runs for m in r.commit_metrics}

    table = Table(title=f"Narrative-commit scan: {slide_file.name}")
    table.add_column("SHA", style="cyan")
    table.add_column("Date")
    table.add_column("nar-Δ", justify="right", style="magenta")
    table.add_column("con-Δ", justify="right", style="blue")
    table.add_column("ratio", justify="right")
    table.add_column("heavy", justify="center")
    table.add_column("run")
    table.add_column("subject")

    for m in metrics:
        run = sha_to_run.get(m.commit.sha)
        table.add_row(
            m.commit.sha[:10],
            m.commit.date.strftime("%Y-%m-%d"),
            str(m.narrative_delta),
            str(m.content_delta),
            f"{m.ratio:.2f}",
            "[green]Y[/green]" if m.is_narrative_heavy else "",
            f"#{run.run_id}" if run else "",
            m.commit.subject[:60],
        )

    console.print(table)

    if runs:
        console.print()
        console.print("[bold]Narrative runs (candidate recording revisions):[/bold]")
        run_table = Table()
        run_table.add_column("Run", style="cyan")
        run_table.add_column("Len", justify="right")
        run_table.add_column("Pre-run SHA", style="yellow")
        run_table.add_column("Post-run SHA", style="green")
        run_table.add_column("Nar Δ sum", justify="right")
        run_table.add_column("Subjects")
        for r in runs:
            subjects = " | ".join(m.commit.subject[:35] for m in r.commit_metrics)
            nar_sum = sum(m.narrative_delta for m in r.commit_metrics)
            run_table.add_row(
                f"#{r.run_id}",
                str(len(r.commit_metrics)),
                (r.pre_run_sha or "<root>")[:10],
                r.post_run_sha[:10],
                str(nar_sum),
                subjects,
            )
        console.print(run_table)
    else:
        console.print("[yellow]No narrative-heavy commits found.[/yellow]")
