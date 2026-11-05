"""``clm export agenda`` — the certification agenda with teaching units (issue #1163).

AZAV (and similar) certification needs a day-by-day agenda: per week a
table ``Tag | Thema | Inhalte | Form | UE`` (day, block, contents,
theory/practice, teaching units), a weekly subtitle and an outcome, plus an
overview of the weeks and their units. The course spec carries this in its
agenda layer — ``<section>`` = week (with an optional ``<agenda>``),
``<subsection>`` = day, ``<block units=… form=…>`` = time block; see
``clm info spec-files``. This command renders it as Markdown (default) or
CSV.

The agenda is pure spec data: no topic is resolved against the filesystem,
so it renders for planning specs whose material does not exist yet. Week and
day selection is shared with ``clm validate`` (:mod:`clm.core.agenda`).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import click

from clm.cli.commands._export_shared import (
    check_exclusive_output,
    language_option,
    output_options,
    spec_argument,
)
from clm.cli.commands.export.schedule import _md_cell
from clm.core.agenda import FORM_LABELS, AgendaWeek, build_agenda, strip_week_prefix
from clm.core.course_spec import BlockSpec, CourseSpec, CourseSpecError
from clm.core.utils.text_utils import sanitize_file_name

# Localized labels, per language.
_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "overview": "Überblick",
        "week": "Woche",
        "title": "Thema",
        "units": "UE",
        "units_inline": "UE",
        "total": "Gesamtumfang",
        "day": "Tag",
        "block": "Thema",
        "contents": "Inhalte",
        "form": "Form",
        "week_total": "Summe Woche {n}",
        "outcome": "Ergebnis",
        "scope": "Umfang: {total} UE · 1 UE = {minutes} Minuten",
        "planned": "geplant",
        "adapt": "anzupassen",
        "no_days": "_Keine Tage geplant._",
    },
    "en": {
        "overview": "Overview",
        "week": "Week",
        "title": "Title",
        "units": "Units",
        "units_inline": "units",
        "total": "Total",
        "day": "Day",
        "block": "Block",
        "contents": "Contents",
        "form": "Form",
        "week_total": "Total week {n}",
        "outcome": "Outcome",
        "scope": "Scope: {total} units · 1 unit = {minutes} minutes",
        "planned": "planned",
        "adapt": "to adapt",
        "no_days": "_No days scheduled._",
    },
}

_CSV_FIELDS = [
    "week",
    "week_title",
    "day",
    "weekday",
    "block",
    "contents",
    "form",
    "units",
    "status",
    "standalone",
    "pace",
    "topics",
]

_EMPTY = "—"


def _oneline(text: str) -> str:
    """Collapse the whitespace of a (possibly multi-line) spec text."""
    return " ".join(text.split())


def _block_name(block: BlockSpec, language: str) -> str:
    return _oneline(block.name[language]) if block.name is not None else ""


def _block_contents(block: BlockSpec, language: str) -> str:
    return _oneline(block.contents[language]) if block.contents is not None else ""


def render_agenda_markdown(spec: CourseSpec, weeks: list[AgendaWeek], language: str) -> str:
    """Render the agenda as Markdown: overview table, then one table per week.

    Blocks with ``status="planned"`` / ``"adapt"`` carry an italic marker after
    their name; a block without ``units`` shows ``—`` in the units column.
    """
    labels = _LABELS[language]
    unit_minutes = spec.agenda_targets.unit_minutes if spec.agenda_targets else 45
    total = sum(week.units for week in weeks)
    lines: list[str] = [
        f"# {_oneline(spec.name[language])}",
        "",
        labels["scope"].format(total=total, minutes=unit_minutes),
        "",
        f"## {labels['overview']}",
        "",
        f"| {labels['week']} | {labels['title']} | {labels['units']} |",
        "|---|---|---|",
    ]
    for week in weeks:
        title = strip_week_prefix(_oneline(week.section.name[language]))
        lines.append(f"| {week.number} | {_md_cell(title)} | {week.units} |")
    lines += [f"| | **{labels['total']}** | **{total}** |", ""]

    for week in weeks:
        section_name = _oneline(week.section.name[language])
        lines += [f"## {section_name} ({week.units} {labels['units_inline']})", ""]
        agenda = week.section.agenda
        if agenda is not None and agenda.subtitle[language].strip():
            lines += [f"_{_oneline(agenda.subtitle[language])}_", ""]
        if not week.days:
            lines += [labels["no_days"], ""]
        else:
            lines += [
                f"| {labels['day']} | {labels['block']} | {labels['contents']} "
                f"| {labels['form']} | {labels['units']} |",
                "|---|---|---|---|---|",
            ]
            for day in week.days:
                if not day.blocks:
                    lines.append(f"| {day.label} | {_EMPTY} |  |  | 0 |")
                    continue
                for block in day.blocks:
                    name = _md_cell(_block_name(block, language)) or _EMPTY
                    if block.status in ("planned", "adapt"):
                        name += f" *({labels[block.status]})*"
                    form = FORM_LABELS[block.form][language] if block.form else ""
                    units = str(block.units) if block.units is not None else _EMPTY
                    contents = _md_cell(_block_contents(block, language))
                    lines.append(f"| {day.label} | {name} | {contents} | {form} | {units} |")
            week_total = labels["week_total"].format(n=week.number)
            lines += [f"| **{week_total}** | | | | **{week.units}** |", ""]
        if agenda is not None and agenda.outcome[language].strip():
            lines += [f"**{labels['outcome']}:** {_oneline(agenda.outcome[language])}", ""]

    return "\n".join(lines).rstrip("\n") + "\n"


def render_agenda_csv(weeks: list[AgendaWeek], language: str) -> str:
    """Render the agenda as CSV: one row per block, every block attribute a column.

    ``form``, ``status`` and ``pace`` carry the spec's language-neutral tokens;
    ``topics`` joins the block's topic ids with spaces; a missing ``units``
    or ``form`` is an empty cell. A day without blocks yields no row.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_FIELDS)
    for week in weeks:
        week_title = _oneline(week.section.name[language])
        for day in week.days:
            weekdays = ",".join(day.subsection.weekdays)
            for block in day.blocks:
                writer.writerow(
                    [
                        week.number,
                        week_title,
                        day.label,
                        weekdays,
                        _block_name(block, language),
                        _block_contents(block, language),
                        block.form or "",
                        "" if block.units is None else block.units,
                        block.status,
                        "true" if block.standalone else "false",
                        block.pace,
                        " ".join(topic.id for topic in block.topics),
                    ]
                )
    return buffer.getvalue()


@click.command()
@spec_argument
@language_option(default="de", aliases=("--lang",), help="Language of the agenda.")
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["md", "csv"], case_sensitive=False),
    default="md",
    show_default=True,
    help="Output format.",
)
@output_options
@click.option(
    "--include-optional",
    "include_optional",
    is_flag=True,
    default=False,
    help='Include modules marked optional="true" (on a <section> or <subsection>).',
)
def agenda(
    spec_file: Path,
    language: str,
    output_format: str,
    output_file: Path | None,
    output_dir: Path | None,
    include_optional: bool,
):
    """Export the certification agenda with teaching units.

    Reads the agenda layer of a course spec — <section> weeks with an
    optional <agenda> subtitle/outcome, <subsection> days and <block> time
    blocks with units, form and contents — and renders an overview table plus
    one table per week. Material on disk is not needed.

    \b
    Examples:
        clm export agenda course.xml                 # German Markdown to stdout
        clm export agenda course.xml -L en           # English agenda
        clm export agenda course.xml -f csv          # CSV, one row per block
        clm export agenda course.xml -o agenda.md    # Write to a file
        clm export agenda course.xml -d ./docs       # Write into a directory
    """
    language = language.lower()
    output_format = output_format.lower()
    check_exclusive_output(output_file, output_dir)

    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    weeks = build_agenda(spec, include_optional=include_optional)
    if not any(day.blocks for week in weeks for day in week.days):
        click.echo(
            "Warning: this spec defines no <block> time blocks; the agenda is "
            'empty. Add <block units="..." form="..."> elements inside the '
            "<subsection> days. See 'clm info spec-files'.",
            err=True,
        )

    if output_format == "csv":
        content = render_agenda_csv(weeks, language)
    else:
        content = render_agenda_markdown(spec, weeks, language)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        title = sanitize_file_name(spec.name[language])
        file_path = output_dir / f"{title}-agenda-{language}.{output_format}"
        file_path.write_text(content, encoding="utf-8")
        click.echo(f"Written: {file_path}")
    elif output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        click.echo(f"Written: {output_file}")
    else:
        click.echo(content, nl=False)
