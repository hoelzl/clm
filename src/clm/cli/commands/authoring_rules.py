"""Look up course authoring rules."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.slides.authoring_rules import get_authoring_rules


@click.command("authoring-rules")
@click.option(
    "--course-spec",
    default=None,
    help="Course spec path or slug (e.g. 'machine-learning-azav').",
)
@click.option(
    "--slide-path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to a slide file; resolves to the course(s) containing it.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course data directory (contains course-specs/, slides/). Default: cwd.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def authoring_rules_cmd(
    course_spec: str | None,
    slide_path: Path | None,
    data_dir: Path | None,
    as_json: bool,
):
    """Look up merged authoring rules for a course or slide file.

    Reads _common.authoring.md and per-course .authoring.md files from
    the course-specs/ directory and returns the merged result.

    At least one of --course-spec or --slide-path must be provided.

    \b
    Examples:
        clm authoring-rules --course-spec machine-learning-azav
        clm authoring-rules --slide-path slides/module_550/topic_010/slides_010.py
        clm authoring-rules --course-spec ml-azav --json
    """
    if not course_spec and not slide_path:
        raise click.UsageError("At least one of --course-spec or --slide-path is required.")

    if data_dir is None:
        data_dir = Path.cwd()

    result = get_authoring_rules(
        data_dir,
        course_spec=course_spec,
        slide_path=str(slide_path) if slide_path else None,
    )

    if as_json:
        d: dict = {
            "has_common_rules": result.common_rules is not None,
            "course_rules": [
                {"course_spec": e.course_spec, "rules": e.rules} for e in result.course_rules
            ],
            "merged": result.merged,
        }
        if result.notes:
            d["notes"] = result.notes
        click.echo(json.dumps(d, indent=2))
        return

    # Human-readable output
    if result.notes:
        for note in result.notes:
            click.echo(f"NOTE: {note}")
        click.echo()

    click.echo(result.merged)
