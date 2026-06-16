"""Git operations for course output directories.

This module provides commands for managing git repositories in course output
directories, enabling trainers to commit and push generated course content.
"""

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from contextvars import ContextVar
from pathlib import Path

import click

from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import (
    DEFAULT_OUTPUT_TARGET_SPECS,
    CourseSpec,
    CourseSpecError,
    release_channel_ref,
)
from clm.core.provenance_manifest import MANIFEST_FILENAME
from clm.infrastructure.config import get_config

logger = logging.getLogger(__name__)

# Context variable for dry-run mode
_dry_run_mode: ContextVar[bool] = ContextVar("dry_run_mode", default=False)

#: Opt-in switch for token-authenticated HTTPS git transport (issue #341).
TOKEN_AUTH_ENV_VAR = "CLM_GIT_TOKEN_AUTH"
_TRUTHY = ("1", "true", "yes", "on")


def _token_auth_config_args() -> list[str]:
    """``git -c`` options that authenticate HTTPS transport with the GitLab token.

    Headless environments (CI, cron, containers) have no credential helper,
    so ``clm git push``/``sync --push`` fail with ``could not read Username``
    (issue #341). With ``CLM_GIT_TOKEN_AUTH=1`` and a token in
    ``CLM_GITLAB_TOKEN``/``GITLAB_TOKEN``, an ephemeral credential helper is
    injected instead: the token never appears in the URL, in ``.git/config``,
    or on the command line — the helper reads it from the environment when
    git asks for credentials. GitLab accepts ``oauth2:<token>`` basic auth
    for PAT/OAuth tokens (an ``Authorization: Bearer`` header would be
    rejected, so a credential helper — not ``http.extraHeader`` — is the
    right mechanism).

    Opt-in by design: a workstation with stored credentials (e.g. Git
    Credential Manager) keeps using them unless this is explicitly enabled.
    The empty first helper clears configured helpers so the token takes
    precedence and no credential dialog can pop up in CI. The options are
    only consulted when a command needs authentication, so they are passed
    to every git invocation. Returns ``[]`` when disabled or no token is set.
    """
    if os.environ.get(TOKEN_AUTH_ENV_VAR, "").strip().lower() not in _TRUTHY:
        return []
    from clm.infrastructure.gitlab_api import TOKEN_ENV_VARS

    token_var = next((v for v in TOKEN_ENV_VARS if os.environ.get(v, "").strip()), None)
    if token_var is None:
        logger.warning(
            "%s is enabled but no token is set in %s; git will use its default credentials.",
            TOKEN_AUTH_ENV_VAR,
            "/".join(TOKEN_ENV_VARS),
        )
        return []
    helper = f'!f() {{ echo username=oauth2; echo "password=${token_var}"; }}; f'
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


# =============================================================================
# Data Classes for Output Repositories
# =============================================================================


class OutputRepo:
    """Represents an output directory that may have a git repository.

    ``source`` distinguishes an ``<output-target>`` repo (``"output"``) from a
    per-cohort release channel repo (``"channel"``, issue #208). A channel
    repo's ``language`` is the empty string unless the channel is
    language-scoped via its ``lang`` attribute (issue #293).
    """

    def __init__(
        self,
        path: Path,
        target_name: str,
        language: str,
        remote_url: str | None = None,
        source: str = "output",
    ):
        self.path = path
        self.target_name = target_name
        self.language = language
        self.remote_url = remote_url
        self.source = source
        # Other channels releasing into this same destination (issue #325);
        # filled in by _dedupe_shared_destinations so a shared repo is visited
        # once but its display still names every stream it serves.
        self.shared_refs: list[str] = []

    @property
    def git_dir(self) -> Path:
        return self.path / ".git"

    @property
    def has_git(self) -> bool:
        return self.git_dir.is_dir()

    @property
    def display_name(self) -> str:
        # Channel repos carry no language, so drop the empty trailing segment.
        name = self.target_name if not self.language else f"{self.target_name}/{self.language}"
        if self.shared_refs:
            name += " (+ " + ", ".join(self.shared_refs) + ")"
        return name

    def has_remote(self) -> bool:
        """Check if this repo has a remote configured."""
        if not self.has_git:
            return False
        result = run_git(self.path, "remote", "get-url", "origin")
        return result.returncode == 0


# =============================================================================
# Git Helper Functions
# =============================================================================


def _format_command(cmd: list[str]) -> str:
    """Format a command list for display with proper shell quoting.

    Uses shlex.join() to properly quote arguments containing spaces,
    special characters, etc.
    """
    return shlex.join(cmd)


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the specified repository.

    Args:
        repo_path: Path to the repository
        *args: Git command arguments

    Returns:
        CompletedProcess with stdout/stderr captured.
        In dry-run mode, returns a mock result with returncode=0.
    """
    cmd = ["git", *_token_auth_config_args(), "-C", str(repo_path), *args]
    logger.debug(f"Running: {_format_command(cmd)}")

    if _dry_run_mode.get():
        click.echo(f"  [dry-run] Would run: {_format_command(cmd)}")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def run_git_global(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command without specifying a repository.

    Args:
        *args: Git command arguments

    Returns:
        CompletedProcess with stdout/stderr captured.
        In dry-run mode, returns a mock result with returncode=0.
    """
    cmd = ["git", *_token_auth_config_args(), *args]
    logger.debug(f"Running: {_format_command(cmd)}")

    if _dry_run_mode.get():
        click.echo(f"  [dry-run] Would run: {_format_command(cmd)}")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="",
            stderr="",
        )

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def remote_exists(url: str) -> bool:
    """Check if a remote repository exists and is accessible.

    Uses git ls-remote to check remote existence without cloning.
    Returns True even for empty repositories (no commits/refs).
    """
    # Do NOT use --exit-code here: that flag returns exit code 2 when
    # the remote has no matching refs (i.e., an empty repository),
    # which would misclassify empty repos as nonexistent.
    result = run_git_global("ls-remote", url)
    return result.returncode == 0


def remote_has_commits(url: str) -> bool:
    """Check if a remote repository has any commits.

    Returns False if remote doesn't exist or is empty.
    """
    result = run_git_global("ls-remote", "--heads", url)
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def is_behind_remote(repo_path: Path, branch: str = "master") -> tuple[bool, int]:
    """Check if local branch is behind remote.

    Returns:
        Tuple of (is_behind, commit_count)
    """
    # Fetch first to ensure we have latest remote refs
    run_git(repo_path, "fetch", "origin")

    # Count commits that are on remote but not local
    result = run_git(repo_path, "rev-list", "--count", f"HEAD..origin/{branch}")
    if result.returncode != 0:
        return False, 0

    count = int(result.stdout.strip())
    return count > 0, count


def get_remote_status(repo_path: Path, branch: str = "master") -> tuple[int, int]:
    """Get ahead/behind counts relative to remote tracking branch.

    Returns:
        Tuple of (ahead_count, behind_count)
    """
    # Fetch first to ensure we have latest remote refs
    run_git(repo_path, "fetch", "origin")

    # Get ahead count (commits in local not in remote)
    ahead_result = run_git(repo_path, "rev-list", "--count", f"origin/{branch}..HEAD")
    ahead = 0
    if ahead_result.returncode == 0 and ahead_result.stdout.strip():
        ahead = int(ahead_result.stdout.strip())

    # Get behind count (commits in remote not in local)
    behind_result = run_git(repo_path, "rev-list", "--count", f"HEAD..origin/{branch}")
    behind = 0
    if behind_result.returncode == 0 and behind_result.stdout.strip():
        behind = int(behind_result.stdout.strip())

    return ahead, behind


def has_uncommitted_changes(repo_path: Path) -> bool:
    """Check if repository has uncommitted changes."""
    result = run_git(repo_path, "status", "--porcelain")
    return bool(result.stdout.strip())


def has_staged_changes(repo_path: Path) -> bool:
    """Check whether the index has changes staged for commit.

    Unlike :func:`has_uncommitted_changes` (which inspects the *working tree*
    via ``git status --porcelain`` and therefore still reports an excluded,
    untracked ``.clm-manifest.json``), this is index-scoped. It is the correct
    gate for the "anything to commit?" decision after
    :func:`_stage_all_excluding_sidecars`: a change that touches only the
    excluded manifest leaves the index empty and must be treated as a no-op
    rather than a failed ``git commit``.
    """
    # `git diff --cached --quiet` exits 1 when staged differences exist, 0 when
    # the index matches HEAD (or the empty tree, on a repo with no commits yet).
    result = run_git(repo_path, "diff", "--cached", "--quiet")
    return result.returncode != 0


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    result = run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        return "master"  # Default
    return result.stdout.strip()


# =============================================================================
# Repository Discovery
# =============================================================================


def find_output_repos(
    spec_file: Path,
    target_filter: str | None = None,
) -> list[OutputRepo]:
    """Find all output repositories for a course spec.

    Targets that are not distributed — an explicit ``distribute="false"`` or,
    by default, a target feeding a release stream (issue #292) — are skipped
    when enumerating all targets. Naming one explicitly via *target_filter*
    still selects it: an explicit request wins over the default-safe skip.

    Args:
        spec_file: Path to course spec file
        target_filter: Optional target name to filter by

    Returns:
        List of OutputRepo objects for directories with or needing git repos
    """
    spec = CourseSpec.from_file(spec_file)
    course_root, _default_output = resolve_course_paths(spec_file)
    github_config = spec.github
    config = get_config()
    remote_template = config.git.remote_template
    config_remote_path = config.git.remote_path

    repos: list[OutputRepo] = []

    if spec.output_targets:
        # Course has explicit output targets
        for i, target_spec in enumerate(spec.output_targets):
            if target_filter and target_spec.name != target_filter:
                continue
            if not target_filter and not spec.is_distributed_target(target_spec):
                logger.debug(
                    "Skipping non-distributed output target %r (release build source "
                    'or distribute="false")',
                    target_spec.name,
                )
                continue

            # Resolve path
            path = Path(target_spec.path)
            if not path.is_absolute():
                path = course_root / path

            # Get languages for this target
            languages = target_spec.languages or ["de", "en"]

            # Resolve remote_path: per-target > config > course-level (on GitHubSpec)
            effective_remote_path = target_spec.remote_path or config_remote_path

            for lang in languages:
                # Explicit targets use: path / dir_name
                output_path = path / spec.output_dir_name[lang]

                remote_url = github_config.derive_remote_url(
                    target_spec.name,
                    lang,
                    is_first_target=(i == 0),
                    project_slug=spec.project_slug,
                    remote_template=remote_template,
                    remote_path=effective_remote_path,
                )

                repos.append(
                    OutputRepo(
                        path=output_path,
                        target_name=target_spec.name,
                        language=lang,
                        remote_url=remote_url,
                    )
                )
    else:
        # Course uses the default output structure: shared/trainer/speaker
        # (issue #383). ``clm build`` writes all three tiers, so ``clm git``
        # manages all three rather than the old hardcoded ``public``/``speaker``
        # pair — closing the build/git asymmetry of issue #381. The ``speaker``
        # tier is always *listed* (so it is never silently invisible), but its
        # remote is derived only when ``<include-speaker>`` opts in; otherwise it
        # stays a local-only repo and recording material is not pushed by default.
        for i, target_spec in enumerate(DEFAULT_OUTPUT_TARGET_SPECS):
            if target_filter and target_spec.name != target_filter:
                continue

            target_path = course_root / target_spec.path
            effective_remote_path = target_spec.remote_path or config_remote_path

            for lang in ["de", "en"]:
                output_path = target_path / spec.output_dir_name[lang]

                if target_spec.name == "speaker" and not github_config.include_speaker:
                    remote_url = None
                else:
                    remote_url = github_config.derive_remote_url(
                        target_spec.name,
                        lang,
                        is_first_target=(i == 0),
                        project_slug=spec.project_slug,
                        remote_template=remote_template,
                        remote_path=effective_remote_path,
                    )

                repos.append(
                    OutputRepo(
                        path=output_path,
                        target_name=target_spec.name,
                        language=lang,
                        remote_url=remote_url,
                    )
                )

    return repos


def find_release_channel_repos(
    spec_file: Path,
    channel_filter: str | None = None,
) -> list[OutputRepo]:
    """Find the per-cohort release-channel repositories for a course (#208, #291).

    Mirrors :func:`find_output_repos` but enumerates the ``<release-channels>``
    blocks (one per release stream) instead of ``<output-targets>``. Each
    channel is a single cohort repo whose working tree is the channel ``path``
    (resolved under the course root, identically to ``clm release``). The remote
    URL is best-effort (used only by ``clm git init``); push/commit operate on
    whatever ``origin`` the repo actually has.

    Args:
        spec_file: Path to the course spec file.
        channel_filter: Optional channel address (``stream/channel`` or a
            unique bare channel name) to restrict the result to. Raises
            :class:`CourseSpecError` when it does not resolve.

    Returns:
        One :class:`OutputRepo` per matching channel (``source="channel"``,
        ``target_name`` = the canonical channel address); empty when the spec
        declares no ``<release-channels>`` block.
    """
    spec = CourseSpec.from_file(spec_file)
    if not spec.release_channel_blocks:
        return []

    if channel_filter:
        pairs = [spec.resolve_release_channel(channel_filter)]
    else:
        pairs = list(spec.iter_release_channels())

    course_root, _ = resolve_course_paths(spec_file)
    github_config = spec.github
    config = get_config()
    remote_template = config.git.remote_template
    config_remote_path = config.git.remote_path

    repos: list[OutputRepo] = []
    for block, channel in pairs:
        path = Path(channel.path)
        if not path.is_absolute():
            path = course_root / path

        effective_remote_path = channel.remote_path or config_remote_path
        remote_url = github_config.derive_channel_remote_url(
            channel.name,
            project_slug=spec.project_slug,
            remote_template=remote_template,
            remote_path=effective_remote_path,
            stream=block.name,
            language=channel.lang,
            repo_override=channel.repo,
        )

        repos.append(
            OutputRepo(
                path=path,
                # A language-scoped channel (issue #293) carries its lang so the
                # display name reads e.g. "materials/2026-04/de"; the repo path
                # is still the single channel dest (one repo per channel).
                target_name=release_channel_ref(block, channel),
                language=channel.lang,
                remote_url=remote_url,
                source="channel",
            )
        )

    return repos


def _select_repos(
    spec_file: Path,
    *,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool = False,
) -> list[OutputRepo]:
    """Resolve the repo set a ``clm git`` subcommand should act on.

    Default is the ``<output-targets>`` repos (optionally filtered by
    ``--target``). ``--channel NAME`` or ``--all-channels`` switches to the
    per-cohort release-channel repos instead; the two modes are mutually
    exclusive with ``--target``.

    ``--all`` unions both worlds: every distributed output target *plus* every
    release-channel repo, visited once each (issue: single push-everything
    command). A destination shared by several streams collapses to one entry
    via :func:`_dedupe_shared_destinations`, which also folds in the (rare)
    case of a channel path that coincides with an output target. It is mutually
    exclusive with ``--target``/``--channel``/``--all-channels`` and degrades to
    the plain output-target set on a course that declares no
    ``<release-channels>``.
    """
    if all_repos:
        if target or channel or all_channels:
            raise click.UsageError(
                "--all cannot be combined with --target/--channel/--all-channels."
            )
        output_repos = find_output_repos(spec_file)
        channel_repos = find_release_channel_repos(spec_file)
        return _dedupe_shared_destinations([*output_repos, *channel_repos])
    if channel or all_channels:
        if target:
            raise click.UsageError("--target cannot be combined with --channel/--all-channels.")
        spec = CourseSpec.from_file(spec_file)
        if not any(block.channels for block in spec.release_channel_blocks):
            raise click.ClickException(
                f"{spec_file.name} declares no <release-channels>; "
                f"--channel/--all-channels is not available for this course."
            )
        try:
            repos = find_release_channel_repos(spec_file, channel or None)
        except CourseSpecError as e:
            raise click.ClickException(str(e)) from None
        return _dedupe_shared_destinations(repos)
    return find_output_repos(spec_file, target)


def _dedupe_shared_destinations(repos: list[OutputRepo]) -> list[OutputRepo]:
    """Visit a destination shared by several streams only once (issue #325).

    Git operations act on the repository, not the release stream, so channels
    of different streams releasing into the same working tree collapse into a
    single entry. The first channel (spec declaration order) keeps the entry
    and the derived remote URL — only ``clm git init`` consumes the
    derivation, and a shared repo can only have one origin; a differing
    derivation from a collapsed channel is surfaced as a note.
    """
    by_path: dict[Path, OutputRepo] = {}
    for repo in repos:
        kept = by_path.get(repo.path.resolve())
        if kept is None:
            by_path[repo.path.resolve()] = repo
            continue
        kept.shared_refs.append(repo.target_name)
        if repo.remote_url and repo.remote_url != kept.remote_url:
            click.echo(
                f"Note: channels {kept.target_name!r} and {repo.target_name!r} share "
                f"the destination {kept.path} but derive different remote URLs; "
                f"using the first ({kept.remote_url}). Set the repo's origin "
                f"explicitly if another URL is intended.",
                err=True,
            )
    return list(by_path.values())


def _stage_all_excluding_sidecars(repo_path: Path) -> None:
    """Stage every change except CLM's private build sidecars.

    The provenance manifest (``.clm-manifest.json``) records every output
    file's owning topic/section and source commit (issue #208). It is a private
    build artifact and must never be distributed in a student-facing output or
    cohort repo, so it is excluded from staging here regardless of whether the
    repo's ``.gitignore`` predates this exclusion. The per-cohort frozen
    manifest (``.clm-released.json``) is deliberately *not* excluded — it is the
    freeze record and belongs in the channel repo.

    The exclude pathspec only governs *new* staging; a manifest that a prior
    (pre-exclusion) commit already tracked would otherwise stay published and go
    stale. So we first drop any tracked manifest from the index — ``git rm
    --cached --ignore-unmatch`` is a no-op for clean repos and for repos with no
    commits yet, and stages a deletion when it was tracked. Both steps match the
    manifest at any depth (a build only ever writes it at the output root today,
    but the recursive glob keeps the guard honest about its own invariant).
    """
    run_git(
        repo_path,
        "rm",
        "--cached",
        "--ignore-unmatch",
        "--quiet",
        "--",
        MANIFEST_FILENAME,
        f":(glob)**/{MANIFEST_FILENAME}",
    )
    run_git(
        repo_path,
        "add",
        "-A",
        "--",
        ".",
        f":(exclude){MANIFEST_FILENAME}",
        f":(exclude,glob)**/{MANIFEST_FILENAME}",
    )


def commit_and_push_repo(
    repo: OutputRepo,
    message: str | None,
    *,
    amend: bool = False,
    force_with_lease: bool = False,
    remote_ahead_hint: list[str] | None = None,
) -> bool:
    """Stage (sidecar-excluded), commit, and push one already-initialized repo.

    The reusable core of ``clm git sync``'s per-repo loop, shared with
    ``clm release sync --push`` (issue #208) so both paths apply the same
    staging exclusion (:func:`_stage_all_excluding_sidecars`), index-scoped
    commit gate (:func:`has_staged_changes`), remote-ahead guard, and push
    policy. The caller echoes the ``[name] path`` header and verifies
    ``repo.has_git`` first; this helper assumes a git repository.

    Progress is echoed with the same wording ``clm git sync`` has always used.
    No trailing blank line is printed — the caller prints exactly one after each
    repo. Returns ``True`` when the repo ends in the intended state (committed
    and/or pushed, or a clean no-op) and ``False`` on any git error or a blocked
    non-force push against an ahead remote.

    ``amend`` implies a force push at the call site (``clm git sync`` sets
    ``force_with_lease=True`` whenever ``amend`` is given); this helper does not
    re-derive that coupling. ``remote_ahead_hint`` is an optional list of command
    lines printed (indented, to stderr) under "To resolve:" when a non-force push
    is blocked because the remote is ahead.
    """
    has_remote = repo.has_remote()
    branch = get_current_branch(repo.path)

    # Refuse to clobber an ahead remote unless we are explicitly force-pushing.
    if has_remote and not force_with_lease:
        behind, count = is_behind_remote(repo.path, branch)
        if behind:
            click.echo(
                f"  Error: Remote 'origin/{branch}' is {count} commit(s) ahead",
                err=True,
            )
            if remote_ahead_hint:
                click.echo("", err=True)
                click.echo("  To resolve:", err=True)
                for line in remote_ahead_hint:
                    click.echo(f"      {line}", err=True)
            return False

    # Stage all changes (excluding the private build manifest).
    _stage_all_excluding_sidecars(repo.path)

    if amend:
        if message:
            commit_args = ["commit", "--amend", "-m", message]
        else:
            commit_args = ["commit", "--amend", "--no-edit"]
        result = run_git(repo.path, *commit_args)
        if result.returncode == 0:
            click.echo(f"  Amended commit{': ' + message if message else ''}")
        else:
            click.echo(f"  Error amending: {result.stderr.strip()}", err=True)
            return False
    else:
        # Gate on the index (not the working tree) so a change that touches only
        # the excluded manifest is a clean no-op rather than a failed commit.
        if has_staged_changes(repo.path):
            assert message is not None
            result = run_git(repo.path, "commit", "-m", message)
            if result.returncode == 0:
                click.echo(f"  Committed: {message}")
            else:
                click.echo(f"  Error committing: {result.stderr.strip()}", err=True)
                return False
        else:
            click.echo("  No changes to commit")

    if has_remote:
        push_args = ["push"]
        if force_with_lease:
            push_args.append("--force-with-lease")
        push_args.extend(["-u", "origin", branch])
        result = run_git(repo.path, *push_args)
        if result.returncode == 0:
            if force_with_lease:
                click.echo(f"  Force-pushed to origin/{branch}")
            else:
                click.echo(f"  Pushed to origin/{branch}")
        else:
            click.echo(f"  Error pushing: {result.stderr.strip()}", err=True)
            return False
    else:
        click.echo("  Skipped push: No remote configured")

    return True


# =============================================================================
# Init Implementation
# =============================================================================


def init_repo_fresh(repo: OutputRepo, branch: str) -> bool:
    """Initialize a fresh git repository (no remote or empty remote).

    Returns True on success.
    """
    # Create .gitignore
    gitignore_content = """# Python
__pycache__/
*.py[cod]
*$py.class
.Python
*.so

# OS files
.DS_Store
Thumbs.db

# Editor files
*.swp
*.swo
*~
.idea/
.vscode/

# Temporary files
*.tmp
*.temp

# CLM private build sidecars (never distribute the provenance manifest)
.clm-manifest.json
"""
    gitignore_path = repo.path / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(gitignore_content)

    # Initialize repo
    result = run_git(repo.path, "init")
    if result.returncode != 0:
        click.echo(f"  Error: Failed to initialize repository: {result.stderr}", err=True)
        return False

    # Create initial branch
    run_git(repo.path, "checkout", "-b", branch)

    # Add remote if URL is available and remote exists
    if repo.remote_url and remote_exists(repo.remote_url):
        run_git(repo.path, "remote", "add", "origin", repo.remote_url)
        click.echo(f"  Remote set to: {repo.remote_url}")

    # Initial commit with all existing files (never the private build manifest)
    _stage_all_excluding_sidecars(repo.path)
    result = run_git(repo.path, "commit", "-m", "Initial commit")
    if result.returncode == 0:
        click.echo("  Created initial commit")
    elif "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
        click.echo("  No files to commit (empty directory)")

    return True


def init_repo_from_remote(repo: OutputRepo, branch: str) -> bool:
    """Initialize repository by restoring from existing remote.

    This handles the crash recovery case where .git was lost but files exist.
    Clones into temp, moves .git, preserves working files.

    Returns True on success.
    """
    if not repo.remote_url:
        click.echo("  Error: No remote URL configured", err=True)
        return False

    click.echo(f"  Restoring from remote: {repo.remote_url}")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / "clone"

        # Clone the remote
        result = run_git_global("clone", repo.remote_url, str(temp_path))
        if result.returncode != 0:
            click.echo(f"  Error: Failed to clone remote: {result.stderr}", err=True)
            return False

        # Move .git to output directory
        source_git = temp_path / ".git"
        target_git = repo.git_dir

        try:
            shutil.move(str(source_git), str(target_git))
        except Exception as e:
            click.echo(f"  Error: Failed to restore .git directory: {e}", err=True)
            return False

    click.echo("  Restored git history from remote")

    # Show status
    result = run_git(repo.path, "status", "--short")
    if result.stdout.strip():
        lines = result.stdout.strip().split("\n")
        click.echo(f"  {len(lines)} file(s) differ from remote HEAD")
    else:
        click.echo("  Working directory matches remote HEAD")

    return True


# =============================================================================
# Init Logic Helpers
# =============================================================================


def _init_handle_existing_repo(repo: OutputRepo) -> None:
    """Handle init for a repo that already has a .git directory.

    If the remote exists but isn't configured locally, add it.
    Otherwise skip with an informative message.
    """
    if not repo.remote_url:
        click.echo("  Already initialized (no remote configured)")
        return

    if repo.has_remote():
        click.echo("  Already initialized")
        return

    # Local repo exists but has no remote — check if remote is available
    if remote_exists(repo.remote_url):
        run_git(repo.path, "remote", "add", "origin", repo.remote_url)
        click.echo(f"  Added remote: {repo.remote_url}")
    else:
        click.echo("  Already initialized (remote not yet created)")
        click.echo(f"  Remote URL: {repo.remote_url}")
        click.echo("  Run 'clm git init' again after creating the remote repository.")


def _init_create_new_repo(repo: OutputRepo, branch: str) -> None:
    """Handle init for a repo that has no .git directory yet."""
    if not repo.remote_url:
        click.echo("  Creating local-only repository...")
        init_repo_fresh(repo, branch)
    elif not remote_exists(repo.remote_url):
        click.echo("  Creating local-only repository (remote not found)...")
        init_repo_fresh(repo, branch)
        click.echo(f"  Remote URL: {repo.remote_url}")
        click.echo("  Run 'clm git init' again after creating the remote repository.")
    elif not remote_has_commits(repo.remote_url):
        click.echo("  Creating repository with empty remote...")
        init_repo_fresh(repo, branch)
    else:
        # Remote exists with commits — recovery mode
        init_repo_from_remote(repo, branch)


# =============================================================================
# Click Command Group
# =============================================================================

# Shared options that switch a subcommand from <output-targets> repos to the
# per-cohort <release-channels> repos (issues #208, #291). Mutually exclusive
# with --target; see _select_repos.
_channel_option = click.option(
    "--channel",
    default=None,
    help="Act on the named release-channel (cohort) repo instead of output "
    "targets. Address a channel in a named stream as STREAM/CHANNEL "
    "(e.g. materials/2026-04); a bare name works when unique.",
)
_all_channels_option = click.option(
    "--all-channels",
    is_flag=True,
    help="Act on every release-channel (cohort) repo of every stream instead of output targets.",
)
_all_option = click.option(
    "--all",
    "all_repos",
    is_flag=True,
    help="Act on every distributed output target AND every release-channel repo in one pass. "
    "Mutually exclusive with --target/--channel/--all-channels.",
)


@click.group(name="git")
def git_group():
    """Manage git repositories for course output directories.

    These commands help maintain git repositories in course output directories,
    enabling version control and distribution of generated course materials.

    Common workflow:
      clm build <spec-file>              # Generate course
      clm git init <spec-file>           # Set up git repos
      clm git sync <spec-file> -m "msg"  # Commit and push
    """
    pass


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--branch", default="master", help="Default branch name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def init(
    spec_file: Path,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    branch: str,
    dry_run: bool,
):
    """Initialize git repositories in output directories.

    For each output target directory:
    - No local repo, no remote: create local-only repository
    - No local repo, remote exists: clone/restore from remote
    - Local repo exists, no remote: skip (print remote URL if configured)
    - Local repo exists, remote exists: add remote origin if not yet configured

    Use --channel NAME / --all-channels to initialize per-cohort release
    repositories instead of output targets (issue #208). Use --all to
    initialize output targets and every release channel in one pass.

    \b
    Examples:
        clm git init course.xml                # Initialize all targets
        clm git init course.xml --target students  # Initialize specific target
        clm git init course.xml --channel jan      # Initialize a cohort repo
        clm git init course.xml --all          # Targets + every release channel
        clm git init course.xml --dry-run      # Show what would be done
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    click.echo(f"Initializing git repositories for {spec_file.name}...")
    click.echo()

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        # Check if directory exists
        if not repo.path.exists():
            click.echo("  Skipped: Directory does not exist (run 'clm build' first)")
            click.echo()
            continue

        if repo.has_git:
            # Local repo already exists — check if we need to add a remote
            _init_handle_existing_repo(repo)
        else:
            # No local repo — determine initialization mode
            _init_create_new_repo(repo, branch)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--dry-run", is_flag=True, help="Show paths that would be checked")
def status(
    spec_file: Path,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    dry_run: bool,
):
    """Show git status of output directories.

    Displays the git status for each output target that has a repository.
    Use --channel NAME / --all-channels to report on per-cohort release
    repositories instead of output targets (issue #208). Use --all to report
    on output targets and every release channel in one pass.

    \b
    Examples:
        clm git status course.xml
        clm git status course.xml --target students
        clm git status course.xml --all-channels
        clm git status course.xml --all
        clm git status course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - Showing paths that would be checked]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.path.exists():
            click.echo("  Directory does not exist")
            click.echo()
            continue

        if not repo.has_git:
            click.echo("  No git repository (run 'clm git init')")
            click.echo()
            continue

        # Show branch
        branch = get_current_branch(repo.path)
        click.echo(f"  Branch: {branch}")

        # Show remote
        has_remote = repo.has_remote()
        if has_remote:
            result = run_git(repo.path, "remote", "get-url", "origin")
            click.echo(f"  Remote: {result.stdout.strip()}")

            # Show ahead/behind status
            ahead, behind = get_remote_status(repo.path, branch)
            if ahead == 0 and behind == 0:
                click.echo("  Sync: Up to date with remote")
            else:
                parts = []
                if ahead > 0:
                    parts.append(f"{ahead} ahead")
                if behind > 0:
                    parts.append(f"{behind} behind")
                click.echo(f"  Sync: {', '.join(parts)}")
        else:
            click.echo("  Remote: (none)")

        # Show status
        result = run_git(repo.path, "status", "--short")
        if result.stdout.strip():
            click.echo("  Changes:")
            for line in result.stdout.strip().split("\n"):
                click.echo(f"    {line}")
        else:
            click.echo("  Clean (no uncommitted changes)")

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("-m", "--message", default=None, help="Commit message")
@click.option("--amend", is_flag=True, help="Amend the previous commit")
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def commit(
    spec_file: Path,
    message: str | None,
    amend: bool,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    dry_run: bool,
):
    """Stage all changes and commit.

    Stages all files (except the private ``.clm-manifest.json``) and creates a
    commit with the given message. Skips repositories with no changes. Use
    --channel NAME / --all-channels to commit per-cohort release repositories
    instead of output targets (issue #208). Use --all to commit output targets
    and every release channel in one pass.

    \b
    Examples:
        clm git commit course.xml -m "Update lecture notes"
        clm git commit course.xml --amend
        clm git commit course.xml --amend -m "New message"
        clm git commit course.xml -m "Fix typos" --target students
        clm git commit course.xml -m "Release functions" --channel jan
        clm git commit course.xml -m "Weekly update" --all
    """
    if not message and not amend:
        raise click.UsageError("Either -m/--message or --amend must be provided.")

    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.has_git:
            click.echo("  Skipped: No git repository")
            click.echo()
            continue

        # Stage all changes (excluding the private build manifest)
        _stage_all_excluding_sidecars(repo.path)

        # Check if there is anything *staged* to commit (skip for non-amend).
        # Gate on the index, not the working tree, so a change that touches only
        # the excluded manifest is a clean no-op instead of a failed commit.
        if not amend and not has_staged_changes(repo.path):
            click.echo("  Nothing to commit (working tree clean)")
            click.echo()
            continue

        # Build commit command
        if amend:
            if message:
                commit_args = ["commit", "--amend", "-m", message]
            else:
                commit_args = ["commit", "--amend", "--no-edit"]
        else:
            assert message is not None
            commit_args = ["commit", "-m", message]

        result = run_git(repo.path, *commit_args)
        if result.returncode == 0:
            if amend:
                click.echo(f"  Amended commit{': ' + message if message else ''}")
            else:
                click.echo(f"  Committed: {message}")
        else:
            click.echo(f"  Error: {result.stderr.strip()}", err=True)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--force-with-lease", is_flag=True, help="Force push with lease (safe force push)")
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def push(
    spec_file: Path,
    force_with_lease: bool,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    dry_run: bool,
):
    """Push commits to remote.

    Pushes commits to the configured remote. Skips repositories without remotes.
    Use --channel NAME / --all-channels to push per-cohort release repositories
    instead of output targets (issue #208). Use --all to push every distributed
    output target AND every release channel in one command — the single
    push-everything workflow.

    \b
    Examples:
        clm git push course.xml
        clm git push course.xml --force-with-lease
        clm git push course.xml --target students
        clm git push course.xml --channel jan
        clm git push course.xml --all
        clm git push course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.has_git:
            click.echo("  Skipped: No git repository")
            click.echo()
            continue

        if not repo.has_remote():
            click.echo("  Skipped: No remote configured")
            click.echo()
            continue

        # Get current branch
        branch = get_current_branch(repo.path)

        # Push
        push_args = ["push"]
        if force_with_lease:
            push_args.append("--force-with-lease")
        push_args.extend(["-u", "origin", branch])

        result = run_git(repo.path, *push_args)
        if result.returncode == 0:
            if force_with_lease:
                click.echo(f"  Force-pushed to origin/{branch}")
            else:
                click.echo(f"  Pushed to origin/{branch}")
        else:
            click.echo(f"  Error: {result.stderr.strip()}", err=True)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("-m", "--message", default=None, help="Commit message")
@click.option(
    "--amend", is_flag=True, help="Amend the previous commit (implies --force-with-lease)"
)
@click.option("--force-with-lease", is_flag=True, help="Force push with lease (safe force push)")
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def sync(
    spec_file: Path,
    message: str | None,
    amend: bool,
    force_with_lease: bool,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    dry_run: bool,
):
    """Commit and push in one operation.

    This is the most common workflow: stage all changes, commit, and push.
    Checks if remote is ahead first and aborts with instructions if so.

    Use --amend to amend the previous commit and force-push. Use
    --force-with-lease for a safe force push without amending. Use
    --channel NAME / --all-channels to sync per-cohort release repositories
    instead of output targets (issue #208). Use --all to commit-and-push every
    distributed output target AND every release channel in one command.

    \b
    Examples:
        clm git sync course.xml -m "Weekly update"
        clm git sync course.xml --amend
        clm git sync course.xml --amend -m "New message"
        clm git sync course.xml --force-with-lease -m "Update"
        clm git sync course.xml --channel jan -m "Release functions"
        clm git sync course.xml --all -m "Weekly update"
    """
    if not message and not amend:
        raise click.UsageError("Either -m/--message or --amend must be provided.")

    # --amend implies --force-with-lease
    if amend:
        force_with_lease = True

    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    errors_found = False

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.has_git:
            click.echo("  Skipped: No git repository")
            click.echo()
            continue

        # The remote-ahead recovery hint is command-specific; pass the
        # ``clm git`` recipe so the shared helper can print it verbatim.
        remote_ahead_hint = [
            f"clm git reset {spec_file}",
            f"clm build {spec_file}",
            f'clm git sync {spec_file} -m "{message}"',
        ]
        if not commit_and_push_repo(
            repo,
            message,
            amend=amend,
            force_with_lease=force_with_lease,
            remote_ahead_hint=remote_ahead_hint,
        ):
            errors_found = True

        click.echo()

    if errors_found:
        raise SystemExit(1)


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@_channel_option
@_all_channels_option
@_all_option
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def reset(
    spec_file: Path,
    target: str | None,
    channel: str | None,
    all_channels: bool,
    all_repos: bool,
    dry_run: bool,
):
    """Reset local repos to remote tracking branch.

    Fetches from remote and performs a hard reset to origin/<branch>.
    Use this to recover when remote is ahead of local. Use --channel NAME /
    --all-channels to reset per-cohort release repositories instead of output
    targets (issue #208). Use --all to reset output targets and every release
    channel in one pass.

    WARNING: This will discard all local changes!

    After reset, run 'clm build' to regenerate (fast due to cache),
    then 'clm git sync' to commit and push.

    \b
    Examples:
        clm git reset course.xml
        clm git reset course.xml --target students
        clm git reset course.xml --channel jan
        clm git reset course.xml --all
        clm git reset course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = _select_repos(
        spec_file, target=target, channel=channel, all_channels=all_channels, all_repos=all_repos
    )

    if not repos:
        click.echo("No output directories found.")
        return

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.has_git:
            click.echo("  Skipped: No git repository")
            click.echo()
            continue

        if not repo.has_remote():
            click.echo("  Skipped: No remote configured")
            click.echo()
            continue

        branch = get_current_branch(repo.path)

        # Fetch
        click.echo("  Fetching from origin...")
        result = run_git(repo.path, "fetch", "origin")
        if result.returncode != 0:
            click.echo(f"  Error fetching: {result.stderr.strip()}", err=True)
            click.echo()
            continue

        # Reset
        click.echo(f"  Resetting to origin/{branch}...")
        result = run_git(repo.path, "reset", "--hard", f"origin/{branch}")
        if result.returncode == 0:
            click.echo("  Reset complete")
        else:
            click.echo(f"  Error: {result.stderr.strip()}", err=True)

        click.echo()

    click.echo("Next steps:")
    click.echo(f"  1. clm build {spec_file}")
    click.echo(f'  2. clm git sync {spec_file} -m "<message>"')
