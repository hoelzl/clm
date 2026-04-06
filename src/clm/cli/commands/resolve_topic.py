"""Resolve a topic ID to its filesystem path."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.topic_resolver import (
    get_course_topic_ids,
)
from clm.core.topic_resolver import (
    resolve_topic as _resolve_topic,
)


@click.command("resolve-topic")
@click.argument("topic_id")
@click.option(
    "--course-spec",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Scope resolution to topics referenced by this course spec.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from --course-spec or cwd.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def resolve_topic_cmd(
    topic_id: str,
    course_spec: Path | None,
    data_dir: Path | None,
    as_json: bool,
):
    """Resolve a topic ID to its filesystem path.

    TOPIC_ID is the topic identifier (e.g., "what_is_ml") or a glob
    pattern (e.g., "what_is_ml*"). Matching is by exact suffix against
    topic directory names (the part after topic_NNN_).

    \b
    Examples:
        clm resolve-topic what_is_ml
        clm resolve-topic "decorators*"
        clm resolve-topic intro --course-spec course-specs/python.xml
    """
    slides_dir = _resolve_slides_dir(data_dir, course_spec)

    course_topic_ids = None
    if course_spec:
        try:
            spec = CourseSpec.from_file(course_spec)
            course_topic_ids = get_course_topic_ids(spec)
        except CourseSpecError as e:
            raise click.ClickException(f"Failed to parse course spec: {e}") from None

    result = _resolve_topic(topic_id, slides_dir, course_topic_ids=course_topic_ids)

    if as_json:
        click.echo(json.dumps(_result_to_dict(result), indent=2))
        return

    if result.glob:
        if not result.matches:
            raise click.ClickException(f"No topics match pattern '{topic_id}'")
        for m in result.matches:
            click.echo(f"{m.topic_id}\t{m.path}")
    elif result.ambiguous:
        lines = [f"Topic '{topic_id}' is ambiguous — found in multiple modules:"]
        for alt in result.alternatives:
            lines.append(f"  {alt.module}: {alt.path}")
        raise click.ClickException("\n".join(lines))
    elif result.path is None:
        raise click.ClickException(f"Topic '{topic_id}' not found")
    else:
        click.echo(result.path)


def _resolve_slides_dir(data_dir: Path | None, course_spec: Path | None) -> Path:
    """Determine the slides/ directory from available arguments."""
    if data_dir:
        return data_dir / "slides"
    if course_spec:
        # course specs are typically in <root>/course-specs/
        return course_spec.parent.parent / "slides"
    return Path.cwd() / "slides"


def _result_to_dict(result) -> dict:
    """Convert a ResolutionResult to a JSON-serializable dict."""
    d: dict = {"topic_id": result.topic_id}

    if result.glob:
        d["glob"] = True
        d["matches"] = [
            {
                "topic_id": m.topic_id,
                "path": str(m.path),
                "path_type": m.path_type,
                "module": m.module,
            }
            for m in result.matches
        ]
    else:
        d["path"] = str(result.path) if result.path else None
        d["path_type"] = result.path_type
        d["slide_files"] = [str(f) for f in result.slide_files]
        d["ambiguous"] = result.ambiguous
        if result.alternatives:
            d["alternatives"] = [
                {
                    "topic_id": a.topic_id,
                    "path": str(a.path),
                    "path_type": a.path_type,
                    "module": a.module,
                }
                for a in result.alternatives
            ]

    return d
