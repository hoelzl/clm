"""``clm slides referenced-by`` — which spec(s)/topic(s) pull a deck in.

Reverse lookup over the same build-faithful resolution as ``clm course
decks`` (see ``clm.core.spec_decks``).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.core.spec_decks import find_deck_references


@click.command("referenced-by")
@click.argument(
    "deck",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--specs-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of *.xml specs to search. Default: <course-root>/course-specs/.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from the deck.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def referenced_by_cmd(
    deck: Path,
    specs_dir: Path | None,
    data_dir: Path | None,
    as_json: bool,
) -> None:
    """Show which spec(s)/topic(s) pull DECK into their shipping set.

    Reverse of ``clm course decks``. A deck reachable from no spec is reported as
    unreferenced — useful for spotting orphaned or superseded decks before a
    corpus-wide change.

    \b
    Examples:
        clm slides referenced-by slides/module_x/topic_y/slides_intro.py
        clm slides referenced-by slides_intro.py --specs-dir course-specs/
    """
    deck = deck.resolve()
    course_root = _course_root_for_deck(deck, data_dir)
    slides_dir = course_root / "slides"
    if specs_dir is None:
        specs_dir = course_root / "course-specs"
    if not specs_dir.is_dir():
        raise click.ClickException(
            f"Specs directory not found: {specs_dir}. Pass --specs-dir explicitly."
        )

    spec_files = sorted(specs_dir.glob("*.xml"))
    references = find_deck_references(deck, spec_files, slides_dir)

    if as_json:
        payload = {
            "deck": str(deck),
            "specs_dir": str(specs_dir),
            "referenced": bool(references),
            "references": [
                {
                    "spec": str(r.spec_path),
                    "topic_id": r.topic_id,
                    "section": r.section,
                    "resolved_module": r.resolved_module,
                }
                for r in references
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    if not references:
        click.echo(f"unreferenced: {deck.name} is pulled in by no spec in {specs_dir}")
        return
    for ref in references:
        click.echo(f"{ref.spec_path.name}\t{ref.topic_id}\t{ref.section}")


def _course_root_for_deck(deck: Path, data_dir: Path | None) -> Path:
    """Infer the course root from a deck path (``…/slides/module_*/topic/deck``)."""
    if data_dir is not None:
        return data_dir
    for parent in deck.parents:
        if parent.name == "slides":
            return parent.parent
    raise click.ClickException(
        "Could not infer the course root from the deck path (no 'slides/' "
        "ancestor). Pass --data-dir explicitly."
    )
