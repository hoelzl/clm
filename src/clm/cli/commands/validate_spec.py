"""Validate a course specification XML file."""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.core.course_spec import CourseSpecError
from clm.slides.spec_validator import validate_spec


@click.command("validate-spec")
@click.argument(
    "spec_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from spec file location.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option(
    "--include-disabled",
    is_flag=True,
    default=False,
    help="Also validate sections marked 'enabled=\"false\"'. Each finding "
    "from a disabled section has '(disabled)' appended to its message so you "
    "can distinguish roadmap content from active content.",
)
def validate_spec_cmd(
    spec_file: Path,
    data_dir: Path | None,
    as_json: bool,
    include_disabled: bool,
):
    """Validate a course specification XML file.

    Checks that all referenced topic IDs resolve to exactly one existing
    topic directory, that there are no duplicate topic references, and
    that referenced dir-group paths exist.

    \b
    Examples:
        clm validate-spec course-specs/python-basics.xml
        clm validate-spec course-specs/ml-azav.xml --json
        clm validate-spec course-specs/ml-azav.xml --include-disabled
    """
    slides_dir = _resolve_slides_dir(data_dir, spec_file)

    try:
        result = validate_spec(spec_file, slides_dir, include_disabled=include_disabled)
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None

    if as_json:
        click.echo(json.dumps(_result_to_dict(result), indent=2))
        return

    # Human-readable output
    errors = result.errors
    warnings = result.warnings

    if not result.findings:
        click.echo(f"OK — {result.topics_total} topics, no issues found.")
        return

    for f in result.findings:
        icon = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(f.severity, "???")
        click.echo(f"[{icon}] {f.message}")
        if f.suggestion:
            click.echo(f"       {f.suggestion}")
        if f.matches:
            for m in f.matches:
                click.echo(f"       - {m}")

    click.echo()
    click.echo(
        f"{result.topics_total} topics checked: {len(errors)} error(s), {len(warnings)} warning(s)."
    )

    if errors:
        raise SystemExit(1)


def _resolve_slides_dir(data_dir: Path | None, spec_file: Path) -> Path:
    """Determine the slides/ directory."""
    if data_dir:
        return data_dir / "slides"
    # course specs are typically in <root>/course-specs/
    return spec_file.parent.parent / "slides"


def _result_to_dict(result) -> dict:
    """Convert a SpecValidationResult to a JSON-serializable dict."""
    return {
        "course_spec": result.course_spec,
        "topics_total": result.topics_total,
        "findings": [
            {
                k: v
                for k, v in {
                    "severity": f.severity,
                    "type": f.type,
                    "topic_id": f.topic_id,
                    "section": f.section,
                    "message": f.message,
                    "suggestion": f.suggestion or None,
                    "matches": f.matches or None,
                    "sections": f.sections or None,
                }.items()
                if v is not None
            }
            for f in result.findings
        ],
    }
