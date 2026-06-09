"""``clm release`` — per-topic solution release to cohort repositories (#208).

Thin CLI over :mod:`clm.release`. A channel can be addressed two ways:

* ``--channel NAME`` resolves the ledger, the frozen ``--source`` build root,
  and the ``--dest`` cohort repo from the spec's ``<release-channels>`` block;
* or those three paths can be passed explicitly (and override resolution).

``clm release sync --push`` commits and pushes the cohort repo after promoting,
delegating to ``clm git``'s shared commit/push helper (issue #208).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import click
from attrs import frozen

from clm.cli.commands.git_ops import (
    OutputRepo,
    commit_and_push_repo,
    find_release_channel_repos,
)
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    CourseSpec,
    CourseSpecError,
    SectionSpec,
    release_channel_ref,
)
from clm.core.provenance_manifest import MANIFEST_FILENAME, load_manifest
from clm.release.frozen_manifest import FROZEN_FILENAME, FrozenManifest
from clm.release.ledger import Ledger, partition_known
from clm.release.sync import SyncResult, apply_sync, plan_sync

logger = logging.getLogger(__name__)

_SPEC_ARG = click.argument(
    "spec_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
_CHANNEL_OPT = click.option(
    "--channel",
    default="",
    help="Channel address; resolves --ledger/--source/--dest from the spec's "
    "<release-channels>. Use STREAM/CHANNEL (e.g. materials/2026-04) when "
    "several streams are declared; a bare name works when unique. Explicit "
    "paths override resolution.",
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
    """Resolve a channel address (``stream/channel`` or unique bare name, #291)."""
    spec = CourseSpec.from_file(spec_file)
    if not spec.release_channel_blocks:
        raise click.ClickException(
            f"{spec_file} has no <release-channels> block; pass explicit "
            f"--ledger/--source/--dest instead of --channel."
        )
    try:
        block, channel = spec.resolve_release_channel(channel_name)
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None
    course_root, _ = resolve_course_paths(spec_file)
    source_target = next((t for t in spec.output_targets if t.name == block.source_target), None)
    source = _abs_under(course_root, source_target.path) if source_target else None
    return _ResolvedChannel(
        name=release_channel_ref(block, channel),
        ledger=_abs_under(course_root, channel.ledger),
        source=source,
        dest=_abs_under(course_root, channel.path),
    )


def _spec_topic_ids(spec_file: Path) -> list[str]:
    return [topic.id for topic in CourseSpec.from_file(spec_file).topics]


def _default_push_message(channel_name: str, result: SyncResult) -> str:
    """A one-line commit message summarizing what a sync froze into the cohort."""
    parts: list[str] = []
    if result.copied_topics:
        parts.append(f"{len(result.copied_topics)} new")
    if result.refrozen_topics:
        parts.append(f"{len(result.refrozen_topics)} refrozen")
    # The first sync of a cohort ships the skeleton (dir-group/shared files) even
    # with no topics released yet — don't call that "no topic changes".
    if not parts and result.skeleton_copied:
        parts.append("skeleton")
    detail = ", ".join(parts) if parts else "no topic changes"
    return f"Release to {channel_name}: {detail}"


def _push_channel_repo(
    *,
    spec_file: Path | None,
    channel: str,
    dest_path: Path,
    channel_name: str,
    result: SyncResult,
    message: str | None,
) -> None:
    """Commit and push the cohort repo after a successful ``clm release sync``.

    Delegates to ``clm git``'s shared :func:`commit_and_push_repo` so promotion
    and distribution use one git implementation (and one manifest-exclusion
    chokepoint). The repo is the channel ``dest`` working tree, where the
    promoted files already live; when the channel resolves from a spec we reuse
    its discovered :class:`OutputRepo` (carrying the derived remote URL),
    otherwise we operate on ``dest_path`` directly. Raises a ``ClickException``
    if the repo was never initialized, and exits non-zero if the push fails.
    """
    repo: OutputRepo | None = None
    if channel and spec_file is not None:
        repos = find_release_channel_repos(spec_file, channel)
        repo = next((r for r in repos if r.path.resolve() == dest_path.resolve()), None)
    if repo is None:
        repo = OutputRepo(
            path=dest_path,
            target_name=channel_name,
            language="",
            source="channel",
        )

    click.echo()
    click.echo(f"[{repo.display_name}] {repo.path}")
    if not repo.has_git:
        if channel and spec_file is not None:
            # Channel mode: `clm git init --channel` resolves this exact repo.
            recovery = (
                f"Initialize the cohort repo first:\n"
                f"    clm git init {spec_file} --channel {channel_name}\n"
                f"then re-run with --push."
            )
        else:
            # Explicit --dest mode has no <release-channels> entry, so
            # `clm git init --channel` cannot resolve it — init a plain repo.
            recovery = (
                f"Initialize a git repository there first (run `git init` in "
                f"{repo.path} and add an 'origin' remote), then re-run with --push."
            )
        raise click.ClickException(f"No git repository at {repo.path}. {recovery}")

    remote_ahead_hint: list[str] | None = None
    if spec_file is not None and channel:
        remote_ahead_hint = [
            f"clm git reset {spec_file} --channel {channel_name}",
            f"clm release sync {spec_file} --channel {channel_name} --push",
        ]

    ok = commit_and_push_repo(
        repo,
        message or _default_push_message(channel_name, result),
        remote_ahead_hint=remote_ahead_hint,
    )
    if not ok:
        raise SystemExit(1)


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


def _section_label(section: SectionSpec) -> str:
    """Display label for a section: its id, else its English/German name."""
    if section.id:
        return section.id
    return section.name.en or section.name.de


@release_group.command("week")
@_SPEC_ARG
@click.argument("selectors", nargs=-1, required=True)
@_CHANNEL_OPT
@_LEDGER_OPT
def week_cmd(
    spec_file: Path, selectors: tuple[str, ...], channel: str, ledger_path: Path | None
) -> None:
    """Release every topic in the selected section(s) — a "week" — to a channel.

    SELECTORS use the same grammar as ``build --only-sections``: ``id:`` /
    ``idx:`` / ``name:`` prefixes, or a bare 1-based index or
    case-insensitive name substring. A "week" is a course section; this
    resolves the matching section(s), expands them to their topic ids, and
    appends those to the channel ledger — a section-scoped ``release add``.

    Section indices are **disabled-inclusive** (an ``enabled="false"`` section
    still consumes its index), so the spec is parsed keeping disabled sections
    and any selected-but-disabled section is reported and skipped rather than
    silently shifting which topics get released.
    """
    if ledger_path is None and channel:
        ledger_path = _resolve_channel(spec_file, channel).ledger
    if ledger_path is None:
        raise click.ClickException("Pass --ledger PATH or --channel NAME.")

    # Parse keeping disabled sections so the selector indices line up with the
    # authoring order (see CourseSpec.resolve_section_selectors). Disabled
    # sections never land in resolved_indices — they are collected separately
    # into skipped_disabled — so expanding resolved_indices to topics yields
    # only enabled sections' topics.
    try:
        spec = CourseSpec.from_file(spec_file, keep_disabled=True)
        selection = spec.resolve_section_selectors(list(selectors))
    except CourseSpecError as e:
        raise click.ClickException(str(e)) from None

    for label in selection.skipped_disabled:
        click.echo(f"Warning: skipping disabled section '{label}' (enabled=\"false\").")

    topic_ids: list[str] = []
    seen: set[str] = set()
    selected_labels: list[str] = []
    for idx in selection.resolved_indices:
        section = spec.sections[idx]
        selected_labels.append(_section_label(section))
        for topic in section.topics:
            if topic.id not in seen:
                seen.add(topic.id)
                topic_ids.append(topic.id)

    if not topic_ids:
        raise click.ClickException("The selected section(s) declare no topics; nothing to release.")

    click.echo(
        f"Selected {len(selected_labels)} of {len(spec.sections)} section(s): "
        + ", ".join(selected_labels)
    )

    ledger = Ledger.load(ledger_path)
    added = ledger.add(topic_ids)
    ledger.save(ledger_path)

    if added:
        click.echo(f"Released {len(added)} topic(s): {', '.join(added)}")
    already = [tid for tid in topic_ids if tid not in added]
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
@click.option(
    "--push",
    is_flag=True,
    help="After promoting, commit and push the cohort repo (via clm git's "
    "commit/push). The repo must already exist — run `clm git init --channel` once.",
)
@click.option(
    "-m",
    "--message",
    "commit_message",
    default=None,
    help="Commit message used by --push (default: a one-line summary of the sync).",
)
def sync_cmd(
    spec_file: Path | None,
    channel: str,
    ledger_path: Path | None,
    source_path: Path | None,
    dest_path: Path | None,
    refreeze_ids: tuple[str, ...],
    refreeze_all: bool,
    dry_run: bool,
    push: bool,
    commit_message: str | None,
) -> None:
    """Promote released-but-not-frozen topics into a cohort.

    Address the channel either with ``--channel NAME`` (resolved from
    ``SPEC_FILE``'s <release-channels>) or with explicit
    ``--ledger``/``--source``/``--dest`` paths. With ``--push`` the cohort repo
    is committed and pushed afterward, reusing ``clm git``'s machinery.
    """
    if channel:
        if spec_file is None:
            raise click.ClickException("--channel requires the SPEC_FILE argument.")
        resolved = _resolve_channel(spec_file, channel)
        # Use the canonical stream/channel address everywhere downstream
        # (messages, the frozen manifest's channel field, push hints).
        channel = resolved.name
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
        if push:
            click.echo("Dry run: --push skipped (nothing was promoted).")
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

    if push:
        _push_channel_repo(
            spec_file=spec_file,
            channel=channel,
            dest_path=dest_path,
            channel_name=channel_name,
            result=result,
            message=commit_message,
        )
