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

CACHE_DB_NAME = "clm-llm.sqlite"


def _record_split_watermark(de_path: Path, en_path: Path, cache_dir: Path | None) -> str | None:
    """Record a sync watermark for a freshly-split pair (Fix #3.1).

    The two halves are in-sync BY CONSTRUCTION of the split, so recording a
    baseline here means the next default ``clm slides sync`` (no ``--baseline``)
    sees a single-language edit as an edit, not the whole deck as new.

    Reuses ``resolve_cache_dir`` so split and sync key the cache identically
    (same worktree-anchoring). Best-effort: returns a warning string instead of
    raising — splitting must never fail because the watermark couldn't be
    recorded. Returns ``None`` on success.
    """
    try:
        from clm.infrastructure.llm.cache import SyncWatermarkCache, resolve_cache_dir
        from clm.slides.sync_apply import _record_watermark

        cache_root = resolve_cache_dir(cli_override=cache_dir)
        cache = SyncWatermarkCache(cache_root / CACHE_DB_NAME)
        try:
            _record_watermark(cache, de_path, en_path)
        finally:
            cache.close()
    except Exception as exc:  # noqa: BLE001 — never let a cache hiccup fail the split
        return f"could not record sync watermark for the split pair: {exc}"
    return None


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
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Directory for the sync watermark recorded for the split pair (default: "
        "--cache-dir > $CLM_CACHE_DIR > tool.clm.cache_dir > <cwd>/.clm-cache/). "
        "Must match what `clm slides sync` resolves so the watermark is found."
    ),
)
@click.option(
    "--no-watermark",
    is_flag=True,
    help=(
        "Do not record a sync watermark for the freshly-split pair. By default "
        "split records one (the halves are in-sync by construction) so the next "
        "`clm slides sync` has a baseline and sees single-language edits as edits."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
def split_cmd(
    source: Path,
    force: bool,
    report_only: bool,
    cache_dir: Path | None,
    no_watermark: bool,
    as_json: bool,
) -> None:
    """Split a bilingual SOURCE slide file into ``<basename>.de.<ext>`` and
    ``<basename>.en.<ext>`` companions.

    \b
    The split is byte-identical: ``unify`` of the two outputs reproduces
    SOURCE exactly. The bilingual ``# {{ header("DE", "EN") }}`` macro
    call is rewritten into ``header_de(...)`` for the DE file and
    ``header_en(...)`` for the EN file (sibling macros defined in
    ``templates_python/macros.j2``); the matching ``# j2 from
    'macros.j2' import header`` import line is rewritten in parallel.
    Shared cells (no ``lang`` attribute) are copied verbatim to both
    outputs.

    \b
    If SOURCE has a sibling voiceover companion (``voiceover_<name>.<ext>``),
    it is split in lockstep into ``voiceover_<name>.de.<ext>`` /
    ``voiceover_<name>.en.<ext>`` so the narration is never orphaned;
    ``for_slide`` / ``vo_anchor`` are preserved. ``--force`` also covers
    overwriting existing companion halves.
    """
    try:
        result = split_in_file(source, force=force, dry_run=report_only)
    except SplitError as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}, indent=2))
        else:
            click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    # Record a watermark for the in-sync pair so the next default sync has a
    # baseline (Fix #3.1). Only when both halves were actually written.
    if result.wrote and not no_watermark:
        warning = _record_split_watermark(Path(result.de_path), Path(result.en_path), cache_dir)
        if warning is not None:
            result.warnings.append(warning)

    if as_json:
        click.echo(json.dumps(_to_dict(result, report_only=report_only), indent=2))
    else:
        _print_human(result, report_only=report_only)


def _print_human(result: SplitResult, *, report_only: bool) -> None:
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
    prefix = "[report-only] " if report_only else ""
    verb = "would write" if report_only else "wrote"
    paths = [result.de_path, result.en_path]
    if result.de_companion and result.en_companion:
        paths += [result.de_companion, result.en_companion]
    for path in paths:
        note = " (overwrote)" if path in result.overwrote else ""
        click.echo(f"{prefix}{verb} {path}{note}")


def _to_dict(result: SplitResult, *, report_only: bool) -> dict[str, object]:
    return {
        "source": result.source,
        "de_path": result.de_path,
        "en_path": result.en_path,
        "wrote": result.wrote,
        "overwrote": result.overwrote,
        "source_companion": result.source_companion,
        "de_companion": result.de_companion,
        "en_companion": result.en_companion,
        "warnings": result.warnings,
        "report_only": report_only,
    }
