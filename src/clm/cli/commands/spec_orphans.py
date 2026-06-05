"""``clm spec orphans`` — decks reachable from no spec, plus cruft (gap #7).

The inverse of ``clm spec decks``: scan every spec in a course and report the
decks on disk that *no* spec pulls in, grouped by likely intent (explicit
``_old`` / ``_bak`` = superseded; ``_partN`` / ``_short`` / ``_long`` =
probably-intentional alternate content; everything else = review). Also
surfaces — and optionally removes — gitignored ``.ipynb_checkpoints/`` cruft.
Delegates to :mod:`clm.core.spec_orphans`.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import click

from clm.core.spec_orphans import (
    OrphanKind,
    find_orphans,
    render_report,
    report_to_dict,
)


def _resolve_slides_dir(specs_dir: Path, slides_dir: Path | None, data_dir: Path | None) -> Path:
    if slides_dir is not None:
        return slides_dir
    if data_dir is not None:
        return data_dir / "slides"
    return specs_dir.parent / "slides"


@click.command("orphans")
@click.argument(
    "specs_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--slides-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="The course's slides/ directory. Default: <specs-dir>/../slides.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Course data directory (contains slides/). Alternative to --slides-dir.",
)
@click.option(
    "--kind",
    type=click.Choice([k.value for k in OrphanKind]),
    default=None,
    help="Show only orphans of this intent (superseded / alternate / unknown).",
)
@click.option(
    "--clean-checkpoints",
    is_flag=True,
    help="Delete the .ipynb_checkpoints/ directories found (gitignored cache cruft).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def spec_orphans_cmd(
    specs_dir: Path,
    slides_dir: Path | None,
    data_dir: Path | None,
    kind: str | None,
    clean_checkpoints: bool,
    as_json: bool,
) -> None:
    """List decks reachable from no spec, grouped by likely intent.

    SPECS_DIR is the directory of course spec ``*.xml`` files. Orphans are
    computed against the **union** of every spec (a deck unreferenced by one
    spec may be pulled in by another).

    \b
    Buckets:
      superseded   explicit _old / _bak / _orig / numeric duplicate — usually
                   safe to archive.
      alternate    _partN / _short / _long — probably intentional content; do
                   NOT blindly archive.
      unknown      no recognizable marker — review before acting.

    Plus any gitignored .ipynb_checkpoints/ directories (regenerable cache
    cruft); pass --clean-checkpoints to delete them. Exit code is 0 — this is
    a report.
    """
    resolved_slides = _resolve_slides_dir(specs_dir, slides_dir, data_dir)
    if not resolved_slides.is_dir():
        raise click.ClickException(
            f"Slides directory not found: {resolved_slides}. Pass --slides-dir or --data-dir."
        )

    spec_files = sorted(specs_dir.glob("*.xml"))
    if not spec_files:
        raise click.ClickException(f"No *.xml specs found in {specs_dir}.")

    report = find_orphans(spec_files, resolved_slides)

    removed: list[Path] = []
    if clean_checkpoints:
        for ckpt in report.checkpoints:
            try:
                shutil.rmtree(ckpt)
                removed.append(ckpt)
            except OSError as exc:
                click.echo(f"warning: could not remove {ckpt}: {exc}", err=True)

    if kind is not None:
        wanted = OrphanKind(kind)
        report.orphans = [o for o in report.orphans if o.kind == wanted]

    if as_json:
        payload = report_to_dict(report)
        if clean_checkpoints:
            payload["checkpoints_removed"] = [str(p) for p in removed]
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(render_report(report, resolved_slides))
        if clean_checkpoints:
            click.echo()
            click.echo(f"Removed {len(removed)} .ipynb_checkpoints/ director(ies).")

    sys.exit(0)
