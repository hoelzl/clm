"""``clm slides coverage-report`` — DE/EN completeness per deck (gap #8).

Separates "deck exists in only one language (needs translation)" from
"bilingual deck off by a cell or two (alignment fix)" among count-mismatch
errors, by counting ``lang="de"`` vs ``lang="en"`` slide cells per deck.
Accepts a directory (with the same ``--only`` / ``--exclude`` /
``--shipping-only`` scoping as ``assign-ids``) or a spec ``.xml`` (resolved to
the decks it pulls in). Delegates to :mod:`clm.slides.lang_coverage`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.cli.commands.shared import has_deck_scope, resolve_scoped_files
from clm.slides.lang_coverage import (
    CoverageStatus,
    render_report,
    report_to_dict,
    scan_coverage,
)

_STATUS_CHOICES = [s.value for s in CoverageStatus]


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


@click.command("coverage-report")
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
    "--status",
    type=click.Choice(_STATUS_CHOICES),
    default=None,
    help="Show only decks with this status (de_only / en_only / imbalanced / balanced).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def coverage_report_cmd(
    path: Path,
    only: str | None,
    exclude: tuple[str, ...],
    shipping_only: bool,
    specs_dir: Path | None,
    data_dir: Path | None,
    status: str | None,
    as_json: bool,
) -> None:
    """Report DE/EN completeness per deck.

    PATH is a directory of slide files or a course spec ``.xml`` (its shipping
    decks are scanned). Each deck is classified by its DE vs EN slide counts:

    \b
      de_only      DE present, EN missing — needs EN translation.
      en_only      EN present, DE missing — needs DE translation.
      imbalanced   both present, counts differ — an alignment fix.
      balanced     equal DE/EN counts (not listed unless --status balanced).

    Split ``.de.py`` / ``.en.py`` halves are scored as one pair; a half whose
    twin is absent counts the missing language as zero. Only slide/subslide
    cells are counted (narrative cells inherit their slide). Exit code is 0.
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
        base: Path | None = None
    elif path.is_dir():
        base = path
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

    report = scan_coverage(files)

    if status is not None:
        wanted = CoverageStatus(status)
        report.entries = [e for e in report.entries if e.status == wanted]

    if as_json:
        click.echo(json.dumps(report_to_dict(report), indent=2))
    else:
        click.echo(render_report(report, base=base))

    sys.exit(0)
