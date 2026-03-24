"""Polish command for improving existing speaker notes with LLM cleanup.

This is a standalone command independent of the voiceover pipeline.
It reads existing speaker notes from a .py slide file and polishes
them using an LLM.

Requires the ``[summarize]`` extra (openai).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import click
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


@click.command()
@click.argument("slides", type=click.Path(exists=True, path_type=Path))
@click.option("--lang", required=True, type=click.Choice(["de", "en"]), help="Language of notes.")
@click.option("--slides-range", default=None, help="Slide range to polish (e.g. '5-10').")
@click.option("--dry-run", is_flag=True, help="Show polished text without writing.")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
@click.option("--model", default=None, help="LLM model identifier.")
def polish(slides, lang, slides_range, dry_run, output, model):
    """Polish existing speaker notes in a .py slide file using an LLM.

    Reads notes cells from SLIDES, sends them through an LLM for cleanup
    (removing filler words, fixing grammar, etc.), and writes the result.

    Requires: pip install clm[summarize]
    """
    from clm.notebooks.slide_parser import parse_slides
    from clm.notebooks.slide_writer import write_notes

    console.print(f"[bold]Parsing slides:[/bold] {slides}")
    slide_groups = parse_slides(slides, lang)
    console.print(f"  Found {len(slide_groups)} slide groups")

    # Collect slides with existing notes
    slides_with_notes: dict[int, tuple[str, str]] = {}  # idx -> (notes_text, slide_content)
    for sg in slide_groups:
        if not sg.has_notes:
            continue
        if slides_range:
            start, end = _parse_range(slides_range)
            if not (start <= sg.index <= end):
                continue
        slides_with_notes[sg.index] = (sg.notes_text, sg.text_content)

    if not slides_with_notes:
        console.print("[yellow]No notes found to polish.[/yellow]")
        return

    console.print(f"  {len(slides_with_notes)} slides with notes to polish")

    # Polish each slide's notes
    polished_map = asyncio.run(_polish_all(slides_with_notes, model=model))

    # Display results
    for idx in sorted(polished_map.keys()):
        title = ""
        for sg in slide_groups:
            if sg.index == idx:
                title = sg.title[:40]
                break
        console.print(f"\n[bold]Slide {idx}[/bold] ({title})")
        console.print(f"[dim]{polished_map[idx][:200]}[/dim]")

    if dry_run:
        console.print("\n[yellow]Dry run — no changes written.[/yellow]")
        return

    dest = write_notes(slides, polished_map, lang, output_path=output)
    console.print(f"\n[green]Polished notes written to {dest}[/green]")


def _parse_range(range_str: str) -> tuple[int, int]:
    """Parse a slide range string like '5-20' into (start, end)."""
    if "-" in range_str:
        parts = range_str.split("-", 1)
        return int(parts[0]), int(parts[1])
    n = int(range_str)
    return n, n


async def _polish_all(
    slides_with_notes: dict[int, tuple[str, str]],
    *,
    model: str | None = None,
) -> dict[int, str]:
    """Polish all notes via LLM."""
    from clm.notebooks.polish import polish_text

    kwargs: dict = {}
    if model:
        kwargs["model"] = model

    polished: dict[int, str] = {}
    for idx, (notes_text, slide_content) in slides_with_notes.items():
        console.print(f"  Polishing slide {idx}...")
        polished[idx] = await polish_text(notes_text, slide_content, **kwargs)

    return polished
