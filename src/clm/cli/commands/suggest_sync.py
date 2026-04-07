"""Suggest bilingual sync updates for a slide file."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.language_tools import suggest_sync


@click.command("suggest-sync")
@click.argument(
    "file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--source-language",
    type=click.Choice(["de", "en"]),
    default=None,
    help="The language that was edited.  Auto-detected if omitted.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output as JSON.",
)
def suggest_sync_cmd(
    file: Path,
    source_language: str | None,
    json_output: bool,
):
    """Compare a slide file against git HEAD and suggest sync updates.

    Detects cells modified in one language without corresponding changes
    in the other language.  Uses slide_id metadata for precise pairing
    when available; falls back to positional pairing.

    \b
    Examples:
        clm suggest-sync slides/topic/slides_intro.py
        clm suggest-sync slides/topic/slides_intro.py --source-language de
        clm suggest-sync slides/topic/slides_intro.py --json
    """
    result = suggest_sync(file, source_language=source_language)

    if json_output:
        d = {
            "file": result.file,
            "source_language": result.source_language,
            "target_language": result.target_language,
            "pairing_method": result.pairing_method,
            "suggestions": [
                {
                    k: v
                    for k, v in {
                        "type": s.type,
                        "slide_id": s.slide_id,
                        "source_line": s.source_line,
                        "source_content": s.source_content,
                        "target_line": s.target_line,
                        "target_content_current": s.target_content_current,
                        "suggestion": s.suggestion,
                    }.items()
                    if v is not None
                }
                for s in result.suggestions
            ],
            "unmodified_pairs": result.unmodified_pairs,
            "sync_needed": result.sync_needed,
        }
        click.echo(json.dumps(d, indent=2))
    else:
        _print_human_readable(result)


def _print_human_readable(result):
    """Print a human-readable summary of sync results."""
    if not result.sync_needed:
        click.echo(
            f"In sync: {result.source_language.upper()} and "
            f"{result.target_language.upper()} are aligned "
            f"({result.unmodified_pairs} pairs)."
        )
        return

    click.echo(
        f"Source: {result.source_language.upper()} -> "
        f"Target: {result.target_language.upper()} "
        f"(pairing: {result.pairing_method})"
    )
    click.echo(f"Unmodified pairs: {result.unmodified_pairs}")
    click.echo(f"Suggestions: {len(result.suggestions)}")
    click.echo()

    for i, s in enumerate(result.suggestions, 1):
        icon = {"modified": "~", "added": "+", "deleted": "-"}.get(s.type, "?")
        sid = f" [{s.slide_id}]" if s.slide_id else ""
        click.echo(f"  {icon} {i}. [{s.type}]{sid}")
        click.echo(f"    {s.suggestion}")
        click.echo()
