"""``clm query affected-specs`` — map changed files to affected course specs.

Read-only CI helper (issue #350): feed it ``git diff --name-only`` output and
it reports which course specs' builds those changes can influence, using the
same topic/include/dir-group resolution as the build. Delegates to
:mod:`clm.core.affected_specs`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.core.affected_specs import (
    find_affected_specs,
    render_report,
    report_to_dict,
)


@click.command("affected-specs")
@click.argument("paths", nargs=-1)
@click.option(
    "--spec-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default="course-specs",
    show_default=True,
    help="Directory containing the course spec *.xml files.",
)
@click.option(
    "--course-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course root that input paths are relative to. Default: parent of --spec-dir.",
)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read additional newline-separated paths from stdin.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report for scripting.")
def affected_specs_cmd(
    paths: tuple[str, ...],
    spec_dir: Path,
    course_root: Path | None,
    read_stdin: bool,
    as_json: bool,
) -> None:
    """Map changed paths to the course specs whose builds they can influence.

    PATHS are changed files relative to the course root (pass them as
    arguments and/or pipe them in with --stdin). Every spec in --spec-dir is
    resolved once with the same rules the build uses; each path is then
    attributed to the specs that claim it (topic dirs and the sibling files a
    single-file topic references, <include> sources, <dir-group> paths, ...).

    The mapping fails open: a build-relevant path that no spec claims (Jinja
    macros, shared toplevel dirs, ...) sets "all": true and lists every spec,
    so a CI matrix built from the output never silently skips a course. Only
    clearly build-irrelevant paths (.github/, top-level docs) and content
    invisible to every build (unreferenced topics, _archive dirs) affect
    nothing. The exit code is always 0 — an empty result is data, not an
    error.

    \b
    Examples:
        clm query affected-specs slides/module_240_generics/type_name.hpp
        git diff --name-only $BEFORE $HEAD | clm query affected-specs --stdin --json
        clm query affected-specs --spec-dir course-specs --stdin --json < changed.txt
    """
    all_paths = list(paths)
    if read_stdin:
        all_paths.extend(line.strip() for line in sys.stdin if line.strip())

    spec_files = sorted(spec_dir.glob("*.xml"))
    if not spec_files:
        raise click.ClickException(f"No *.xml specs found in {spec_dir}.")

    report = find_affected_specs(all_paths, spec_dir, course_root=course_root)

    for warning in report.warnings:
        click.echo(f"warning: {warning}", err=True)

    if as_json:
        click.echo(json.dumps(report_to_dict(report), indent=2))
    else:
        click.echo(render_report(report))

    sys.exit(0)
