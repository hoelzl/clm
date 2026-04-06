"""Extract a single-language view of a bilingual slide file."""

from __future__ import annotations

from pathlib import Path

import click

from clm.slides.language_tools import get_language_view


@click.command("language-view")
@click.argument(
    "file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "language",
    type=click.Choice(["de", "en"]),
)
@click.option(
    "--include-voiceover",
    is_flag=True,
    help="Include voiceover cells.",
)
@click.option(
    "--include-notes",
    is_flag=True,
    help="Include speaker-notes cells.",
)
def language_view_cmd(
    file: Path,
    language: str,
    include_voiceover: bool,
    include_notes: bool,
):
    """Extract a single-language view of a bilingual slide file.

    Shows only cells for LANGUAGE plus language-neutral cells, with
    [original line N] annotations for mapping back to the source.

    \b
    Examples:
        clm language-view slides/topic/slides_intro.py de
        clm language-view slides/topic/slides_intro.py en --include-voiceover
        clm language-view slides/topic/slides_intro.py de --include-notes
    """
    output = get_language_view(
        file,
        language,
        include_voiceover=include_voiceover,
        include_notes=include_notes,
    )
    click.echo(output, nl=False)
