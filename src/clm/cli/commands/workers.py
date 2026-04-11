"""Worker management commands.

This module provides commands for listing and managing CLM workers.
"""

from datetime import datetime, timezone
from pathlib import Path

import click


@click.group(name="workers")
def workers_group():
    """Manage CLM workers."""
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

    \b
    Examples:
        clm workers list
        clm workers list --status=idle
        clm workers list --format=json
        clm workers list --status=busy --status=hung
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

    \b
    Examples:
        clm workers cleanup
        clm workers cleanup --force
        clm workers cleanup --all --force
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


@workers_group.command(name="reap")
@click.option(
    "--jobs-db-path",
    type=click.Path(),
    default="clm_jobs.db",
    help="Path to the job queue database",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be reaped without killing anything",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--all",
    "reap_all",
    is_flag=True,
    help=(
        "Also reap worker processes whose environment is unreadable or whose "
        "DB_PATH does not match --jobs-db-path. Default is to leave those alone "
        "so you cannot accidentally kill workers from another worktree."
    ),
)
@click.pass_context
def workers_reap(ctx, jobs_db_path, dry_run, force, reap_all):
    """Reap orphaned workers: DB rows + OS process trees.

    Unlike ``clm workers cleanup`` (which only deletes DB rows), ``reap``
    actually kills surviving worker processes and their descendants
    (Jupyter kernels, drawio/plantuml subprocesses). It is the
    self-service recovery command for when a ``clm build`` crashed hard
    or was taskkill'd and left processes running.

    The command:

    \b
    1. Marks any in-flight job rows as failed (same as pool_stopped
       would have done if the worker had died cleanly).
    2. Scans for surviving ``python -m clm.workers.*`` processes via
       psutil.
    3. Matches each surviving process against the DB at --jobs-db-path
       (via the DB_PATH env var the worker was launched with).
    4. Terminate-then-kill each matched process *tree* (including its
       descendants — kernels and converters).
    5. Deletes the now-stale worker rows from the DB (same shape as
       ``cleanup``).

    Processes whose environment cannot be read (usually cross-session
    on Windows) or whose DB_PATH does not match are *listed* but not
    reaped by default. Pass ``--all`` to reap them too.

    \b
    Examples:
        clm workers reap --dry-run
        clm workers reap --force
        clm workers reap --all --force
    """
    from clm.infrastructure.database.job_queue import JobQueue
    from clm.infrastructure.workers.discovery import WorkerDiscovery
    from clm.infrastructure.workers.process_reaper import (
        DiscoveredWorkerProcess,
        reap_process_tree,
        scan_worker_processes,
    )

    jobs_db_path = Path(jobs_db_path)

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        ctx.exit(1)

    target_db = jobs_db_path.resolve()

    # Step 1 — orphan-row reap (reuses Fix 3's atomic helper). We do this
    # before killing processes so that the "worker died mid-job" rows
    # exist in the DB even if the subsequent process kill takes a while
    # or partially fails. Wrapped in try/except so a DB hiccup does not
    # prevent the process-kill step from running.
    job_queue = JobQueue(jobs_db_path)
    try:
        if dry_run:
            click.echo(
                "[dry-run] Would reap in-flight job rows via JobQueue.mark_orphaned_jobs_failed"
            )
            orphan_rows: list[dict] = []
        else:
            orphan_rows = job_queue.mark_orphaned_jobs_failed()
            if orphan_rows:
                click.echo(f"Marked {len(orphan_rows)} orphan job row(s) as failed:")
                for row in orphan_rows:
                    click.echo(f"  job #{row['id']}: {row['input_file']} (was {row['status']})")
            else:
                click.echo("No orphan job rows found")
    except Exception as exc:
        click.echo(f"Warning: orphan-row reap failed: {exc}", err=True)
        orphan_rows = []

    # Step 2 — scan for surviving worker processes.
    try:
        survivors = scan_worker_processes()
    except Exception as exc:
        click.echo(f"Error: process scan failed: {exc}", err=True)
        job_queue.close()
        return 1

    # Partition into "matched this DB" vs "other / unknown".
    matched: list[DiscoveredWorkerProcess] = []
    unmatched: list[DiscoveredWorkerProcess] = []
    for proc in survivors:
        if proc.db_path is not None:
            try:
                if proc.db_path.resolve() == target_db:
                    matched.append(proc)
                    continue
            except OSError:
                # Non-existent path in env var -> treat as unmatched.
                pass
        unmatched.append(proc)

    if not matched and not unmatched:
        click.echo("No surviving worker processes found")
    else:
        click.echo(f"Found {len(survivors)} surviving worker process(es):")
        for proc in matched:
            click.echo(
                f"  [match]   pid={proc.pid} type={proc.worker_type} "
                f"worker_id={proc.worker_id or '?'} cwd={proc.cwd or '?'}"
            )
        for proc in unmatched:
            reason = "different DB" if proc.db_path is not None else "env unreadable"
            click.echo(
                f"  [skip]    pid={proc.pid} type={proc.worker_type} "
                f"db_path={proc.db_path or '?'} ({reason})"
            )

    # Figure out what we are actually going to kill.
    to_kill: list[DiscoveredWorkerProcess] = list(matched)
    if reap_all:
        to_kill.extend(unmatched)

    if not to_kill:
        click.echo("Nothing to kill")
    elif dry_run:
        click.echo(f"[dry-run] Would kill {len(to_kill)} process tree(s):")
        for proc in to_kill:
            click.echo(f"  pid={proc.pid} {' '.join(proc.cmdline)}")
    else:
        if not force:
            if not click.confirm(f"Kill {len(to_kill)} process tree(s)?"):
                click.echo("Cancelled")
                job_queue.close()
                return 0

        killed = 0
        for proc in to_kill:
            try:
                n = reap_process_tree(proc.pid, log_prefix=f"worker-{proc.pid}")
                click.echo(f"  Killed pid={proc.pid} (reaped {n} process(es) in tree)")
                killed += n
            except Exception as exc:
                click.echo(f"  Error killing pid={proc.pid}: {exc}", err=True)
        click.echo(f"Reaped {killed} process(es) across {len(to_kill)} worker tree(s)")

    # Step 3 — clean up stale worker rows in the DB. We run the same
    # discovery-based sweep as ``cleanup``: dead/hung workers plus
    # idle/busy workers whose heartbeat is stale. This is what makes
    # ``reap`` a superset of ``cleanup``.
    if dry_run:
        click.echo("[dry-run] Would clean up stale worker rows via WorkerDiscovery")
    else:
        discovery = WorkerDiscovery(jobs_db_path)
        try:
            stale = discovery.discover_workers(status_filter=["dead", "hung"])
            active = discovery.discover_workers(status_filter=["idle", "busy"])
            stale.extend(
                w
                for w in active
                if (datetime.now(timezone.utc) - w.last_heartbeat).total_seconds() > 60
            )
            if stale:
                conn = job_queue._get_conn()
                for worker in stale:
                    try:
                        conn.execute("DELETE FROM workers WHERE id = ?", (worker.db_id,))
                    except Exception as exc:
                        click.echo(f"  Error clearing worker row #{worker.db_id}: {exc}", err=True)
                conn.commit()
                click.echo(f"Cleared {len(stale)} stale worker row(s)")
            else:
                click.echo("No stale worker rows to clear")
        finally:
            discovery.close()

    job_queue.close()
    return 0
