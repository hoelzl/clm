"""Extract voiceover cells to companion files, or inline them back."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.pairing import derive_split_pair
from clm.slides.voiceover_tools import (
    ExtractionResult,
    InlineResult,
    PairedExtractionResult,
    VoiceoverError,
    extract_voiceover,
    extract_voiceover_pair,
    inline_voiceover,
    resolve_companion,
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
    "--both",
    is_flag=True,
    help="Extract BOTH companions of a split deck in one op (the EN-authority "
    "paired extract). Auto-detected when PATH is a split half whose twin exists; "
    "passing --both forces it and errors if there is no twin.",
)
@click.option(
    "--single",
    is_flag=True,
    help="Extract only PATH's own companion, even on a split half whose twin "
    "exists — opt out of the default auto-pairing.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview changes without modifying files.",
)
@click.option(
    "--layout",
    type=click.Choice(["subdir", "sibling"]),
    default=None,
    help="Where to write the companion: 'subdir' creates/uses a voiceover/ "
    "folder; 'sibling' writes next to the slide. Default: auto-detect an "
    "existing voiceover/ folder, else sibling.",
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
    both: bool,
    single: bool,
    dry_run: bool,
    layout: str | None,
    as_json: bool,
):
    """Extract voiceover cells from a slide file to a companion file.

    Moves voiceover and notes cells to a companion voiceover_*.<ext> file,
    linked via slide_id/for_slide metadata.  Content cells without
    slide_id get auto-generated IDs before extraction.  Refuses to
    overwrite an existing companion unless --force is given.

    On a split half (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``) whose twin exists on
    disk, both companions are extracted in one op by default: the two halves are
    first minted with EN-authority slide_ids so the companions' for_slide sets
    agree, then each half is extracted and all writes commit atomically. Pass
    --single to extract only this half; --both forces the paired form (and errors
    if there is no twin). A bilingual deck (no .de/.en twin) always extracts a
    single companion.

    \b
    Examples:
        clm voiceover extract slides/topic/slides_intro.py --dry-run
        clm voiceover extract slides/topic/slides_intro.de.py          # auto-pairs
        clm voiceover extract slides/topic/slides_intro.de.py --single # this half only
        clm voiceover extract slides/topic/slides_intro.py --force
        clm voiceover extract slides/topic/slides_intro.de.py --json
    """
    if both and single:
        raise click.UsageError("--both and --single are mutually exclusive")

    pair = None if single else derive_split_pair(path)
    if both and pair is None:
        raise click.ClickException(
            f"--both needs a split deck: '{path.name}' has no <deck>.de.<ext> / "
            f"<deck>.en.<ext> twin on disk."
        )

    # Fold the --layout flag with the course-wide default (CLM_SIDECAR_LAYOUT /
    # [tool.clm] sidecar-layout). A flag wins; otherwise a course default of
    # subdir steers new companions into voiceover/.
    from clm.slides.sidecar_layout import effective_write_layout

    layout = effective_write_layout(path, layout)

    try:
        if pair is not None:
            paired = extract_voiceover_pair(
                pair[0], pair[1], force=force, dry_run=dry_run, layout=layout
            )
            payload = _paired_extraction_to_dict(paired)
            summary = paired.summary
        else:
            result = extract_voiceover(path, force=force, dry_run=dry_run, layout=layout)
            payload = _extraction_to_dict(result)
            summary = result.summary
    except VoiceoverError as e:
        raise click.ClickException(str(e)) from e

    click.echo(json.dumps(payload, indent=2) if as_json else summary)


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

    Merges voiceover cells from the companion voiceover_*.<ext> file back
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
    comp = resolve_companion(path)
    if comp is None:
        click.echo(f"No companion file found for {path.name}")
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


def _paired_extraction_to_dict(result: PairedExtractionResult) -> dict:
    """JSON shape for a paired extract. The ``"paired": true`` discriminator
    lets consumers branch; ``"companions"`` reuses the single-file dict per half
    (DE first, EN second). A single-file extract keeps emitting the flat dict
    (no ``paired`` key), so existing ``--json`` consumers are unaffected."""
    return {
        "paired": True,
        "dry_run": result.dry_run,
        "ids_minted": result.ids_minted,
        "companions": [_extraction_to_dict(r) for r in result.results],
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
