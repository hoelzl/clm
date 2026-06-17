"""``clm course targets`` — list the output targets a spec defines."""

from __future__ import annotations

from pathlib import Path

import click

from clm.core.course_spec import CourseSpec, CourseSpecError


@click.command(name="targets")
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
def list_targets(spec_file, output_format):
    """List output targets defined in a course spec file.

    Shows all output targets with their paths, kinds, formats, and languages.

    \b
    Examples:
        clm course targets course.xml
        clm course targets course.xml --format=json
    """
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        if output_format == "json":
            import json

            error_output = {
                "status": "error",
                "error_type": "spec_parsing",
                "file": str(spec_file),
                "message": str(e),
            }
            print(json.dumps(error_output, indent=2))
            raise SystemExit(1) from None
        else:
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1) from None

    # When the spec declares no <output-targets>, the build/git defaults fall
    # back to the shared/trainer/speaker structure (#383). Show that structure
    # so the listing matches what a build actually writes.
    targets = spec.effective_output_targets
    is_default = not spec.output_targets

    if output_format == "json":
        import json

        data = [
            {
                "name": t.name,
                "path": t.path,
                "remote_path": t.remote_path or None,
                "kinds": t.kinds or ["all"],
                "formats": t.formats or ["all"],
                "languages": t.languages or ["all"],
                "is_default": is_default,
            }
            for t in targets
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        if is_default:
            click.echo("No <output-targets> defined; using the default structure:")
        else:
            click.echo("Output Targets:")
        click.echo("=" * 80)
        click.echo("")

        for target in targets:
            kinds_str = ", ".join(target.kinds) if target.kinds else "all"
            formats_str = ", ".join(target.formats) if target.formats else "all"
            languages_str = ", ".join(target.languages) if target.languages else "all"

            click.echo(f"  {target.name}")
            click.echo(f"    Path:      {target.path}")
            click.echo(f"    Kinds:     [{kinds_str}]")
            click.echo(f"    Formats:   [{formats_str}]")
            click.echo(f"    Languages: [{languages_str}]")
            click.echo("")

    return 0
