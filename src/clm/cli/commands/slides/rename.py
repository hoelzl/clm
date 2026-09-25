"""``clm slides rename`` — rename a split deck's file stem, atomically (#991).

The stem-level sibling of ``rename-id``: moves both halves and their
separated voiceover companions together (``git mv`` inside a work tree),
re-keys the deck's section in the per-topic sync ledger as a pure rename so
the deck stays warm, migrates the build cache's path-keyed rows so the
renamed deck does not re-execute, and validates the result. See
:mod:`clm.slides.rename_deck`.

Exit codes: ``0`` renamed (or would-rename in ``--report-only``); ``1``
renamed, but the post-rename validation reported errors; ``2`` usage error
(no twin, collision, bad stem, ambiguous companions, ledger collision).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from clm.core.slide_text.pairing import derive_split_pair, order_split_pair, split_lang_tag
from clm.slides.rename_deck import (
    DeckRenameError,
    DeckRenamePlan,
    apply_deck_rename,
    plan_deck_rename,
)


@click.command("rename")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("new_stem")
@click.argument(
    "en_path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--single",
    is_flag=True,
    help="Rename a lone half (and its companion) that has no twin on disk.",
)
@click.option(
    "--no-cache-migrate",
    is_flag=True,
    help="Do not rewrite the build cache's path rows; the renamed deck re-executes once.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    help="Skip the post-rename `clm validate` of the renamed halves.",
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Report what would move / migrate without touching files, ledger or cache.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
@click.pass_context
def rename_cmd(
    ctx: click.Context,
    path: Path,
    new_stem: str,
    en_path: Path | None,
    single: bool,
    no_cache_migrate: bool,
    no_validate: bool,
    report_only: bool,
    as_json: bool,
) -> None:
    """Rename a split deck's file stem to NEW_STEM — pair, companions, ledger, cache.

    \b
    PATH is one half of a split pair (``<deck>.de.<ext>`` / ``<deck>.en.<ext>``
    — the twin is found on disk) or both halves passed explicitly. NEW_STEM is
    the bare new stem (``slides_30_skills``): the language tag and extension
    are kept, so ``slides_30_task_templates.{de,en}.py`` become
    ``slides_30_skills.{de,en}.py``.

    \b
    The rename is atomic across the deck: both halves, their separated
    voiceover companions (``voiceover/voiceover_<stem>.{de,en}.<ext>`` or the
    sibling layout — companions are paired by stem, so they must move with
    the halves), the deck's section in the committed sync ledger (re-keyed,
    never re-fingerprinted — the deck stays warm and ``sync report`` is clean
    afterwards), and the build cache's path-keyed rows (so the deck does not
    re-execute). Files move with ``git mv`` inside a work tree. It refuses
    before touching anything when a target name exists, when a companion
    lives in both layouts, or when the ledger already has a section under
    NEW_STEM. Course specs need no change: topics resolve by directory and
    decks are discovered on disk. Evergreen artifacts that carry deck stems
    (the cohort ``video-schedule.csv``) must be regenerated — the report says
    so.
    """
    try:
        de, en = _resolve_pair(path, en_path, single=single)
        plan = plan_deck_rename(de, en, new_stem)
    except (click.UsageError, DeckRenameError) as exc:
        message = exc.format_message() if isinstance(exc, click.UsageError) else str(exc)
        if as_json:
            click.echo(json.dumps({"error": message}, indent=2))
            sys.exit(2)
        raise click.UsageError(message) from None

    cache_report = None
    used_git = False
    ledger_migrated = False
    if not report_only:
        used_git, ledger_migrated = apply_deck_rename(plan)
    if not no_cache_migrate:
        cache_report = _migrate_cache(ctx, plan, dry_run=report_only)

    validation: dict | None = None
    if not report_only and not no_validate:
        validation = _validate(plan)

    payload = _to_dict(
        plan,
        report_only=report_only,
        used_git=used_git,
        ledger_migrated=ledger_migrated,
        cache_report=cache_report,
        validation=validation,
    )
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        _print_human(payload)
    sys.exit(1 if validation is not None and validation["errors"] else 0)


def _resolve_pair(
    path: Path, en_path: Path | None, *, single: bool
) -> tuple[Path | None, Path | None]:
    """Resolve the argument(s) to an ordered ``(de, en)`` pair (``None`` for
    the missing half under ``--single``)."""
    path = path.resolve()
    if en_path is not None:
        pair = order_split_pair(path, en_path.resolve())
        if pair is None:
            raise click.UsageError(
                f"{path.name} and {en_path.name} are not the two halves of one split deck."
            )
        return pair
    tag = split_lang_tag(path)
    if tag is None:
        raise click.UsageError(
            f"{path.name} has no .de/.en language tag; pass a split half "
            "(<deck>.de.<ext>) or both halves explicitly."
        )
    pair = derive_split_pair(path)
    if pair is None:
        if single:
            return (path, None) if tag == "de" else (None, path)
        other = "EN" if tag == "de" else "DE"
        raise click.UsageError(
            f"no {other} twin found next to {path.name}; pass both halves explicitly, "
            "or --single to rename this half alone."
        )
    return pair


def _migrate_cache(ctx: click.Context, plan: DeckRenamePlan, *, dry_run: bool):
    """Rewrite the cache rows keyed on the moved files (one transaction).

    A ``PathMapping.old`` must be the path *exactly as stored* — the build
    stores the course-root-derived path, which need not equal the resolved
    CLI argument (a junction / ``subst`` drive, a lower-case drive letter).
    So, like ``course renumber``'s ``plan_dir_rename``, the mappings are
    built from the DB's own distinct values: every stored path that names one
    of the moved files (compared as resolved, case-normalised paths) maps to
    the same spelling with the new filename.
    """
    from clm.infrastructure.database.cache_path_migration import (
        PathMapping,
        migrate_cache_paths,
        stored_input_paths,
    )

    obj = ctx.obj or {}
    cache_db = obj.get("CACHE_DB_PATH")
    if cache_db is None:
        return None
    cache_db = Path(cache_db)
    if not cache_db.exists():
        return None

    def _norm(path: Path) -> str:
        try:
            return os.path.normcase(str(path.resolve()))
        except OSError:
            return os.path.normcase(str(path))

    by_old = {_norm(m.old): m for m in plan.moves}
    mappings: list[PathMapping] = []
    for stored in stored_input_paths(cache_db):
        move = by_old.get(_norm(Path(stored)))
        if move is not None:
            mappings.append(PathMapping(stored, str(Path(stored).with_name(move.new.name))))
    return migrate_cache_paths(cache_db, mappings, dry_run=dry_run)


def _validate(plan: DeckRenamePlan) -> dict:
    """Run `clm validate` on the renamed halves; companions are read with them."""
    from clm.slides.validator import validate_file

    errors: list[dict] = []
    warnings = 0
    for move in plan.moves:
        if move.role.endswith("_companion"):
            continue
        result = validate_file(move.new)
        for finding in result.findings:
            if finding.severity == "error":
                errors.append(
                    {
                        "file": finding.file,
                        "line": finding.line,
                        "category": finding.category,
                        "message": finding.message,
                    }
                )
            else:
                warnings += 1
    return {"errors": errors, "warning_count": warnings}


def _to_dict(
    plan: DeckRenamePlan,
    *,
    report_only: bool,
    used_git: bool,
    ledger_migrated: bool,
    cache_report,
    validation: dict | None,
) -> dict:
    return {
        "old_stem": plan.old_stem,
        "new_stem": plan.new_stem,
        "report_only": report_only,
        "moves": [{"role": m.role, "old": str(m.old), "new": str(m.new)} for m in plan.moves],
        "git_mv": used_git,
        "ledger": str(plan.ledger_path),
        "ledger_section": plan.ledger_has_section,
        "ledger_migrated": ledger_migrated,
        "cache": None
        if cache_report is None
        else {
            "db_path": cache_report.db_path,
            "dry_run": cache_report.dry_run,
            "rows_rewritten": cache_report.rows_rewritten,
            "collisions_dropped": cache_report.collisions_dropped,
        },
        "validation": validation,
        "warnings": list(plan.warnings),
    }


def _print_human(payload: dict) -> None:
    verb = "would rename" if payload["report_only"] else "renamed"
    click.echo(f'{verb} deck "{payload["old_stem"]}" → "{payload["new_stem"]}"')
    for move in payload["moves"]:
        click.echo(f"  {move['role']:<13} {Path(move['old']).name} → {Path(move['new']).name}")
    if not payload["report_only"] and payload["git_mv"]:
        click.echo("  files: moved with git mv")
    if payload["ledger_section"]:
        state = "would be re-keyed" if payload["report_only"] else "re-keyed"
        state = state if payload["report_only"] or payload["ledger_migrated"] else "NOT re-keyed"
        click.echo(f"  ledger: section {state} (recorded baselines carried, deck stays warm)")
    else:
        click.echo("  ledger: no section for this deck (cold / not recorded) — nothing to migrate")
    cache = payload["cache"]
    if cache is None:
        click.echo("  cache: not migrated (no cache database, or --no-cache-migrate)")
    else:
        verb_c = "would rewrite" if cache["dry_run"] else "rewrote"
        click.echo(f"  cache: {verb_c} {cache['rows_rewritten']} path row(s)")
    validation = payload["validation"]
    if validation is not None:
        if validation["errors"]:
            click.echo(f"  validate: {len(validation['errors'])} error(s) on the renamed deck:")
            for err in validation["errors"]:
                where = (
                    f"{Path(err['file']).name}:{err['line']}"
                    if err["line"]
                    else Path(err["file"]).name
                )
                click.echo(f"    {where}: {err['message']}")
        else:
            click.echo(
                f"  validate: OK ({validation['warning_count']} warning(s))"
                if validation["warning_count"]
                else "  validate: OK"
            )
    for warning in payload["warnings"]:
        click.echo(f"  note: {warning}")
