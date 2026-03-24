"""Voiceover command for synchronizing video recordings with slide speaker notes.

This module provides the ``clm voiceover`` command group with subcommands
for the full sync pipeline and individual diagnostic steps.

Requires the ``[voiceover]`` extra.
"""

from __future__ import annotations

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
    then insert or update speaker notes in the .py slide file.

    Requires: pip install clm[voiceover]
    """


@voiceover_group.command()
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Video language.")
@click.option(
    "--mode",
    default="verbatim",
    type=click.Choice(["verbatim", "polished"]),
    help="Verbatim keeps transcript as-is; polished runs LLM cleanup.",
)
@click.option("--whisper-model", default="large-v3", help="Whisper model size.")
@click.option("--slides-range", default=None, help="Slide range to update (e.g. '5-20').")
@click.option("--dry-run", is_flag=True, help="Show mapping without writing changes.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
@click.option("--keep-audio", is_flag=True, help="Keep extracted audio file.")
@click.option("--model", default=None, help="LLM model for polished mode.")
def sync(
    video, slides, lang, mode, whisper_model, slides_range, dry_run, output, keep_audio, model
):
    """Synchronize speaker notes from a video recording.

    Transcribes VIDEO, detects slide transitions, matches them to SLIDES,
    and inserts/updates speaker notes in the .py file.
    """
    from clm.notebooks.slide_parser import parse_slides
    from clm.notebooks.slide_writer import write_notes
    from clm.voiceover.aligner import align_transcript
    from clm.voiceover.keyframes import detect_transitions
    from clm.voiceover.matcher import match_events_to_slides
    from clm.voiceover.transcribe import transcribe_video

    # Parse slides
    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)
    console.print(f"  Found {len(slide_groups)} slide groups")

    # Transcribe
    console.print(f"[bold]Transcribing:[/bold] {video}")
    transcript = transcribe_video(
        video, language=lang, model_size=whisper_model, keep_audio=keep_audio
    )
    console.print(
        f"  {len(transcript.segments)} segments, "
        f"{transcript.duration:.0f}s, language={transcript.language}"
    )

    # Detect transitions
    console.print("[bold]Detecting slide transitions...[/bold]")
    events, _diffs = detect_transitions(video)
    console.print(f"  {len(events)} transitions detected")

    # Match to slides
    console.print("[bold]Matching transitions to slides...[/bold]")
    match_result = match_events_to_slides(events, slide_groups, video, lang=lang)
    console.print(f"  {len(match_result.timeline)} timeline entries")

    # Align transcript to slides
    console.print("[bold]Aligning transcript to slides...[/bold]")
    alignment = align_transcript(transcript, match_result.timeline)

    # Apply slide range filter
    slide_indices = set(alignment.slide_notes.keys())
    if slides_range:
        start, end = _parse_range(slides_range)
        slide_indices = {i for i in slide_indices if start <= i <= end}

    # Build notes map
    notes_map: dict[int, str] = {}
    for idx in sorted(slide_indices):
        text = alignment.get_notes_text(idx)
        if text:
            notes_map[idx] = text

    # Polish if requested
    if mode == "polished" and notes_map:
        import asyncio

        console.print("[bold]Polishing notes with LLM...[/bold]")
        notes_map = asyncio.run(_polish_notes(notes_map, slide_groups, model=model, lang=lang))

    # Display results
    _display_notes_summary(notes_map, slide_groups)

    if dry_run:
        console.print("\n[yellow]Dry run — no changes written.[/yellow]")
        return

    # Write notes
    dest = write_notes(slides, notes_map, lang, output_path=output)
    console.print(f"\n[green]Notes written to {dest}[/green]")


@voiceover_group.command()
@click.argument("video", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", default=None, help="Language hint (e.g. 'de', 'en').")
@click.option("--whisper-model", default="large-v3", help="Whisper model size.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
def transcribe(video, lang, whisper_model, output):
    """Transcribe a video file and output the transcript."""
    from clm.voiceover.transcribe import transcribe_video

    console.print(f"[bold]Transcribing:[/bold] {video}")
    transcript = transcribe_video(video, language=lang, model_size=whisper_model)

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
