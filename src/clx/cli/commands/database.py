"""Database management commands.

This module provides commands for managing CLX databases.
"""

import click


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
