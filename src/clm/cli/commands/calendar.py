"""``clm export calendar`` — a per-cohort viewing calendar on real dates (#283).

Projects the course schedule (the same ordered day-buckets ``export schedule``
produces) onto one cohort's real calendar dates, using the cohort's hand-edited
``release/<channel>.calendar.toml``. Emits a trainer-facing Markdown/CSV view or
the student-facing ``.ics`` feed.

The calendar file is addressed either by ``--channel NAME`` (resolved beside the
channel's ledger in the spec's ``<release-channels>``) or by an explicit
``--calendar PATH``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    language_option,
    output_options,
    spec_argument,
)
from clm.cli.commands.schedule import build_buckets, build_schedule
from clm.cohort_calendar.config import CohortCalendarError, load_calendar_config
from clm.cohort_calendar.projection import project
from clm.cohort_calendar.render import render_csv, render_ics, render_markdown
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError

logger = logging.getLogger(__name__)


def _abs_under(course_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else course_root / path


def resolve_calendar_path(spec_file: Path, channel_name: str, explicit: Path | None) -> Path:
    """Locate a cohort's calendar file: ``--calendar`` wins, else from the channel.

    The conventional path lives beside the channel's ledger, named
    ``<channel>.calendar.toml`` (e.g. ledger ``release/jan.txt`` →
    ``release/jan.calendar.toml``).
    """
    if explicit is not None:
        return explicit
    if not channel_name:
        raise click.UsageError("Pass --channel NAME or --calendar PATH.")
    spec = CourseSpec.from_file(spec_file)
    channels = spec.release_channels
    if channels is None:
        raise click.ClickException(
            f"{spec_file} has no <release-channels> block; pass --calendar PATH "
            "instead of --channel."
        )
    channel = channels.channel(channel_name)
    if channel is None:
        available = ", ".join(c.name for c in channels.channels) or "(none defined)"
        raise click.ClickException(
            f"Unknown channel {channel_name!r}. Defined channels: {available}."
        )
    course_root, _ = resolve_course_paths(spec_file)
    ledger = _abs_under(course_root, channel.ledger)
    return ledger.parent / f"{channel_name}.calendar.toml"


@click.command("calendar")
@spec_argument
@language_option(
    default="de",
    aliases=("--lang",),
    help="Language for deck titles and weekday labels.",
)
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["md", "csv", "ics"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output format (ics = subscribable student feed).",
)
@click.option(
    "--channel",
    default="",
    help="Cohort channel name; resolves the calendar file beside its ledger.",
)
@click.option(
    "--calendar",
    "calendar_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit path to the cohort calendar TOML (overrides --channel).",
)
@output_options
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from spec.",
)
def calendar(
    spec_file: Path,
    language: str,
    output_format: str,
    channel: str,
    calendar_path: Path | None,
    output_file: Path | None,
    output_dir: Path | None,
    data_dir: Path | None,
) -> None:
    """Project the course schedule onto a cohort's real calendar dates.

    \b
    Examples:
        clm export calendar course.xml --channel jan          # German Markdown
        clm export calendar course.xml --channel jan -f ics   # student .ics feed
        clm export calendar course.xml --calendar c.toml -L en -f csv
        clm export calendar course.xml --channel jan -o jan.ics -f ics
    """
    language = language.lower()
    output_format = output_format.lower()
    check_exclusive_output(output_file, output_dir)

    cal_path = resolve_calendar_path(spec_file, channel, calendar_path)
    try:
        config = load_calendar_config(cal_path)
    except CohortCalendarError as e:
        raise click.ClickException(f"Failed to load calendar {cal_path}: {e}") from None

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None
    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    course_root, _ = resolve_course_paths(spec_file, data_dir=data_dir)
    course = Course.from_spec(spec, course_root, output_root=None)
    buckets = build_buckets(build_schedule(course, language))

    proj = project(buckets, config)
    for diag in proj.diagnostics:
        click.echo(f"{diag.level}: {diag.message}", err=True)
    if not proj.ok:
        raise click.ClickException(
            "Calendar has errors (see above); fix the calendar file "
            "(or run `clm calendar check`) before exporting."
        )

    namespace = channel or cal_path.stem.replace(".calendar", "")
    if output_format == "csv":
        content = render_csv(proj, language)
        ext = "csv"
    elif output_format == "ics":
        content = render_ics(course.name[language], proj, namespace=namespace)
        ext = "ics"
    else:
        content = render_markdown(course.name[language], proj, language)
        ext = "md"

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        from clm.core.utils.text_utils import sanitize_file_name

        title = sanitize_file_name(course.name[language])
        tag = channel or language
        file_path = output_dir / f"{title}-calendar-{tag}.{ext}"
        file_path.write_text(content, encoding="utf-8")
        click.echo(f"Written: {file_path}")
    elif output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")
    else:
        click.echo(content, nl=False)
