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
def voiceover_group():
    """Video-to-speaker-notes synchronization.

    Transcribe a video recording and align the transcript to slides,
    then insert or update voiceover/notes cells in the .py slide file.

    Requires: pip install clm[voiceover]
    """


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
def sync(
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
    from clm.voiceover.keyframes import TransitionEvent, detect_transitions
    from clm.voiceover.matcher import match_events_to_slides
    from clm.voiceover.timeline import (
        build_parts,
        merge_transcripts,
        offset_events,
        offset_transcript,
    )
    from clm.voiceover.transcribe import transcribe_video

    video_paths = [Path(v) for v in videos]
    multi_part = len(video_paths) > 1

    # Parse slides
    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)
    console.print(f"  Found {len(slide_groups)} slide groups")

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

    # Per-part transcription and transition detection
    all_transcripts = []
    all_events: list[TransitionEvent] = []

    for part in parts:
        part_label = f" (part {part.index})" if multi_part else ""
        console.print(
            f"[bold]Transcribing{part_label}:[/bold] {part.path.name} "
            f"(backend={backend_name}, device={device})"
        )
        transcript = transcribe_video(
            part.path,
            language=lang,
            backend_name=backend_name,
            model_size=whisper_model,
            device=device,
            keep_audio=keep_audio,
        )
        console.print(
            f"  {len(transcript.segments)} segments, "
            f"{transcript.duration:.0f}s, language={transcript.language}"
        )

        console.print(f"[bold]Detecting slide transitions{part_label}...[/bold]")
        events, _diffs = detect_transitions(part.path)
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

    # Match to slides
    console.print("[bold]Matching transitions to slides...[/bold]")
    match_result = match_events_to_slides(
        all_events,
        slide_groups,
        video_paths[0],
        video_paths=video_paths if multi_part else None,
        total_duration=total_duration,
        lang=lang,
    )
    console.print(f"  {len(match_result.timeline)} timeline entries")

    # Align transcript to slides
    console.print("[bold]Aligning transcript to slides...[/bold]")
    alignment = align_transcript(merged_transcript, match_result.timeline)

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
def transcribe(video, lang, whisper_model, backend_name, device, output):
    """Transcribe a video file and output the transcript."""
    from clm.voiceover.transcribe import transcribe_video

    console.print(f"[bold]Transcribing:[/bold] {video} (backend={backend_name}, device={device})")
    transcript = transcribe_video(
        video,
        language=lang,
        backend_name=backend_name,
        model_size=whisper_model,
        device=device,
    )

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
def detect(video, output):
    """Detect slide transitions in a video file."""
    from clm.voiceover.keyframes import detect_transitions

    console.print(f"[bold]Detecting transitions:[/bold] {video}")
    events, _diffs = detect_transitions(video)

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
def identify(video, slides, lang, output):
    """Identify which slides appear in a video (OCR + matching)."""
    from clm.notebooks.slide_parser import parse_slides
    from clm.voiceover.keyframes import detect_transitions
    from clm.voiceover.matcher import match_events_to_slides

    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)

    console.print(f"[bold]Detecting transitions:[/bold] {video}")
    events, _ = detect_transitions(video)

    console.print(
        f"[bold]Matching {len(events)} transitions to {len(slide_groups)} slides...[/bold]"
    )
    result = match_events_to_slides(events, slide_groups, video, lang=lang)

    data = [
        {
            "slide_index": e.slide_index,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "match_score": e.match_score,
        }
        for e in result.timeline
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
        for e in result.timeline:
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
    default=50,
    show_default=True,
    type=int,
    help="Minimum narrative char-delta required for 'heavy' classification.",
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
