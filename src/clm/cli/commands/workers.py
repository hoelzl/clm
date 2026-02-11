"""Worker management commands.

This module provides commands for listing and managing CLX workers.
"""

from datetime import datetime, timezone
from pathlib import Path

import click


@click.group(name="workers")
def workers_group():
    """Manage CLX workers."""
    pass


@workers_group.command(name="list")
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clm_jobs.db",
    help="Path to the job queue database",
)
@click.option(
    "--format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.option(
    "--status",
    multiple=True,
    type=click.Choice(["idle", "busy", "hung", "dead"], case_sensitive=False),
    help="Filter by status (can specify multiple)",
)
def workers_list(jobs_db_path, format, status):
    """List registered workers.

    Examples:
        clx workers list
        clx workers list --status=idle
        clx workers list --format=json
        clx workers list --status=busy --status=hung
    """
    from clm.infrastructure.workers.discovery import WorkerDiscovery

    jobs_db_path = Path(jobs_db_path)

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        return 1

    # Discover workers
    discovery = WorkerDiscovery(jobs_db_path)
    status_filter = list(status) if status else None
    workers = discovery.discover_workers(status_filter=status_filter)

    if not workers:
        click.echo("No workers found")
        return 0

    if format == "json":
        import json

        data = [
            {
                "id": w.db_id,
                "type": w.worker_type,
                "executor_id": w.executor_id,
                "status": w.status,
                "started_at": w.started_at.isoformat(),
                "last_heartbeat": w.last_heartbeat.isoformat(),
                "jobs_processed": w.jobs_processed,
                "jobs_failed": w.jobs_failed,
                "is_healthy": w.is_healthy,
            }
            for w in workers
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        # Table format
        try:
            from tabulate import tabulate  # type: ignore[import-untyped]
        except ImportError:
            click.echo(
                "Error: tabulate library not installed. Use --format=json instead.",
                err=True,
            )
            return 1

        rows = []
        for w in workers:
            uptime = datetime.now(timezone.utc) - w.started_at
            uptime_str = str(uptime).split(".")[0]

            health = "+" if w.is_healthy else "x"

            rows.append(
                [
                    w.db_id,
                    w.worker_type,
                    w.executor_id[:12] if len(w.executor_id) > 12 else w.executor_id,
                    w.status,
                    health,
                    uptime_str,
                    w.jobs_processed,
                    w.jobs_failed,
                ]
            )

        headers = [
            "ID",
            "Type",
            "Executor",
            "Status",
            "Health",
            "Uptime",
            "Processed",
            "Failed",
        ]
        click.echo(tabulate(rows, headers=headers, tablefmt="simple"))

    return 0


@workers_group.command(name="cleanup")
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clm_jobs.db",
    help="Path to the job queue database",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--all",
    "cleanup_all",
    is_flag=True,
    help="Clean up all workers (not just dead/hung)",
)
def workers_cleanup(jobs_db_path, force, cleanup_all):
    """Clean up dead workers and orphaned processes.

    By default, this removes workers that are:
    - Marked as 'dead' or 'hung' in the database
    - Have stale heartbeats (>60 seconds old)

    Examples:
        clx workers cleanup
        clx workers cleanup --force
        clx workers cleanup --all --force
    """
    from clm.infrastructure.database.job_queue import JobQueue
    from clm.infrastructure.workers.discovery import WorkerDiscovery

    jobs_db_path = Path(jobs_db_path)

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        return 1

    # Discover workers to clean up
    discovery = WorkerDiscovery(jobs_db_path)

    if cleanup_all:
        workers = discovery.discover_workers()
        click.echo("Warning: Cleaning up ALL workers", err=True)
    else:
        workers = discovery.discover_workers(status_filter=["dead", "hung"])

        all_workers = discovery.discover_workers(status_filter=["idle", "busy"])
        stale_workers = [
            w
            for w in all_workers
            if (datetime.now(timezone.utc) - w.last_heartbeat).total_seconds() > 60
        ]
        workers.extend(stale_workers)

    if not workers:
        click.echo("No workers to clean up")
        return 0

    click.echo(f"Found {len(workers)} worker(s) to clean up:")
    for w in workers:
        click.echo(f"  #{w.db_id} ({w.worker_type}, {w.status})")

    if not force:
        if not click.confirm("Remove these workers?"):
            click.echo("Cancelled")
            return 0

    job_queue = JobQueue(jobs_db_path)
    conn = job_queue._get_conn()

    cleaned = 0
    for worker in workers:
        try:
            conn.execute("DELETE FROM workers WHERE id = ?", (worker.db_id,))
            cleaned += 1
            click.echo(f"  Cleaned up worker #{worker.db_id}")

        except Exception as e:
            click.echo(f"  Error cleaning worker #{worker.db_id}: {e}", err=True)

    conn.commit()
    job_queue.close()

    click.echo(f"Cleaned up {cleaned} worker(s)")
    return 0
