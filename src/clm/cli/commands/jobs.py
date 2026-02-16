"""Job management commands.

This module provides commands for listing and cancelling CLM jobs.
"""

import click


@click.group(name="jobs")
def jobs_group():
    """Manage CLM jobs."""
    pass


@jobs_group.command(name="cancel")
@click.option(
    "--older-than",
    type=int,
    default=None,
    help="Only cancel pending jobs older than N minutes",
)
@click.option(
    "--type",
    "job_type",
    type=click.Choice(["notebook", "plantuml", "drawio"], case_sensitive=False),
    default=None,
    help="Only cancel jobs of this type",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be cancelled without actually cancelling",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def jobs_cancel(ctx, older_than, job_type, dry_run, force):
    """Cancel pending jobs.

    By default, cancels all pending jobs. Use --older-than to only cancel
    jobs that have been pending for at least N minutes, and --type to
    restrict to a specific job type.

    Examples:
        clm jobs cancel                       # Cancel all pending jobs
        clm jobs cancel --older-than=5        # Cancel jobs pending > 5 minutes
        clm jobs cancel --type=notebook       # Cancel only notebook jobs
        clm jobs cancel --dry-run             # Preview without cancelling
        clm jobs cancel --older-than=10 --force
    """
    from clm.infrastructure.database.job_queue import JobQueue

    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        ctx.exit(1)

    min_age_seconds = older_than * 60 if older_than is not None else None

    with JobQueue(jobs_db_path) as jq:
        # Preview: count matching jobs
        pending_jobs = jq.get_jobs_by_status("pending", limit=10000)

        # Apply filters to get the count
        from datetime import datetime

        matching = []
        for job in pending_jobs:
            if job_type and job.job_type != job_type:
                continue
            if min_age_seconds is not None:
                age = (datetime.now() - job.created_at).total_seconds()
                if age < min_age_seconds:
                    continue
            matching.append(job)

        if not matching:
            click.echo("No matching pending jobs found.")
            return

        # Show summary
        description = "pending"
        if job_type:
            description = f"pending {job_type}"
        if older_than is not None:
            description += f" (older than {older_than} minute{'s' if older_than != 1 else ''})"

        click.echo(f"Found {len(matching)} {description} job(s):")
        for job in matching[:10]:
            age = (datetime.now() - job.created_at).total_seconds()
            if age >= 3600:
                age_str = f"{age / 3600:.1f}h"
            elif age >= 60:
                age_str = f"{age / 60:.0f}m"
            else:
                age_str = f"{age:.0f}s"
            click.echo(f"  #{job.id} [{job.job_type}] {job.input_file} (age: {age_str})")
        if len(matching) > 10:
            click.echo(f"  ... and {len(matching) - 10} more")

        if dry_run:
            click.echo("\nDRY RUN - no changes made")
            return

        if not force:
            if not click.confirm(f"\nCancel {len(matching)} job(s)?"):
                click.echo("Cancelled.")
                return

        cancelled = jq.cancel_pending_jobs(
            min_age_seconds=min_age_seconds,
            job_type=job_type,
        )
        click.echo(f"\nCancelled {len(cancelled)} job(s).")


@jobs_group.command(name="list")
@click.option(
    "--status",
    type=click.Choice(
        ["pending", "processing", "completed", "failed", "cancelled"],
        case_sensitive=False,
    ),
    default="pending",
    help="Filter by job status (default: pending)",
)
@click.option(
    "--limit",
    type=int,
    default=50,
    help="Maximum number of jobs to show (default: 50)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def jobs_list(ctx, status, limit, output_format):
    """List jobs in the queue.

    Shows jobs filtered by status. Defaults to showing pending jobs.

    Examples:
        clm jobs list                        # Show pending jobs
        clm jobs list --status=failed        # Show failed jobs
        clm jobs list --status=processing    # Show processing jobs
        clm jobs list --limit=100            # Show more results
        clm jobs list --format=json          # JSON output
    """
    from clm.infrastructure.database.job_queue import JobQueue

    jobs_db_path = ctx.obj["JOBS_DB_PATH"]

    if not jobs_db_path.exists():
        click.echo(f"Error: Job queue database not found: {jobs_db_path}", err=True)
        ctx.exit(1)

    with JobQueue(jobs_db_path) as jq:
        jobs = jq.get_jobs_by_status(status, limit=limit)

    if not jobs:
        click.echo(f"No {status} jobs found.")
        return

    if output_format == "json":
        import json

        data = [job.to_dict() for job in jobs]
        click.echo(json.dumps(data, indent=2))
    else:
        from datetime import datetime

        click.echo(f"{len(jobs)} {status} job(s):\n")
        for job in jobs:
            age = (datetime.now() - job.created_at).total_seconds()
            if age >= 3600:
                age_str = f"{age / 3600:.1f}h ago"
            elif age >= 60:
                age_str = f"{age / 60:.0f}m ago"
            else:
                age_str = f"{age:.0f}s ago"

            line = f"  #{job.id}  [{job.job_type:>9}]  {age_str:>8}  {job.input_file}"
            if job.error:
                line += f"  error: {job.error[:60]}"
            click.echo(line)
