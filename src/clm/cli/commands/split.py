"""``clm slides split`` — Phase 5 of the slide-format-redesign.

Wraps :func:`clm.slides.split.split_in_file` with a small CLI surface
mirroring ``assign-ids``: ``--dry-run`` / ``--report-only``, ``--force``,
``--json``. The command is intentionally a single-file operation; bulk
conversion of a course module is left to shell loops or future
``clm slides split --recursive`` work.

Exit codes:

- ``0`` — split succeeded (or would have succeeded in ``--report-only``)
- ``2`` — refused (targets already exist without ``--force``, or input
  is already a split file)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides.split import SplitError, SplitResult, split_in_file


@click.command("split")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing ``.de.py`` / ``.en.py`` companions if present.",
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Compute the split and report what would be written without modifying files.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def split_cmd(source: Path, force: bool, report_only: bool, as_json: bool) -> None:
    """Split a bilingual SOURCE slide file into ``<basename>.de.py`` and
    ``<basename>.en.py`` companions.

    \b
    The split is byte-identical: ``unify`` of the two outputs reproduces
    SOURCE exactly. The bilingual ``# {{ header("DE", "EN") }}`` macro
    call is rewritten into ``header_de(...)`` for the DE file and
    ``header_en(...)`` for the EN file (sibling macros defined in
    ``templates_python/macros.j2``); the matching ``# j2 from
    'macros.j2' import header`` import line is rewritten in parallel.
    Shared cells (no ``lang`` attribute) are copied verbatim to both
    outputs.
    """
    try:
        result = split_in_file(source, force=force, dry_run=report_only)
    except SplitError as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}, indent=2))
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(_to_dict(result, report_only=report_only), indent=2))
    else:
        _print_human(result, report_only=report_only)


def _print_human(result: SplitResult, *, report_only: bool) -> None:
    prefix = "[report-only] " if report_only else ""
    verb = "would write" if report_only else "wrote"
    for path in (result.de_path, result.en_path):
        note = " (overwrote)" if path in result.overwrote else ""
        click.echo(f"{prefix}{verb} {path}{note}")


def _to_dict(result: SplitResult, *, report_only: bool) -> dict[str, object]:
    return {
        "source": result.source,
        "de_path": result.de_path,
        "en_path": result.en_path,
        "wrote": result.wrote,
        "overwrote": result.overwrote,
        "report_only": report_only,
    }
