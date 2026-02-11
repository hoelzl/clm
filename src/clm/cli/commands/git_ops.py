"""Git operations for course output directories.

This module provides commands for managing git repositories in course output
directories, enabling trainers to commit and push generated course content.
"""

import logging
import shlex
import shutil
import subprocess
import tempfile
from contextvars import ContextVar
from pathlib import Path

import click

from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec
from clm.core.utils.text_utils import sanitize_file_name

logger = logging.getLogger(__name__)

# Context variable for dry-run mode
_dry_run_mode: ContextVar[bool] = ContextVar("dry_run_mode", default=False)


# =============================================================================
# Data Classes for Output Repositories
# =============================================================================


class OutputRepo:
    """Represents an output directory that may have a git repository."""

    def __init__(
        self,
        path: Path,
        target_name: str,
        language: str,
        remote_url: str | None = None,
    ):
        self.path = path
        self.target_name = target_name
        self.language = language
        self.remote_url = remote_url

    @property
    def git_dir(self) -> Path:
        return self.path / ".git"

    @property
    def has_git(self) -> bool:
        return self.git_dir.is_dir()

    @property
    def display_name(self) -> str:
        return f"{self.target_name}/{self.language}"

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
    cmd = ["git", "-C", str(repo_path), *args]
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
    cmd = ["git", *args]
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
    """
    result = run_git_global("ls-remote", "--exit-code", url)
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

    Args:
        spec_file: Path to course spec file
        target_filter: Optional target name to filter by

    Returns:
        List of OutputRepo objects for directories with or needing git repos
    """
    spec = CourseSpec.from_file(spec_file)
    course_root, default_output = resolve_course_paths(spec_file)
    github_config = spec.github
    course_name = spec.name  # Multilingual course name

    repos: list[OutputRepo] = []

    if spec.output_targets:
        # Course has explicit output targets
        for i, target_spec in enumerate(spec.output_targets):
            if target_filter and target_spec.name != target_filter:
                continue

            # Resolve path
            path = Path(target_spec.path)
            if not path.is_absolute():
                path = course_root / path

            # Get languages for this target
            languages = target_spec.languages or ["de", "en"]

            for lang in languages:
                # Build the actual output path (includes language and course name)
                # Explicit targets use: path / Lang / CourseName
                course_dir_name = sanitize_file_name(course_name[lang])
                output_path = path / lang.capitalize() / course_dir_name

                remote_url = github_config.derive_remote_url(
                    target_spec.name,
                    lang,
                    is_first_target=(i == 0),
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
        # Course uses default output structure (public/speaker)
        for target_name in ["public", "speaker"]:
            if target_filter and target_name != target_filter:
                continue

            # Skip speaker if not configured
            if target_name == "speaker" and not github_config.include_speaker:
                continue

            for lang in ["de", "en"]:
                # Default targets use: output / public|speaker / Lang / CourseName
                course_dir_name = sanitize_file_name(course_name[lang])
                output_path = default_output / target_name / lang.capitalize() / course_dir_name

                remote_url = github_config.derive_remote_url(target_name, lang)

                repos.append(
                    OutputRepo(
                        path=output_path,
                        target_name=target_name,
                        language=lang,
                        remote_url=remote_url,
                    )
                )

    return repos


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
    if repo.remote_url:
        if remote_exists(repo.remote_url):
            run_git(repo.path, "remote", "add", "origin", repo.remote_url)
            click.echo(f"  Remote set to: {repo.remote_url}")
        else:
            click.echo(f"  Info: Remote '{repo.remote_url}' does not exist.")
            click.echo("        Repository initialized as local-only.")
            click.echo("        Run 'clx git init' again after creating the repository.")

    # Initial commit with all existing files
    run_git(repo.path, "add", "-A")
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
# Click Command Group
# =============================================================================


@click.group(name="git")
def git_group():
    """Manage git repositories for course output directories.

    These commands help maintain git repositories in course output directories,
    enabling version control and distribution of generated course materials.

    Common workflow:
      clx build <spec-file>              # Generate course
      clx git init <spec-file>           # Set up git repos
      clx git sync <spec-file> -m "msg"  # Commit and push
    """
    pass


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@click.option("--branch", default="master", help="Default branch name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def init(spec_file: Path, target: str | None, branch: str, dry_run: bool):
    """Initialize git repositories in output directories.

    For each output target directory:
    - If .git already exists: skip (already initialized)
    - If remote doesn't exist: create local-only repository
    - If remote exists but is empty: create repo and set remote
    - If remote exists with commits: restore .git from remote (recovery mode)

    Examples:
        clx git init course.xml                # Initialize all targets
        clx git init course.xml --target students  # Initialize specific target
        clx git init course.xml --dry-run      # Show what would be done
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = find_output_repos(spec_file, target)

    if not repos:
        click.echo("No output directories found.")
        return

    click.echo(f"Initializing git repositories for {spec_file.name}...")
    click.echo()

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        # Check if directory exists
        if not repo.path.exists():
            click.echo("  Skipped: Directory does not exist (run 'clx build' first)")
            click.echo()
            continue

        # Check if already initialized
        if repo.has_git:
            click.echo("  Skipped: Repository already exists")
            click.echo()
            continue

        # Determine initialization mode
        if not repo.remote_url:
            # No remote configured
            click.echo("  Creating local-only repository...")
            init_repo_fresh(repo, branch)
        elif not remote_exists(repo.remote_url):
            # Remote URL configured but doesn't exist
            click.echo("  Creating local-only repository (remote not found)...")
            init_repo_fresh(repo, branch)
        elif not remote_has_commits(repo.remote_url):
            # Remote exists but is empty
            click.echo("  Creating repository with empty remote...")
            init_repo_fresh(repo, branch)
        else:
            # Remote exists with commits - recovery mode
            init_repo_from_remote(repo, branch)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@click.option("--dry-run", is_flag=True, help="Show paths that would be checked")
def status(spec_file: Path, target: str | None, dry_run: bool):
    """Show git status of output directories.

    Displays the git status for each output target that has a repository.

    Examples:
        clx git status course.xml
        clx git status course.xml --target students
        clx git status course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - Showing paths that would be checked]")
        click.echo()

    repos = find_output_repos(spec_file, target)

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
            click.echo("  No git repository (run 'clx git init')")
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
@click.option("-m", "--message", required=True, help="Commit message")
@click.option("--target", help="Specific output target name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def commit(spec_file: Path, message: str, target: str | None, dry_run: bool):
    """Stage all changes and commit.

    Stages all files (git add -A) and creates a commit with the given message.
    Skips repositories with no changes.

    Examples:
        clx git commit course.xml -m "Update lecture notes"
        clx git commit course.xml -m "Fix typos" --target students
        clx git commit course.xml -m "Update" --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = find_output_repos(spec_file, target)

    if not repos:
        click.echo("No output directories found.")
        return

    for repo in repos:
        click.echo(f"[{repo.display_name}] {repo.path}")

        if not repo.has_git:
            click.echo("  Skipped: No git repository")
            click.echo()
            continue

        # Stage all changes
        run_git(repo.path, "add", "-A")

        # Check if there are changes to commit
        if not has_uncommitted_changes(repo.path):
            click.echo("  Nothing to commit (working tree clean)")
            click.echo()
            continue

        # Commit
        result = run_git(repo.path, "commit", "-m", message)
        if result.returncode == 0:
            click.echo(f"  Committed: {message}")
        else:
            click.echo(f"  Error: {result.stderr.strip()}", err=True)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def push(spec_file: Path, target: str | None, dry_run: bool):
    """Push commits to remote.

    Pushes commits to the configured remote. Skips repositories without remotes.

    Examples:
        clx git push course.xml
        clx git push course.xml --target students
        clx git push course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = find_output_repos(spec_file, target)

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
        result = run_git(repo.path, "push", "-u", "origin", branch)
        if result.returncode == 0:
            click.echo(f"  Pushed to origin/{branch}")
        else:
            click.echo(f"  Error: {result.stderr.strip()}", err=True)

        click.echo()


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("-m", "--message", required=True, help="Commit message")
@click.option("--target", help="Specific output target name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def sync(spec_file: Path, message: str, target: str | None, dry_run: bool):
    """Commit and push in one operation.

    This is the most common workflow: stage all changes, commit, and push.
    Checks if remote is ahead first and aborts with instructions if so.

    Examples:
        clx git sync course.xml -m "Weekly update"
        clx git sync course.xml -m "Fix typos" --target students
        clx git sync course.xml -m "Update" --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = find_output_repos(spec_file, target)

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

        has_remote = repo.has_remote()
        branch = get_current_branch(repo.path)

        # Check if remote is ahead (only if we have a remote)
        if has_remote:
            behind, count = is_behind_remote(repo.path, branch)
            if behind:
                click.echo(
                    f"  Error: Remote 'origin/{branch}' is {count} commit(s) ahead",
                    err=True,
                )
                click.echo("", err=True)
                click.echo("  To resolve:", err=True)
                click.echo(f"      clx git reset {spec_file}", err=True)
                click.echo(f"      clx build {spec_file}", err=True)
                click.echo(f'      clx git sync {spec_file} -m "{message}"', err=True)
                click.echo()
                errors_found = True
                continue

        # Stage all changes
        run_git(repo.path, "add", "-A")

        # Check if there are changes to commit
        if has_uncommitted_changes(repo.path):
            result = run_git(repo.path, "commit", "-m", message)
            if result.returncode == 0:
                click.echo(f"  Committed: {message}")
            else:
                click.echo(f"  Error committing: {result.stderr.strip()}", err=True)
                click.echo()
                errors_found = True
                continue
        else:
            click.echo("  No changes to commit")

        # Push if we have a remote
        if has_remote:
            result = run_git(repo.path, "push", "-u", "origin", branch)
            if result.returncode == 0:
                click.echo(f"  Pushed to origin/{branch}")
            else:
                click.echo(f"  Error pushing: {result.stderr.strip()}", err=True)
                errors_found = True
        else:
            click.echo("  Skipped push: No remote configured")

        click.echo()

    if errors_found:
        raise SystemExit(1)


@git_group.command()
@click.argument("spec-file", type=click.Path(exists=True, path_type=Path))
@click.option("--target", help="Specific output target name")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def reset(spec_file: Path, target: str | None, dry_run: bool):
    """Reset local repos to remote tracking branch.

    Fetches from remote and performs a hard reset to origin/<branch>.
    Use this to recover when remote is ahead of local.

    WARNING: This will discard all local changes!

    After reset, run 'clx build' to regenerate (fast due to cache),
    then 'clx git sync' to commit and push.

    Examples:
        clx git reset course.xml
        clx git reset course.xml --target students
        clx git reset course.xml --dry-run
    """
    _dry_run_mode.set(dry_run)
    if dry_run:
        click.echo("[DRY RUN MODE - No changes will be made]")
        click.echo()

    repos = find_output_repos(spec_file, target)

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
    click.echo(f"  1. clx build {spec_file}")
    click.echo(f'  2. clx git sync {spec_file} -m "<message>"')
