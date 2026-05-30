"""``clm cassette`` command group: offline cassette diagnostics and repair.

Currently hosts the ``doctor`` subcommand (issue #125), which detects and
optionally repairs chain-orphan interactions in canonical HTTP-replay
cassettes. See :mod:`clm.workers.notebook.cassette_doctor` for the detection
and repair logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.cli.commands.shared import cli_console, get_logger
from clm.core.course_paths import resolve_course_paths
from clm.workers.notebook.cassette_doctor import (
    DEFAULT_MIN_TEXT_LEN,
    CassetteReport,
    diagnose_cassettes,
    iter_cassette_paths,
)

logger = get_logger(__name__)


@click.group("cassette")
def cassette_group() -> None:
    """Inspect and repair HTTP-replay cassettes."""


def _resolve_walk_root(spec_file: Path | None) -> Path:
    """Resolve the directory tree to walk for cassettes.

    When a spec file is given, cassettes live alongside the source ``.py``
    files under the course root (resolved the same way ``clm build`` does).
    Without a spec, the current working directory is walked — convenient for
    repairing a single topic directory in place.
    """
    if spec_file is None:
        return Path.cwd()
    course_root, _ = resolve_course_paths(spec_file)
    return course_root


def _render_text_report(reports: list[CassetteReport], *, fix: bool) -> None:
    """Print a human-readable per-cassette report to the console."""
    console = cli_console
    total_orphans = 0
    total_fixed = 0
    inspected = 0
    skipped = 0

    for report in reports:
        if report.error is not None:
            skipped += 1
            console.print(f"[yellow]! {report.path}[/yellow]: skipped ({report.error})")
            continue
        inspected += 1
        if not report.has_orphans:
            continue
        total_orphans += len(report.orphans)
        if report.fixed:
            total_fixed += 1
        status = " [green](repaired)[/green]" if report.fixed else ""
        console.print(
            f"[bold]{report.path}[/bold]: "
            f"{len(report.orphans)} chain-orphan(s) "
            f"of {report.interaction_count} interaction(s){status}"
        )
        for orphan in report.orphans:
            console.print(
                f"    [{orphan.index}] {orphan.method} {orphan.uri}\n"
                f"        request-body: {orphan.request_fingerprint}\n"
                f"        response ({orphan.text_len} chars): "
                f"{orphan.text_excerpt!r}"
            )

    console.print()
    console.print(
        f"Cassettes inspected: {inspected}" + (f"  (skipped {skipped})" if skipped else "")
    )
    console.print(f"Chain-orphans found: {total_orphans}")
    if fix:
        console.print(f"Cassettes repaired:  {total_fixed}")
    elif total_orphans:
        console.print(
            "Re-run with [bold]--fix[/bold] to remove orphan interactions "
            "so the next build re-records them."
        )


@cassette_group.command("doctor")
@click.argument(
    "spec-file",
    required=False,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--fix",
    is_flag=True,
    default=False,
    help=(
        "Rewrite cassettes to drop chain-orphan interactions so the next "
        "build re-records them. Default off (diagnostic only)."
    ),
)
@click.option(
    "--min-text-len",
    type=click.IntRange(min=1),
    default=DEFAULT_MIN_TEXT_LEN,
    show_default=True,
    help=(
        "Minimum extracted response-content length (chars) for an "
        "interaction to be treated as a chain-edge candidate. Shorter "
        "responses are too likely to appear incidentally in unrelated "
        "request bodies to flag reliably."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON report on stdout instead of text.",
)
def doctor(spec_file: Path | None, fix: bool, min_text_len: int, as_json: bool) -> None:
    """Detect (and optionally repair) orphan chain-pointing cassette interactions.

    Walks every ``*.http-cassette.yaml`` under the spec's source tree (or the
    current directory when SPEC-FILE is omitted). For each interaction, the
    chat-completion text content is extracted and treated as a chain-edge
    candidate when at least ``--min-text-len`` characters long. If no other
    interaction's request body embeds that text, the interaction is flagged
    as a chain-orphan — almost always a chain-opener whose closer was never
    recorded (the canonical-poisoning case from issue #115 that the
    completion-marker fix cannot retroactively repair).

    \b
    Examples:
        clm cassette doctor course.xml                 # report orphans
        clm cassette doctor course.xml --fix           # remove them
        clm cassette doctor course.xml --json          # machine-readable
        clm cassette doctor course.xml --min-text-len 80
        clm cassette doctor                            # walk current dir
    """
    root = _resolve_walk_root(spec_file)
    paths = list(iter_cassette_paths(root))

    reports = diagnose_cassettes(paths, min_text_len=min_text_len, fix=fix)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "root": str(root),
                    "min_text_len": min_text_len,
                    "fix": fix,
                    "cassette_count": len(reports),
                    "orphan_count": sum(len(r.orphans) for r in reports),
                    "cassettes": [r.to_dict() for r in reports],
                },
                indent=2,
            )
        )
        return

    if not paths:
        cli_console.print(f"No cassettes found under {root}.")
        return

    _render_text_report(reports, fix=fix)
