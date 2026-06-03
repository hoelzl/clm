"""``clm release`` — per-topic solution release to cohort repositories (#208).

Thin CLI over :mod:`clm.release`. A channel can be addressed two ways:

* ``--channel NAME`` resolves the ledger, the frozen ``--source`` build root,
  and the ``--dest`` cohort repo from the spec's ``<release-channels>`` block;
* or those three paths can be passed explicitly (and override resolution).

``clm release sync --push`` (delegating to ``clm git``) is added in a later
increment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import click
from attrs import frozen

from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec
from clm.core.provenance_manifest import MANIFEST_FILENAME, load_manifest
from clm.release.frozen_manifest import FROZEN_FILENAME, FrozenManifest
from clm.release.ledger import Ledger, partition_known
from clm.release.sync import apply_sync, plan_sync

logger = logging.getLogger(__name__)

_SPEC_ARG = click.argument(
    "spec_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
_CHANNEL_OPT = click.option(
    "--channel",
    default="",
    help="Channel name; resolves --ledger/--source/--dest from the spec's "
    "<release-channels>. Explicit paths override resolution.",
)
_LEDGER_OPT = click.option(
    "--ledger",
    "ledger_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to the channel's release ledger (created on first add).",
)


@frozen
class _ResolvedChannel:
    name: str
    ledger: Path
    source: Path | None
    dest: Path


@click.group("release")
def release_group() -> None:
    """Release solutions to student cohorts, one topic at a time (issue #208)."""


def _abs_under(course_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else course_root / path


def _resolve_channel(spec_file: Path, channel_name: str) -> _ResolvedChannel:
    spec = CourseSpec.from_file(spec_file)
    channels = spec.release_channels
    if channels is None:
        raise click.ClickException(
            f"{spec_file} has no <release-channels> block; pass explicit "
            f"--ledger/--source/--dest instead of --channel."
        )
    channel = channels.channel(channel_name)
    if channel is None:
        available = ", ".join(c.name for c in channels.channels) or "(none defined)"
        raise click.ClickException(
            f"Unknown channel {channel_name!r}. Defined channels: {available}."
        )
    course_root, _ = resolve_course_paths(spec_file)
    source_target = next((t for t in spec.output_targets if t.name == channels.source_target), None)
    source = _abs_under(course_root, source_target.path) if source_target else None
    return _ResolvedChannel(
        name=channel_name,
        ledger=_abs_under(course_root, channel.ledger),
        source=source,
        dest=_abs_under(course_root, channel.path),
    )


def _spec_topic_ids(spec_file: Path) -> list[str]:
    return [topic.id for topic in CourseSpec.from_file(spec_file).topics]


@release_group.command("add")
@_SPEC_ARG
@click.argument("topic_ids", nargs=-1, required=True)
@_CHANNEL_OPT
@_LEDGER_OPT
def add_cmd(
    spec_file: Path, topic_ids: tuple[str, ...], channel: str, ledger_path: Path | None
) -> None:
    """Append TOPIC_IDS to a channel ledger (validated against the spec)."""
    if ledger_path is None and channel:
        ledger_path = _resolve_channel(spec_file, channel).ledger
    if ledger_path is None:
        raise click.ClickException("Pass --ledger PATH or --channel NAME.")

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
@_CHANNEL_OPT
@_LEDGER_OPT
@click.option(
    "--dest",
    "dest_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Channel destination repo; when given, also reports frozen state.",
)
def status_cmd(
    spec_file: Path, channel: str, ledger_path: Path | None, dest_path: Path | None
) -> None:
    """Show released vs pending topics (and frozen state with --dest/--channel)."""
    if channel:
        resolved = _resolve_channel(spec_file, channel)
        ledger_path = ledger_path or resolved.ledger
        dest_path = dest_path or resolved.dest
    if ledger_path is None:
        raise click.ClickException("Pass --ledger PATH or --channel NAME.")

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
@click.argument(
    "spec_file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@_CHANNEL_OPT
@_LEDGER_OPT
@click.option(
    "--source",
    "source_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Built frozen-source output root (contains .clm-manifest.json).",
)
@click.option(
    "--dest",
    "dest_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Channel destination repository (created if absent).",
)
@click.option(
    "--refreeze",
    "refreeze_ids",
    multiple=True,
    help="Re-copy and re-freeze these already-frozen topics (e.g. a bug fix).",
)
@click.option("--refreeze-all", is_flag=True, help="Re-copy and re-freeze every released topic.")
@click.option("--dry-run", is_flag=True, help="Print the plan; copy nothing.")
def sync_cmd(
    spec_file: Path | None,
    channel: str,
    ledger_path: Path | None,
    source_path: Path | None,
    dest_path: Path | None,
    refreeze_ids: tuple[str, ...],
    refreeze_all: bool,
    dry_run: bool,
) -> None:
    """Promote released-but-not-frozen topics into a cohort.

    Address the channel either with ``--channel NAME`` (resolved from
    ``SPEC_FILE``'s <release-channels>) or with explicit
    ``--ledger``/``--source``/``--dest`` paths.
    """
    if channel:
        if spec_file is None:
            raise click.ClickException("--channel requires the SPEC_FILE argument.")
        resolved = _resolve_channel(spec_file, channel)
        ledger_path = ledger_path or resolved.ledger
        source_path = source_path or resolved.source
        dest_path = dest_path or resolved.dest

    if ledger_path is None or source_path is None or dest_path is None:
        raise click.ClickException(
            "Specify --channel NAME, or all of --ledger, --source and --dest."
        )
    if not source_path.is_dir():
        raise click.ClickException(f"Source output root not found: {source_path}")

    manifest_path = source_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise click.ClickException(
            f"No provenance manifest at {manifest_path}. Build the source with "
            f"`clm build --provenance-manifest` first."
        )
    manifest = load_manifest(manifest_path)
    ledger = Ledger.load(ledger_path)
    channel_name = channel or dest_path.name
    frozen_path = dest_path / FROZEN_FILENAME
    frozen = FrozenManifest.load(frozen_path, channel=channel_name)

    refreeze = set(ledger.released) if refreeze_all else set(refreeze_ids)
    plan = plan_sync(
        manifest=manifest,
        ledger_released=ledger.released,
        frozen=frozen,
        refreeze=refreeze,
    )

    click.echo(
        f"Channel '{channel_name or '?'}': "
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
