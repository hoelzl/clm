"""``clm slides slug-report`` — flag low-quality content-derived slugs (gap #6).

After a bulk ``assign-ids --accept-content-derived`` mints thousands of ids,
this reports just the ones worth reviewing: single generic tokens, very short
code-identifier-shaped slugs, and slugs that hit the length cap and lost their
trailing words. Accepts a directory (with the same ``--only`` / ``--exclude``
/ ``--shipping-only`` scoping as ``assign-ids``) or a spec ``.xml`` (resolves
to the decks the spec pulls in).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli.commands.shared import has_deck_scope, resolve_scoped_files
from clm.slides.slug_quality import (
    render_report,
    report_to_dict,
    scan_slug_quality,
)

_SEVERITIES = ["low", "medium", "high"]


def _decks_for_spec(spec_file: Path, data_dir: Path | None) -> list[Path]:
    from clm.core.course_spec import CourseSpec, CourseSpecError
    from clm.core.spec_decks import resolve_spec_decks

    slides_dir = (data_dir / "slides") if data_dir else (spec_file.parent.parent / "slides")
    if not slides_dir.is_dir():
        raise click.ClickException(
            f"Could not locate the slides/ directory at {slides_dir}. Pass --data-dir."
        )
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as exc:
        raise click.ClickException(str(exc)) from None
    return resolve_spec_decks(spec, slides_dir).deck_files


@click.command("slug-report")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--only",
    type=click.Choice(["bilingual", "split"]),
    default=None,
    help="Scope a directory scan to only bilingual decks (no .de/.en tag) or only split halves.",
)
@click.option(
    "--exclude",
    multiple=True,
    metavar="GLOB",
    help="Skip decks matching GLOB (matched against the full path and each path component). "
    "Repeatable.",
)
@click.option(
    "--shipping-only",
    is_flag=True,
    help="Scope a directory scan to decks reachable from course specs (the shipping set).",
)
@click.option(
    "--specs-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="For --shipping-only: directory of *.xml specs. Default: <course-root>/course-specs/.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course data directory (contains slides/). For a spec PATH or --shipping-only.",
)
@click.option(
    "--min-severity",
    type=click.Choice(_SEVERITIES),
    default="low",
    show_default=True,
    help="Only show findings at or above this confidence (high = very-short / generic).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def slug_report_cmd(
    path: Path,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
    data_dir: Path | None,
    min_severity: str,
    as_json: bool,
) -> None:
    """Flag low-quality ``slide_id`` slugs for review.

    PATH is a directory of slide files or a course spec ``.xml``. For a spec,
    the decks it resolves to (its shipping set) are scanned. For a directory,
    the ``--only`` / ``--exclude`` / ``--shipping-only`` flags scope the scan
    the same way ``clm slides assign-ids`` does.

    \b
    Quality signals (a flag means "worth a look", not "wrong"):
      very_short          one token <= 3 chars (cp / df / os).           [high]
      generic             one content-free token (data / true / value).  [high]
      possibly_truncated  hit the 30-char cap; trailing words lost.      [medium]
      single_token        one token (often fine, e.g. introduction).     [low]

    Exit code is 0; this is a report. Use --min-severity high to see only the
    high-confidence cases.
    """
    scoped = has_deck_scope(only, exclude, shipping_only)

    if path.is_file():
        if path.suffix != ".xml":
            raise click.UsageError(
                "A file PATH must be a spec .xml; pass a directory to scan slide files."
            )
        if scoped:
            raise click.UsageError(
                "--only / --exclude / --shipping-only apply to a directory, not a spec file."
            )
        files = _decks_for_spec(path, data_dir)
    elif path.is_dir():
        if scoped:
            files = resolve_scoped_files(
                path,
                only=only,
                exclude=exclude,
                shipping_only=shipping_only,
                specs_dir=specs_dir,
                data_dir=data_dir,
            )
        else:
            from clm.core.topic_resolver import find_slide_files_recursive

            files = list(find_slide_files_recursive(path))
    else:  # pragma: no cover - click guards existence
        raise click.ClickException(f"PATH must be a slide directory or spec .xml: {path}")

    report = scan_slug_quality(files)

    if as_json:
        click.echo(json.dumps(report_to_dict(report), indent=2))
    else:
        click.echo(render_report(report, min_severity=min_severity))

    sys.exit(0)
