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

import datetime as dt
import logging
from pathlib import Path

import click

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    language_option,
    output_options,
    spec_argument,
)
from clm.cli.commands.schedule import Bucket, build_buckets, build_schedule
from clm.cohort_calendar.config import (
    CohortCalendarConfig,
    CohortCalendarError,
    load_calendar_config,
)
from clm.cohort_calendar.projection import project
from clm.cohort_calendar.render import (
    assignment_content,
    assignment_date_label,
    render_csv,
    render_ics,
    render_markdown,
)
from clm.cohort_calendar.status import compute_status
from clm.core.course import Course
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError

logger = logging.getLogger(__name__)


def _abs_under(course_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else course_root / path


def _channel_options(func):
    """Shared ``--channel`` / ``--calendar`` / ``--data-dir`` for calendar commands."""
    func = click.option(
        "--data-dir",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Course data directory (contains slides/). Default: inferred from spec.",
    )(func)
    func = click.option(
        "--calendar",
        "calendar_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Explicit path to the cohort calendar TOML (overrides --channel).",
    )(func)
    func = click.option(
        "--channel",
        default="",
        help="Cohort channel name; resolves the calendar file beside its ledger.",
    )(func)
    return func


def _load_config(spec_file: Path, channel: str, calendar_path: Path | None) -> CohortCalendarConfig:
    cal_path = resolve_calendar_path(spec_file, channel, calendar_path)
    try:
        return load_calendar_config(cal_path)
    except CohortCalendarError as e:
        raise click.ClickException(f"Failed to load calendar {cal_path}: {e}") from None


def _resolve_buckets(
    spec_file: Path, language: str, data_dir: Path | None
) -> tuple[Course, list[Bucket]]:
    """Parse + validate the spec, build the course, and flatten the schedule to buckets."""
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
    return course, build_buckets(build_schedule(course, language))


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

    config = _load_config(spec_file, channel, calendar_path)
    course, buckets = _resolve_buckets(spec_file, language, data_dir)

    proj = project(buckets, config)
    for diag in proj.diagnostics:
        click.echo(f"{diag.level}: {diag.message}", err=True)
    if not proj.ok:
        raise click.ClickException(
            "Calendar has errors (see above); fix the calendar file "
            "(or run `clm calendar check`) before exporting."
        )

    namespace = channel or resolve_calendar_path(spec_file, channel, calendar_path).stem.replace(
        ".calendar", ""
    )
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


@click.group("calendar")
def calendar_group() -> None:
    """Inspect a cohort's viewing calendar: validate it or show today's status (#283)."""


@calendar_group.command("check")
@spec_argument
@_channel_options
def check_cmd(
    spec_file: Path, channel: str, calendar_path: Path | None, data_dir: Path | None
) -> None:
    """Validate a cohort calendar against the course schedule.

    Date-free: reports unknown/ambiguous refs, over-full segments (with the
    exact deficit), end overflow, and warnings (free dates, stray inserts).
    Exits non-zero if there are errors — suitable for a pre-push hook.
    """
    config = _load_config(spec_file, channel, calendar_path)
    _, buckets = _resolve_buckets(spec_file, "en", data_dir)
    proj = project(buckets, config)

    for diag in proj.errors:
        click.echo(f"error: {diag.message}", err=True)
    for diag in proj.warnings:
        click.echo(f"warning: {diag.message}", err=True)
    n_err, n_warn = len(proj.errors), len(proj.warnings)
    if n_err:
        click.echo(f"✗ {n_err} error(s), {n_warn} warning(s).", err=True)
        raise SystemExit(1)
    click.echo(f"✓ Calendar OK ({n_warn} warning(s)).")


def _format_status(report, language: str) -> list[str]:
    """Human-readable status lines (pure, for testability)."""
    lines = [f"As of {report.as_of.isoformat()}:", ""]
    if report.reference is None and not report.finished:
        lines.append("No assignments in this calendar.")
        return lines

    if report.finished:
        lines.append("Course finished — all assignments are in the past.")
    elif report.not_started:
        first = report.reference
        lines.append(
            f"Course not started. First class "
            f"{assignment_date_label(first, language)} — {assignment_content(first)}"
        )
    elif report.current is not None:
        a = report.current
        lines.append(
            f"Today: {assignment_date_label(a, language)} — "
            f"{assignment_content(a) or '(no new video)'}"
        )
        if a.plan_label:
            lines.append(f"   plan: {a.plan_label}")
    else:
        lines.append("No class today.")
        if report.reference is not None:
            n = report.reference
            lines.append(f"   next: {assignment_date_label(n, language)} — {assignment_content(n)}")

    if report.drift_days is not None:
        if report.drift_days == 0:
            lines.append("Drift: on schedule.")
        elif report.drift_days > 0:
            lines.append(f"Drift: {report.drift_days} day(s) behind the ideal plan.")
        else:
            lines.append(f"Drift: {abs(report.drift_days)} day(s) ahead of the ideal plan.")

    if report.upcoming:
        lines.append("")
        lines.append("Upcoming:")
        for a in report.upcoming:
            lines.append(
                f"  {assignment_date_label(a, language)} — "
                f"{assignment_content(a) or '(no new video)'}"
            )
    return lines


@calendar_group.command("status")
@spec_argument
@language_option(default="de", aliases=("--lang",), help="Language for titles and labels.")
@_channel_options
@click.option(
    "--as-of",
    "as_of",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Reference date (default: today). For tests, dated handouts, what-if previews.",
)
def status_cmd(
    spec_file: Path,
    language: str,
    channel: str,
    calendar_path: Path | None,
    data_dir: Path | None,
    as_of: dt.datetime | None,
) -> None:
    """Show where a cohort is today vs the plan (the only now-relative command)."""
    language = language.lower()
    config = _load_config(spec_file, channel, calendar_path)
    _, buckets = _resolve_buckets(spec_file, language, data_dir)
    as_of_date = as_of.date() if as_of is not None else dt.date.today()

    report = compute_status(buckets, config, as_of_date)
    for line in _format_status(report, language):
        click.echo(line)
    if report.has_errors:
        click.echo(
            "warning: this calendar has projection errors; run `clm calendar check`.",
            err=True,
        )
