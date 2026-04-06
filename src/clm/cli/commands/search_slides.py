"""Search for slide files by topic name or slide title."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.search import search_slides as _search_slides


@click.command("search-slides")
@click.argument("query")
@click.option(
    "--course-spec",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Limit search to topics in this course spec.",
)
@click.option(
    "-L",
    "--language",
    type=click.Choice(["de", "en"], case_sensitive=False),
    help="Search titles in this language only.",
)
@click.option(
    "--max-results",
    type=int,
    default=10,
    show_default=True,
    help="Maximum number of results.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: cwd.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def search_slides_cmd(
    query: str,
    course_spec: Path | None,
    language: str | None,
    max_results: int,
    data_dir: Path | None,
    as_json: bool,
):
    """Search for slide files by topic name, title, or keywords.

    Supports fuzzy matching. Install rapidfuzz for best results.

    \b
    Examples:
        clm search-slides "decorators"
        clm search-slides "RAG" --course-spec course-specs/ml.xml
        clm search-slides "Dekoratoren" -L de
    """
    slides_dir = (data_dir or Path.cwd()) / "slides"

    if not slides_dir.is_dir():
        raise click.ClickException(f"Slides directory not found: {slides_dir}")

    results = _search_slides(
        query,
        slides_dir,
        course_spec_path=course_spec,
        language=language,
        max_results=max_results,
    )

    if not results:
        click.echo(f"No results for '{query}'")
        return

    if as_json:
        click.echo(
            json.dumps(
                {
                    "results": [
                        {
                            "score": r.score,
                            "topic_id": r.topic_id,
                            "directory": r.directory,
                            "slides": [
                                {
                                    "file": s.file,
                                    "title_de": s.title_de,
                                    "title_en": s.title_en,
                                }
                                for s in r.slides
                            ],
                            "courses": r.courses,
                        }
                        for r in results
                    ]
                },
                indent=2,
            )
        )
        return

    for r in results:
        title = ""
        if r.slides:
            s = r.slides[0]
            title = s.title_en or s.title_de or ""
        score_str = f"({r.score:.0f})"
        click.echo(f"  {score_str:>6}  {r.topic_id:40s} {title}")
