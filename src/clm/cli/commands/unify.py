"""``clm slides unify`` — Phase 5 of the slide-format-redesign.

The inverse of ``clm slides split``: combines a ``<basename>.de.py`` /
``<basename>.en.py`` pair into the bilingual ``<basename>.py``. Shared
cells (no ``lang`` attribute) must be byte-identical between the two
inputs — divergent shared content is an error that surfaces before any
file is written.

Exit codes:

- ``0`` — unify succeeded (or would have succeeded in ``--report-only``)
- ``2`` — refused (target exists without ``--force``, shared cells
  diverge, or files cannot be aligned)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from clm.slides.split import UnifyError, UnifyResult, unify_in_file


@click.command("unify")
@click.argument("de_source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("en_source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--target",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Explicit bilingual target path. Defaults to the basename shared by DE_SOURCE "
        "and EN_SOURCE — e.g. ``foo.de.py`` + ``foo.en.py`` → ``foo.py``."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing target file if present.",
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Compute the unified text and report what would be written without modifying files.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def unify_cmd(
    de_source: Path,
    en_source: Path,
    target: Path | None,
    force: bool,
    report_only: bool,
    as_json: bool,
) -> None:
    """Unify DE_SOURCE and EN_SOURCE into a bilingual companion file.

    \b
    The unify step pairs adjacent DE/EN slide cells back into the
    canonical bilingual order and validates that shared cells agree
    between the two inputs. If a shared cell diverges (someone edited
    the DE file without syncing to EN, or vice versa), unify refuses to
    write — Phase 6's validator extension will surface the same check at
    build time.
    """
    try:
        result = unify_in_file(
            de_source,
            en_source,
            target=target,
            force=force,
            dry_run=report_only,
        )
    except UnifyError as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}, indent=2))
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(_to_dict(result, report_only=report_only), indent=2))
    else:
        _print_human(result, report_only=report_only)


def _print_human(result: UnifyResult, *, report_only: bool) -> None:
    prefix = "[report-only] " if report_only else ""
    verb = "would write" if report_only else "wrote"
    note = " (overwrote)" if result.overwrote else ""
    click.echo(f"{prefix}{verb} {result.target}{note}")


def _to_dict(result: UnifyResult, *, report_only: bool) -> dict[str, object]:
    return {
        "de_source": result.de_source,
        "en_source": result.en_source,
        "target": result.target,
        "wrote": result.wrote,
        "overwrote": result.overwrote,
        "report_only": report_only,
    }
