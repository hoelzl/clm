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

from clm.cli.commands.git import (
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
from clm.core.provenance_manifest import (
    MANIFEST_FILENAME,
    load_manifest,
    restrict_manifest_to_language,
)
from clm.release.frozen_manifest import FROZEN_FILENAME, load_frozen_manifest
from clm.release.ledger import Ledger, partition_known
from clm.release.sync import (
    REFRESH,
    EvergreenScan,
    SyncResult,
    apply_sync,
    plan_sync,
    scan_evergreen,
    scan_skeleton,
    topic_path_overlap,
)

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
    # Channel language scope (issue #293). Empty = the channel receives every
    # built language root.
    lang: str = ""
    # Evergreen glob patterns (block-level inherited + channel additions).
    evergreen: tuple[str, ...] = ()
    # Release stream name (empty for the single unnamed block). Selects the
    # per-stream frozen-manifest filename (issue #325).
    stream: str = ""


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
        lang=channel.lang,
        evergreen=channel.evergreen,
        stream=block.name,
    )


def _check_shared_destination_overlap(
    spec_file: Path, channel_ref: str, dest_path: Path, manifest: dict
) -> None:
    """Sync preflight for shared destinations (issue #325).

    When another stream's channel releases into the same destination, its
    source manifest and ours must claim **disjoint** topic files — skeleton
    overlap is fine (presence-as-frozen keeps the first copy), but a
    topic-owned path in both manifests means the streams' output targets
    collide and a sync would clobber the other stream's frozen content. A
    sharer whose source target has no manifest yet (not built) is skipped
    with a note. *manifest* is this sync's already language-restricted
    manifest, so paths compare in destination-relative form.
    """
    spec = CourseSpec.from_file(spec_file)
    block, _ = spec.resolve_release_channel(channel_ref)
    course_root, _ = resolve_course_paths(spec_file)
    for other_block, other_channel in spec.iter_release_channels():
        if other_block.name == block.name:
            continue
        if _abs_under(course_root, other_channel.path).resolve() != dest_path.resolve():
            continue
        other_ref = release_channel_ref(other_block, other_channel)
        target = next((t for t in spec.output_targets if t.name == other_block.source_target), None)
        if target is None:
            continue
        other_manifest_path = _abs_under(course_root, target.path) / MANIFEST_FILENAME
        if not other_manifest_path.is_file():
            click.echo(
                f"Note: '{other_ref}' shares this destination but its source "
                f"target is not built; the cross-stream overlap check will run "
                f"once it is."
            )
            continue
        other_manifest = load_manifest(other_manifest_path)
        if other_channel.lang:
            lang_dir = str(spec.output_dir_name[other_channel.lang])
            other_manifest = restrict_manifest_to_language(
                other_manifest, other_channel.lang, lang_dir
            )
        overlap = topic_path_overlap(manifest, other_manifest)
        if overlap:
            examples = ", ".join(overlap[:5]) + (", …" if len(overlap) > 5 else "")
            raise click.ClickException(
                f"Refusing to sync: {len(overlap)} topic-owned file(s) are claimed "
                f"by both '{channel_ref}' and '{other_ref}', which share the "
                f"destination {dest_path} — promoting would clobber the other "
                f"stream's released content: {examples}. Streams sharing a "
                f"destination must build disjoint outputs (e.g. code-along/partial "
                f"vs completed kinds)."
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
    if result.refreshed_files:
        parts.append(f"{len(result.refreshed_files)} evergreen")
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


@release_group.command("provision")
@_SPEC_ARG
@click.option(
    "--channel",
    default="",
    help="Provision a single channel (STREAM/CHANNEL or unique bare name). "
    "Default: every channel that declares <share-with>.",
)
@click.option("--dry-run", is_flag=True, help="Show the shares that would be applied.")
def provision_cmd(spec_file: Path, channel: str, dry_run: bool) -> None:
    """Share channel repos into their GitLab access groups (issue #294).

    For every selected channel with ``<share-with>`` declarations, performs
    the GitLab group-share via the REST API (idempotent — an existing share is
    reported, not an error). The repo itself must already exist on the remote
    (push it first via ``clm git init/sync --channel``); provisioning only
    grants group access, replacing the manual per-cohort UI step.

    Requires a GitLab token with ``api`` scope in ``CLM_GITLAB_TOKEN`` (or
    ``GITLAB_TOKEN``). Channels without a parseable GitLab remote URL are
    skipped with a note, so the command is a safe no-op for non-GitLab hosts.
    """
    from clm.infrastructure.gitlab_api import (
        GitLabApiError,
        gitlab_token,
        parse_gitlab_remote,
        share_project_with_group,
    )

    spec = CourseSpec.from_file(spec_file)
    if not spec.release_channel_blocks:
        raise click.ClickException(f"{spec_file} has no <release-channels> block.")

    repos = {r.target_name: r for r in find_release_channel_repos(spec_file, channel or None)}
    # Channels sharing one destination share one repository (issue #325):
    # route every share through the first channel's entry (spec order) so all
    # of them target the project that repo actually is.
    canonical_by_dest: dict[Path, OutputRepo] = {}
    for channel_repo in find_release_channel_repos(spec_file, None):
        canonical_by_dest.setdefault(channel_repo.path.resolve(), channel_repo)
    if channel:
        try:
            pairs = [spec.resolve_release_channel(channel)]
        except CourseSpecError as e:
            raise click.ClickException(str(e)) from None
    else:
        pairs = list(spec.iter_release_channels())

    work: list[tuple[str, str, str, str, str]] = []  # ref, base, project, group, access
    seen: dict[tuple[str, str, str], tuple[str, str]] = {}
    for block, ch in pairs:
        ref = release_channel_ref(block, ch)
        if not ch.share_with:
            if channel:
                click.echo(f"[{ref}] no <share-with> declared — nothing to provision.")
            continue
        repo = repos.get(ref)
        if repo is not None:
            repo = canonical_by_dest.get(repo.path.resolve(), repo)
        remote = parse_gitlab_remote(repo.remote_url or "") if repo else None
        if remote is None:
            click.echo(
                f"[{ref}] skipped: no GitLab remote URL could be derived "
                f"(configure <github> repository-base / remote-path)."
            )
            continue
        base_url, project_path = remote
        for share in ch.share_with:
            # One share per (project, group) — channels sharing a destination
            # would otherwise apply the same share once per stream.
            key = (base_url, project_path, share.group)
            prior = seen.get(key)
            if prior is not None:
                if prior[1] != share.access:
                    click.echo(
                        f"[{ref}] share with {share.group} ({share.access}) collapsed "
                        f"into [{prior[0]}]'s {prior[1]} share — the channels share "
                        f"one repository."
                    )
                continue
            seen[key] = (ref, share.access)
            work.append((ref, base_url, project_path, share.group, share.access))

    if not work:
        click.echo("Nothing to provision.")
        return

    if dry_run:
        click.echo("[DRY RUN] Would apply the following group shares:")
        for ref, base_url, project_path, group, access in work:
            click.echo(f"  [{ref}] {base_url}/{project_path} -> {group} ({access})")
        return

    token = gitlab_token()
    if token is None:
        raise click.ClickException(
            "No GitLab token configured. Set CLM_GITLAB_TOKEN (or GITLAB_TOKEN) "
            "to a token with 'api' scope, or use --dry-run to preview."
        )

    errors = 0
    for ref, base_url, project_path, group, access in work:
        try:
            status = share_project_with_group(base_url, project_path, group, access, token)
        except GitLabApiError as e:
            click.echo(f"[{ref}] ERROR sharing with {group}: {e}", err=True)
            errors += 1
            continue
        if status == "already-shared":
            click.echo(f"[{ref}] already shared with {group} — unchanged.")
        else:
            click.echo(f"[{ref}] shared {project_path} with {group} ({access}).")

    if errors:
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
    stream = ""
    if channel:
        resolved = _resolve_channel(spec_file, channel)
        channel = resolved.name
        ledger_path = ledger_path or resolved.ledger
        dest_path = dest_path or resolved.dest
        stream = resolved.stream
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
        frozen = load_frozen_manifest(dest_path, stream=stream, channel=channel or "?").manifest
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
@click.option(
    "--language",
    type=click.Choice(["de", "en"]),
    default=None,
    help="Promote only this language's files, re-rooted at the language "
    "directory (issue #293). Overrides the channel's lang attribute; requires "
    "SPEC_FILE. --source must point at the output-target root.",
)
@click.option(
    "--evergreen",
    "evergreen_patterns",
    multiple=True,
    help="Glob pattern (destination-relative POSIX path) of skeleton files "
    "kept evergreen: re-copied whenever the built content differs from the "
    "cohort's copy (e.g. NEWS.md). Repeatable; adds to the channel's "
    "<evergreen> patterns from the spec.",
)
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
    language: str | None,
    evergreen_patterns: tuple[str, ...],
    dry_run: bool,
    push: bool,
    commit_message: str | None,
) -> None:
    """Promote released-but-not-frozen topics into a cohort.

    Address the channel either with ``--channel NAME`` (resolved from
    ``SPEC_FILE``'s <release-channels>) or with explicit
    ``--ledger``/``--source``/``--dest`` paths. With ``--push`` the cohort repo
    is committed and pushed afterward, reusing ``clm git``'s machinery.

    A channel with a ``lang`` attribute — or an explicit ``--language`` —
    promotes only that language's files, re-rooted so the cohort repo's root
    is the language directory (issue #293). Without either, the destination
    receives every built language root.

    Skeleton files matching an evergreen pattern (the channel's ``<evergreen>``
    declarations plus any ``--evergreen`` options) are exempt from the
    skeleton freeze: each sync re-copies a matching file whose built content
    differs from the cohort's copy.
    """
    channel_lang = ""
    channel_evergreen: tuple[str, ...] = ()
    stream = ""
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
        channel_lang = resolved.lang
        channel_evergreen = resolved.evergreen
        stream = resolved.stream

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

    # Language scoping (issue #293): restrict the manifest to one language and
    # re-root both it and the copy source at the language directory.
    effective_lang = language or channel_lang
    if effective_lang:
        if spec_file is None:
            raise click.ClickException(
                "--language requires the SPEC_FILE argument (it resolves the "
                "language directory name from the spec)."
            )
        lang_dir = str(CourseSpec.from_file(spec_file).output_dir_name[effective_lang])
        lang_root = source_path / lang_dir
        if not lang_root.is_dir():
            raise click.ClickException(
                f"Language root not found: {lang_root}. Build the source target "
                f"for language {effective_lang!r} first."
            )
        manifest = restrict_manifest_to_language(manifest, effective_lang, lang_dir)
        if not manifest["files"]:
            raise click.ClickException(
                f"The provenance manifest records no {effective_lang!r} files under "
                f"{lang_dir!r}; nothing could ever be promoted. Was the source "
                f"built without that language?"
            )
        source_path = lang_root

    # Shared-destination preflight (issue #325): when another stream releases
    # into this destination, their built topic outputs must be disjoint.
    if channel and spec_file is not None:
        _check_shared_destination_overlap(spec_file, channel, dest_path, manifest)

    ledger = Ledger.load(ledger_path)
    channel_name = channel or dest_path.name
    loaded = load_frozen_manifest(dest_path, stream=stream, channel=channel_name)
    frozen = loaded.manifest
    if loaded.ignored_legacy_channel is not None:
        click.echo(
            f"Note: leaving the legacy {FROZEN_FILENAME} alone — it records "
            f"channel '{loaded.ignored_legacy_channel}', not '{channel_name}' "
            f"(it migrates to a per-stream file on that channel's next sync)."
        )

    # Channel patterns plus CLI additions; the scan runs against the
    # (possibly language-restricted) manifest, so patterns are matched on
    # destination-relative paths.
    patterns = tuple(dict.fromkeys((*channel_evergreen, *evergreen_patterns)))
    scan = (
        scan_evergreen(manifest=manifest, patterns=patterns, dest_root=dest_path)
        if patterns
        else EvergreenScan()
    )
    if scan.topic_owned_matches:
        click.echo(
            f"Warning: evergreen pattern(s) matched {len(scan.topic_owned_matches)} "
            f"topic-owned file(s) — evergreen applies only to global (skeleton) "
            f"files; use --refreeze to update released topic content: "
            + ", ".join(scan.topic_owned_matches[:5])
            + (", …" if len(scan.topic_owned_matches) > 5 else ""),
            err=True,
        )

    refreeze = set(ledger.released) if refreeze_all else set(refreeze_ids)
    # Presence-as-frozen (issue #325): a needed skeleton copy is restricted to
    # the files missing at the destination, so a stream joining a shared repo
    # never overwrites the skeleton another stream already froze there.
    skeleton_scan = (
        scan_skeleton(manifest=manifest, dest_root=dest_path)
        if not frozen.skeleton_frozen
        else None
    )
    plan = plan_sync(
        manifest=manifest,
        ledger_released=ledger.released,
        frozen=frozen,
        refreeze=refreeze,
        evergreen=scan.plans,
        skeleton=skeleton_scan,
    )

    if manifest.get("partial"):
        click.echo(
            "Note: the source build manifest is partial (the build reported "
            "errors); topics that failed in that build are refused below "
            "and promote once a build succeeds for them."
        )
    skeleton_line = (
        f"Channel '{channel_name or '?'}': "
        f"skeleton {'copy' if plan.copy_skeleton else 'frozen'} "
        f"({plan.skeleton_file_count} files)"
    )
    if plan.skeleton_present_count:
        skeleton_line += f", {plan.skeleton_present_count} already present (kept)"
    click.echo(skeleton_line)
    for topic_plan in plan.topics:
        click.echo(
            f"  {topic_plan.action:<11} {topic_plan.topic_id} ({topic_plan.file_count} files)"
        )
    # Refreshes the skeleton copy itself does not already deliver — on a plain
    # first sync that is none, so the lines only appear once the skeleton is
    # frozen (or kept via presence-as-frozen, issue #325).
    for evergreen_plan in plan.evergreen_refresh:
        click.echo(f"  {REFRESH:<11} {evergreen_plan.path} (evergreen)")
    if plan.evergreen and not plan.copy_skeleton:
        up_to_date = sum(1 for e in plan.evergreen if e.action != REFRESH)
        if up_to_date:
            click.echo(f"  evergreen: {up_to_date} file(s) up-to-date")

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
    frozen.save(loaded.path)
    if loaded.adopted_legacy is not None:
        loaded.adopted_legacy.unlink(missing_ok=True)
        click.echo(
            f"Migrated the legacy {FROZEN_FILENAME} to the per-stream "
            f"{loaded.path.name} (issue #325)."
        )
    click.echo(
        f"Copied {result.files_copied} file(s): "
        f"{len(result.copied_topics)} newly frozen, "
        f"{len(result.refrozen_topics)} re-frozen, "
        f"{len(result.skipped_topics)} already frozen (skipped)."
    )
    if result.refreshed_files:
        click.echo(
            f"Evergreen: refreshed {len(result.refreshed_files)} file(s): "
            + ", ".join(result.refreshed_files)
        )
    if result.failed_topics:
        click.echo(
            f"Warning: {len(result.failed_topics)} released topic(s) NOT promoted "
            f"— they failed in the source build: {', '.join(result.failed_topics)}. "
            f"Rebuild, then re-run sync.",
            err=True,
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
