"""``clm course renumber`` — renumber topic dirs to course-spec order.

Phase 1 of the course-restructure design (issue #589;
``docs/claude/design/course-restructure-move-rename.md`` §5.1): rename
``topic_NNN_<suffix>`` directories so their ordinal prefixes ascend in the
spec's topic order, then rewrite the input-path columns of ``clm_cache.db``
so every cached result and executed notebook keeps hitting — **no kernel
re-execution**. The ordinal is a sort key only; suffix (= topic id), spec
references, cross-references, output paths, and sync ledgers are all
untouched by construction.

Exit codes: ``0`` renumbered (or would-renumber / nothing to do); ``2``
validation or usage error (ambiguous topic, orphan collision, active build,
unknown module, bad width).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import click

from clm.core.course_renumber import (
    RenumberError,
    RenumberPlan,
    apply_renumber,
    plan_renumber,
)


@click.command("renumber")
@click.argument("module", required=False)
@click.option(
    "--spec",
    "spec_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Course spec whose topic order defines the numbering.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from --spec.",
)
@click.option("--start", default=10, show_default=True, help="First ordinal.")
@click.option("--step", default=10, show_default=True, help="Ordinal increment.")
@click.option(
    "--width",
    default=None,
    type=int,
    help="Zero-pad width for ordinals. Default: preserve the module's widest existing ordinal.",
)
@click.option(
    "--report-only",
    "--dry-run",
    "report_only",
    is_flag=True,
    help="Report what would change (including cache-row counts) without touching anything.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
@click.option(
    "--no-cache-migrate",
    is_flag=True,
    help="Skip the clm_cache.db rewrite (accept a one-time re-execution of the moved topics).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Proceed even when the jobs database shows an active build.",
)
@click.pass_context
def renumber_cmd(
    ctx: click.Context,
    module: str | None,
    spec_file: Path,
    data_dir: Path | None,
    start: int,
    step: int,
    width: int | None,
    report_only: bool,
    as_json: bool,
    no_cache_migrate: bool,
    force: bool,
) -> None:
    """Renumber topic directories to match the spec's topic order.

    \b
    MODULE restricts the renumber to one module directory (e.g.
    "module_550_ml_azav"); without it, every module the spec references is
    renumbered. Only canonical ``topic_<digits>_<suffix>`` names are touched —
    the suffix is the topic's identity and is preserved verbatim, so specs,
    cross-references, output paths, and sync ledgers all stay valid. The
    cache-database lookup paths are rewritten in the same run, so the next
    build replays every cached result instead of re-executing.

    \b
    Examples:
        clm course renumber module_550_ml_azav --spec course-specs/ml.xml --report-only
        clm course renumber --spec course-specs/ml.xml --start 10 --step 10
    """
    from clm.core.course_paths import resolve_course_paths
    from clm.core.course_spec import CourseSpec, CourseSpecError

    course_root, _ = resolve_course_paths(spec_file.absolute(), data_dir)
    slides_dir = course_root / "slides"

    try:
        spec = CourseSpec.from_file(spec_file.absolute())
    except CourseSpecError as e:
        _fail(f"Failed to parse course spec: {e}", as_json)

    try:
        plan = plan_renumber(
            spec,
            slides_dir,
            spec_name=spec_file.name,
            module=module,
            start=start,
            step=step,
            width=width,
        )
    except RenumberError as e:
        _fail(str(e), as_json)

    # Issue #589 guard: the migrator (and the git mv, more so) must not race a
    # build. The jobs DB is resolved exactly like the build resolves it.
    if plan.renames and not report_only and not force:
        active = _active_job_count(_db_path(ctx, "JOBS_DB_PATH"))
        if active:
            _fail(
                f"{active} job(s) pending or processing in the jobs database — a build "
                f"appears to be active. Wait for it to finish or pass --force.",
                as_json,
            )

    used_git = False
    if plan.renames and not report_only:
        used_git = apply_renumber(plan)

    cache_report = None
    if plan.renames and not no_cache_migrate:
        cache_report = _migrate_cache(_db_path(ctx, "CACHE_DB_PATH"), plan, dry_run=report_only)

    if as_json:
        click.echo(
            json.dumps(
                _to_dict(plan, cache_report, report_only=report_only, used_git=used_git), indent=2
            )
        )
    else:
        _print_human(plan, cache_report, report_only=report_only, no_cache_migrate=no_cache_migrate)
    sys.exit(0)


def _db_path(ctx: click.Context, key: str) -> Path:
    """The DB path resolved by the ``clm`` entry point (issue #589: never a
    guessed project-root default — always the same DB the build opens)."""
    obj = ctx.obj or {}
    path = obj.get(key)
    if path is None:
        raise click.ClickException(
            f"internal error: {key} not initialized — run this command via the `clm` entry point."
        )
    return Path(path)


def _active_job_count(jobs_db: Path) -> int:
    """Pending/processing jobs in the jobs DB; 0 when the DB or table is absent."""
    if not jobs_db.exists():
        return 0
    try:
        conn = sqlite3.connect(str(jobs_db))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'processing')"
            ).fetchone()
            return int(row[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _migrate_cache(cache_db: Path, plan: RenumberPlan, *, dry_run: bool):
    """Batch every rename's path mappings into ONE transactional rewrite."""
    from clm.infrastructure.database.cache_path_migration import (
        migrate_cache_paths,
        plan_dir_rename,
    )

    mappings = [
        m for op in plan.renames for m in plan_dir_rename(cache_db, op.old_path, op.new_path)
    ]
    return migrate_cache_paths(cache_db, mappings, dry_run=dry_run)


def _to_dict(plan: RenumberPlan, cache_report, *, report_only: bool, used_git: bool) -> dict:
    return {
        "spec": plan.spec_name,
        "slides_dir": str(plan.slides_dir),
        "report_only": report_only,
        "git_mv": used_git,
        "modules": [
            {
                "module": m.module,
                "renames": [
                    {"topic_id": op.topic_id, "old": op.old_path.name, "new": op.new_path.name}
                    for op in m.renames
                ],
                "unchanged": [p.name for p in m.unchanged],
                "skipped": [
                    {"topic_id": s.topic_id, "path": s.path.name, "reason": s.reason}
                    for s in m.skipped
                ],
            }
            for m in plan.modules
        ],
        "missing_topics": list(plan.missing),
        "cache": None
        if cache_report is None
        else {
            "db_path": cache_report.db_path,
            "rows_rewritten": cache_report.rows_rewritten,
            "collisions_dropped": cache_report.collisions_dropped,
            "tables": {t.table: t.rows_rewritten for t in cache_report.tables},
        },
    }


def _print_human(
    plan: RenumberPlan, cache_report, *, report_only: bool, no_cache_migrate: bool
) -> None:
    verb = "would rename" if report_only else "renamed"
    click.echo(f"order: {plan.spec_name}")
    for m in plan.modules:
        click.echo(
            f"{m.module}: {len(m.renames) + len(m.unchanged)} topic(s) — "
            f"{len(m.renames)} {verb}, {len(m.unchanged)} already in order"
        )
        for op in m.renames:
            click.echo(f"  {op.old_path.name}  ->  {op.new_path.name}")
        for s in m.skipped:
            click.echo(f"  skipped {s.path.name}: {s.reason}")
    if plan.missing:
        click.echo(
            f"missing from slides tree (spec-referenced, not found): {', '.join(plan.missing)}"
        )
    if not plan.renames:
        click.echo("nothing to renumber — topic order already matches the spec.")
    elif no_cache_migrate:
        click.echo("cache: migration skipped (--no-cache-migrate); moved topics re-execute once.")
    elif cache_report is not None:
        click.echo(f"cache: {cache_report.summary()}")


def _fail(message: str, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"error": message}, indent=2))
        sys.exit(2)
    raise click.UsageError(message)
