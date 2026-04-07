"""Extract voiceover cells to companion files, or inline them back."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.voiceover_tools import (
    ExtractionResult,
    InlineResult,
    companion_path,
    extract_voiceover,
    inline_voiceover,
)


@click.command("extract-voiceover")
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without modifying files.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def extract_voiceover_cmd(
    path: Path,
    dry_run: bool,
    as_json: bool,
):
    """Extract voiceover cells from a slide file to a companion file.

    Moves voiceover and notes cells to a companion voiceover_*.py file,
    linked via slide_id/for_slide metadata.  Content cells without
    slide_id get auto-generated IDs before extraction.

    \b
    Examples:
        clm extract-voiceover slides/topic/slides_intro.py --dry-run
        clm extract-voiceover slides/topic/slides_intro.py
        clm extract-voiceover slides/topic/slides_intro.py --json
    """
    result = extract_voiceover(path, dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(_extraction_to_dict(result), indent=2))
    else:
        click.echo(result.summary)


@click.command("inline-voiceover")
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without modifying files.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON.",
)
def inline_voiceover_cmd(
    path: Path,
    dry_run: bool,
    as_json: bool,
):
    """Inline voiceover cells from a companion file back into a slide file.

    Merges voiceover cells from the companion voiceover_*.py file back
    into the slide file, matching via for_slide/slide_id metadata.
    Deletes the companion file after successful inlining.

    \b
    Examples:
        clm inline-voiceover slides/topic/slides_intro.py --dry-run
        clm inline-voiceover slides/topic/slides_intro.py
        clm inline-voiceover slides/topic/slides_intro.py --json
    """
    comp = companion_path(path)
    if not comp.exists():
        click.echo(f"No companion file found at {comp}")
        return

    result = inline_voiceover(path, dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(_inline_to_dict(result), indent=2))
    else:
        click.echo(result.summary)


def _extraction_to_dict(result: ExtractionResult) -> dict:
    return {
        "slide_file": result.slide_file,
        "companion_file": result.companion_file,
        "cells_extracted": result.cells_extracted,
        "ids_generated": result.ids_generated,
        "dry_run": result.dry_run,
        "summary": result.summary,
    }


def _inline_to_dict(result: InlineResult) -> dict:
    return {
        "slide_file": result.slide_file,
        "companion_file": result.companion_file,
        "cells_inlined": result.cells_inlined,
        "unmatched_cells": result.unmatched_cells,
        "companion_deleted": result.companion_deleted,
        "dry_run": result.dry_run,
        "summary": result.summary,
    }
