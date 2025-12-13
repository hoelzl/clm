"""Database management commands.

This module provides commands for managing CLX databases.
"""

import click


@click.group()
def db():
    """Database management commands."""
    pass


@db.command(name="stats")
@click.pass_context
def db_stats(ctx):
    """Show database statistics.

    Displays row counts and sizes for both the jobs and cache databases.

    Examples:
        clx db stats
    """
    from clx.infrastructure.database.db_operations import DatabaseManager
    from clx.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
    from clx.infrastructure.database.job_queue import JobQueue

    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    click.echo("=" * 60)
    click.echo("CLX Database Statistics")
    click.echo("=" * 60)

    # Jobs database stats
    if jobs_db_path.exists():
        click.echo(f"\nJobs Database: {jobs_db_path}")
        with JobQueue(jobs_db_path) as jq:
            stats = jq.get_database_stats()
            click.echo(f"  Size: {stats.get('db_size_mb', 0):.2f} MB")
            click.echo(f"  Jobs: {stats.get('jobs_count', 0)} entries")
            if stats.get("jobs_by_status"):
                for status, count in stats["jobs_by_status"].items():
                    click.echo(f"    - {status}: {count}")
            click.echo(f"  Results Cache: {stats.get('results_cache_count', 0)} entries")
            click.echo(f"  Workers: {stats.get('workers_count', 0)} entries")
            click.echo(f"  Worker Events: {stats.get('worker_events_count', 0)} entries")
    else:
        click.echo(f"\nJobs Database: {jobs_db_path} (not found)")

    # Cache database stats
    if cache_db_path.exists():
        click.echo(f"\nCache Database: {cache_db_path}")
        with DatabaseManager(cache_db_path) as dm:
            stats = dm.get_stats()
            click.echo(f"  Size: {stats.get('db_size_mb', 0):.2f} MB")
            click.echo(f"  Processed Files: {stats.get('processed_files_count', 0)} entries")
            click.echo(f"  Unique Files: {stats.get('unique_files', 0)}")
            click.echo(f"  Processing Issues: {stats.get('processing_issues_count', 0)} entries")

        # Executed notebooks cache (same database)
        with ExecutedNotebookCache(cache_db_path) as nb_cache:
            nb_stats = nb_cache.get_stats()
            click.echo(f"  Executed Notebooks: {nb_stats.get('total_entries', 0)} entries")
    else:
        click.echo(f"\nCache Database: {cache_db_path} (not found)")

    click.echo("")


@db.command(name="prune")
@click.option(
    "--completed-days",
    type=int,
    default=None,
    help="Days to keep completed jobs (default: from config)",
)
@click.option(
    "--failed-days",
    type=int,
    default=None,
    help="Days to keep failed jobs (default: from config)",
)
@click.option(
    "--events-days",
    type=int,
    default=None,
    help="Days to keep worker events (default: from config)",
)
@click.option(
    "--cache-versions",
    type=int,
    default=None,
    help="Number of cache versions to keep per file (default: from config)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be deleted without actually deleting",
)
@click.pass_context
def db_prune(ctx, completed_days, failed_days, events_days, cache_versions, dry_run):
    """Prune old database entries.

    Removes old completed/failed jobs, worker events, and cache entries
    based on retention settings.

    Examples:
        clx db prune                    # Use config defaults
        clx db prune --completed-days=1 # Keep only 1 day of completed jobs
        clx db prune --dry-run          # Show what would be deleted
    """
    from clx.infrastructure.config import get_config
    from clx.infrastructure.database.db_operations import DatabaseManager
    from clx.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
    from clx.infrastructure.database.job_queue import JobQueue

    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    # Get retention config for defaults
    retention = get_config().retention
    completed_days = completed_days or retention.completed_jobs_retention_days
    failed_days = failed_days or retention.failed_jobs_retention_days
    cancelled_days = retention.cancelled_jobs_retention_days
    events_days = events_days or retention.worker_events_retention_days
    cache_versions = cache_versions or retention.cache_versions_to_keep

    if dry_run:
        click.echo("DRY RUN - No changes will be made\n")

    click.echo("Retention settings:")
    click.echo(f"  Completed jobs: {completed_days} days")
    click.echo(f"  Failed jobs: {failed_days} days")
    click.echo(f"  Cancelled jobs: {cancelled_days} days")
    click.echo(f"  Worker events: {events_days} days")
    click.echo(f"  Cache versions: {cache_versions} per file")
    click.echo("")

    total_deleted = 0

    # Prune jobs database
    if jobs_db_path.exists():
        click.echo(f"Pruning jobs database: {jobs_db_path}")
        with JobQueue(jobs_db_path) as jq:
            if dry_run:
                # Get current counts for dry run
                stats = jq.get_database_stats()
                click.echo(f"  Would clean up from {stats.get('jobs_count', 0)} jobs")
            else:
                result = jq.cleanup_all(
                    completed_days=completed_days,
                    failed_days=failed_days,
                    cancelled_days=cancelled_days,
                    events_days=events_days,
                )
                for key, count in result.items():
                    if count > 0:
                        click.echo(f"  Deleted {count} {key.replace('_', ' ')}")
                        total_deleted += count
    else:
        click.echo(f"Jobs database not found: {jobs_db_path}")

    # Prune cache database
    if cache_db_path.exists():
        click.echo(f"\nPruning cache database: {cache_db_path}")
        with DatabaseManager(cache_db_path) as dm:
            if dry_run:
                stats = dm.get_stats()
                click.echo(f"  Would clean up from {stats.get('processed_files_count', 0)} entries")
            else:
                result = dm.cleanup_all(
                    retain_versions=cache_versions,
                    issues_days=failed_days,
                )
                for key, count in result.items():
                    if count > 0:
                        click.echo(f"  Deleted {count} {key.replace('_', ' ')}")
                        total_deleted += count

        # Prune executed notebook cache
        with ExecutedNotebookCache(cache_db_path) as nb_cache:
            if dry_run:
                stats = nb_cache.get_stats()
                click.echo(
                    f"  Would clean up from {stats.get('total_entries', 0)} notebook cache entries"
                )
            else:
                deleted = nb_cache.prune_stale_hashes()
                if deleted > 0:
                    click.echo(f"  Deleted {deleted} stale notebook cache entries")
                    total_deleted += deleted
    else:
        click.echo(f"Cache database not found: {cache_db_path}")

    click.echo("")
    if dry_run:
        click.echo("DRY RUN complete - no changes made")
    else:
        click.echo(f"Prune complete: {total_deleted} total entries deleted")


@db.command(name="vacuum")
@click.option(
    "--which",
    type=click.Choice(["cache", "jobs", "both"], case_sensitive=False),
    default="both",
    help="Which database to vacuum",
)
@click.pass_context
def db_vacuum(ctx, which):
    """Compact databases to reclaim disk space.

    Runs SQLite VACUUM on the selected databases. This can be slow
    for large databases but reclaims disk space after deletions.

    Examples:
        clx db vacuum                # Vacuum both databases
        clx db vacuum --which=jobs   # Vacuum only jobs database
    """
    from clx.infrastructure.database.db_operations import DatabaseManager
    from clx.infrastructure.database.job_queue import JobQueue

    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    if which in ("jobs", "both"):
        if jobs_db_path.exists():
            click.echo(f"Vacuuming jobs database: {jobs_db_path}")
            import os

            size_before = os.path.getsize(jobs_db_path)
            with JobQueue(jobs_db_path) as jq:
                jq.vacuum()
            size_after = os.path.getsize(jobs_db_path)
            saved = size_before - size_after
            click.echo(
                f"  Size: {size_before / 1024 / 1024:.2f} MB -> {size_after / 1024 / 1024:.2f} MB"
            )
            if saved > 0:
                click.echo(f"  Reclaimed: {saved / 1024 / 1024:.2f} MB")
        else:
            click.echo(f"Jobs database not found: {jobs_db_path}")

    if which in ("cache", "both"):
        if cache_db_path.exists():
            click.echo(f"Vacuuming cache database: {cache_db_path}")
            import os

            size_before = os.path.getsize(cache_db_path)
            with DatabaseManager(cache_db_path) as dm:
                dm.vacuum()
            size_after = os.path.getsize(cache_db_path)
            saved = size_before - size_after
            click.echo(
                f"  Size: {size_before / 1024 / 1024:.2f} MB -> {size_after / 1024 / 1024:.2f} MB"
            )
            if saved > 0:
                click.echo(f"  Reclaimed: {saved / 1024 / 1024:.2f} MB")
        else:
            click.echo(f"Cache database not found: {cache_db_path}")


@db.command(name="clean")
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def db_clean(ctx, force):
    """Prune old entries and vacuum databases.

    Combines 'db prune' and 'db vacuum' into a single command
    for comprehensive cleanup.

    Examples:
        clx db clean          # Interactive cleanup
        clx db clean --force  # Skip confirmation
    """
    if not force:
        if not click.confirm("This will delete old entries and compact databases. Continue?"):
            click.echo("Cancelled.")
            return

    # Run prune
    ctx.invoke(db_prune)
    click.echo("")

    # Run vacuum
    ctx.invoke(db_vacuum)

    click.echo("\nCleanup complete!")


# Keep the legacy delete-database command for backwards compatibility
@click.command()
@click.option(
    "--which",
    type=click.Choice(["cache", "jobs", "both"], case_sensitive=False),
    default="both",
    help="Which database to delete",
)
@click.pass_context
def delete_database(ctx, which):
    """Delete CLX databases.

    WARNING: This completely removes the database files. Use 'clx db prune'
    for selective cleanup.

    Examples:
        clx delete-database --which=cache
        clx delete-database --which=jobs
        clx delete-database --which=both
    """
    cache_db_path = ctx.obj["CACHE_DB_PATH"]
    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    deleted = []

    if which in ("cache", "both"):
        if cache_db_path.exists():
            cache_db_path.unlink()
            deleted.append(f"cache database ({cache_db_path})")

    if which in ("jobs", "both"):
        if jobs_db_path.exists():
            jobs_db_path.unlink()
            deleted.append(f"job queue database ({jobs_db_path})")

    if deleted:
        click.echo(f"Deleted: {', '.join(deleted)}")
    else:
        click.echo("No databases found to delete.")
