"""Extract voiceover cells to companion files, or inline them back."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.voiceover_tools import (
    ExtractionResult,
    InlineResult,
    VoiceoverError,
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
    "--force",
    is_flag=True,
    help="Overwrite an existing companion file. The companion is rebuilt from "
    "the slide's current voiceover cells, discarding any content present only "
    "in the companion; without --force an existing companion is left untouched.",
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
    force: bool,
    dry_run: bool,
    as_json: bool,
):
    """Extract voiceover cells from a slide file to a companion file.

    Moves voiceover and notes cells to a companion voiceover_*.py file,
    linked via slide_id/for_slide metadata.  Content cells without
    slide_id get auto-generated IDs before extraction.  Refuses to
    overwrite an existing companion unless --force is given.

    \b
    Examples:
        clm voiceover extract slides/topic/slides_intro.py --dry-run
        clm voiceover extract slides/topic/slides_intro.py
        clm voiceover extract slides/topic/slides_intro.py --force
        clm voiceover extract slides/topic/slides_intro.py --json
    """
    try:
        result = extract_voiceover(path, force=force, dry_run=dry_run)
    except VoiceoverError as e:
        raise click.ClickException(str(e)) from e

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
    Deletes the companion only when every cell is placed; if any cell is
    unmatched (e.g. its owning slide_id was renamed) the companion is kept
    with the leftovers and the command exits non-zero.

    \b
    Examples:
        clm voiceover inline slides/topic/slides_intro.py --dry-run
        clm voiceover inline slides/topic/slides_intro.py
        clm voiceover inline slides/topic/slides_intro.py --json
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
        # In dry-run, show where each voiceover will land so a relocation
        # is visible before the file is written.
        if dry_run and result.placements:
            for p in result.placements:
                if p.status == "unmatched":
                    click.echo(
                        f"  ? {p.for_slide or '<no for_slide>'}: no matching slide — kept in companion"
                    )
                else:
                    where = f"after line {p.after_line}" if p.after_line else "at end"
                    marker = "!" if p.status == "relocated" else "+"
                    click.echo(f"  {marker} {p.for_slide}: {p.status} {where}")

    # Unmatched cells are a partial failure: the companion was preserved with
    # them (no data loss), but the author must act. Exit non-zero so a script
    # or pre-commit hook notices instead of treating it as a clean inline.
    if not dry_run and result.unmatched_cells:
        raise click.ClickException(
            f"{result.unmatched_cells} voiceover cell(s) had no matching slide; "
            f"companion '{comp.name}' was kept with them. "
            f"Fix the slide_id(s) and re-run inline."
        )


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
        "relocated_cells": result.relocated_cells,
        "companion_deleted": result.companion_deleted,
        "companion_retained": result.companion_retained,
        "dry_run": result.dry_run,
        "summary": result.summary,
        "placements": [
            {
                "for_slide": p.for_slide,
                "anchor": p.anchor,
                "status": p.status,
                "after_line": p.after_line,
                "after_header": p.after_header,
            }
            for p in result.placements
        ],
    }
