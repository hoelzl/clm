"""``clm release`` — per-topic solution release to cohort repositories (#208).

Thin CLI over :mod:`clm.release`. Until ``<release-channels>`` spec parsing
lands (step 3), a channel is identified by explicit paths: its ``--ledger``
(release intent, in the course source repo), the ``--source`` frozen-build
output root (which holds the ``.clm-manifest.json`` provenance index), and the
``--dest`` cohort repository. Step 3 will let a single ``--channel NAME``
resolve these from the spec and add ``--push``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import click

from clm.core.course_spec import CourseSpec
from clm.core.provenance_manifest import MANIFEST_FILENAME, load_manifest
from clm.release.frozen_manifest import FROZEN_FILENAME, FrozenManifest
from clm.release.ledger import Ledger, partition_known
from clm.release.sync import apply_sync, plan_sync

logger = logging.getLogger(__name__)

_SPEC_ARG = click.argument(
    "spec_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
_LEDGER_OPT = click.option(
    "--ledger",
    "ledger_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the channel's release ledger (created on first add).",
)


@click.group("release")
def release_group() -> None:
    """Release solutions to student cohorts, one topic at a time (issue #208)."""


def _spec_topic_ids(spec_file: Path) -> list[str]:
    return [topic.id for topic in CourseSpec.from_file(spec_file).topics]


@release_group.command("add")
@_SPEC_ARG
@click.argument("topic_ids", nargs=-1, required=True)
@_LEDGER_OPT
def add_cmd(spec_file: Path, topic_ids: tuple[str, ...], ledger_path: Path) -> None:
    """Append TOPIC_IDS to a channel ledger (validated against the spec)."""
    known, unknown = partition_known(topic_ids, _spec_topic_ids(spec_file))
    if unknown:
        raise click.ClickException(
            "Unknown topic id(s) not declared in the spec: " + ", ".join(unknown)
        )
    ledger = Ledger.load(ledger_path)
    added = ledger.add(known)
    ledger.save(ledger_path)

    if added:
        click.echo(f"Released {len(added)} topic(s): {', '.join(added)}")
    already = [tid for tid in known if tid not in added]
    if already:
        click.echo(f"Already released ({len(already)}): {', '.join(already)}")


@release_group.command("status")
@_SPEC_ARG
@_LEDGER_OPT
@click.option(
    "--dest",
    "dest_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Channel destination repo; when given, also reports frozen state.",
)
@click.option("--channel", default="", help="Channel name (for the frozen manifest).")
def status_cmd(spec_file: Path, ledger_path: Path, dest_path: Path | None, channel: str) -> None:
    """Show released vs pending topics (and frozen state with --dest)."""
    all_ids = _spec_topic_ids(spec_file)
    ledger = Ledger.load(ledger_path)
    released = ledger.released_set
    pending = [tid for tid in all_ids if tid not in released]

    click.echo(f"Topics: {len(all_ids)} total, {len(released)} released, {len(pending)} pending")
    if ledger.released:
        click.echo("  released: " + ", ".join(ledger.released))
    if pending:
        click.echo("  pending:  " + ", ".join(pending))

    if dest_path is not None:
        frozen = FrozenManifest.load(dest_path / FROZEN_FILENAME, channel=channel or "?")
        awaiting = [tid for tid in ledger.released if not frozen.is_frozen(tid)]
        click.echo(
            f"Destination: {len(frozen.frozen)} frozen, {len(awaiting)} released "
            f"awaiting sync, skeleton {'frozen' if frozen.skeleton_frozen else 'not copied'}"
        )
        if awaiting:
            click.echo("  awaiting sync: " + ", ".join(awaiting))


@release_group.command("sync")
@_LEDGER_OPT
@click.option(
    "--source",
    "source_path",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Built frozen-source output root (contains .clm-manifest.json).",
)
@click.option(
    "--dest",
    "dest_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Channel destination repository (created if absent).",
)
@click.option("--channel", default="", help="Channel name (recorded in the frozen manifest).")
@click.option(
    "--refreeze",
    "refreeze_ids",
    multiple=True,
    help="Re-copy and re-freeze these already-frozen topics (e.g. a bug fix).",
)
@click.option("--refreeze-all", is_flag=True, help="Re-copy and re-freeze every released topic.")
@click.option("--dry-run", is_flag=True, help="Print the plan; copy nothing.")
def sync_cmd(
    ledger_path: Path,
    source_path: Path,
    dest_path: Path,
    channel: str,
    refreeze_ids: tuple[str, ...],
    refreeze_all: bool,
    dry_run: bool,
) -> None:
    """Promote released-but-not-frozen topics from SOURCE into the channel DEST."""
    manifest_path = source_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise click.ClickException(
            f"No provenance manifest at {manifest_path}. Build the source with "
            f"`clm build --provenance-manifest` first."
        )
    manifest = load_manifest(manifest_path)
    ledger = Ledger.load(ledger_path)
    frozen_path = dest_path / FROZEN_FILENAME
    frozen = FrozenManifest.load(frozen_path, channel=channel)

    refreeze = set(ledger.released) if refreeze_all else set(refreeze_ids)
    plan = plan_sync(
        manifest=manifest,
        ledger_released=ledger.released,
        frozen=frozen,
        refreeze=refreeze,
    )

    click.echo(
        f"Channel '{channel or frozen.channel or '?'}': "
        f"skeleton {'copy' if plan.copy_skeleton else 'frozen'} "
        f"({plan.skeleton_file_count} files)"
    )
    for topic_plan in plan.topics:
        click.echo(
            f"  {topic_plan.action:<11} {topic_plan.topic_id} ({topic_plan.file_count} files)"
        )

    if dry_run:
        click.echo("Dry run: nothing copied.")
        return

    result = apply_sync(
        plan=plan,
        manifest=manifest,
        source_root=source_path,
        dest_root=dest_path,
        frozen=frozen,
        copied_at=datetime.now(timezone.utc).isoformat(),
    )
    frozen.save(frozen_path)
    click.echo(
        f"Copied {result.files_copied} file(s): "
        f"{len(result.copied_topics)} newly frozen, "
        f"{len(result.refrozen_topics)} re-frozen, "
        f"{len(result.skipped_topics)} already frozen (skipped)."
    )
