"""Unit tests for git operations commands."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.cli.commands.git_ops import (
    OutputRepo,
    _dry_run_mode,
    find_output_repos,
    get_current_branch,
    has_uncommitted_changes,
    is_behind_remote,
    remote_exists,
    remote_has_commits,
    run_git,
    run_git_global,
)
from clm.core.course_spec import GitHubSpec


class TestGitHubSpec:
    """Tests for GitHubSpec class."""

    def test_empty_spec(self):
        """Test creating an empty GitHubSpec."""
        spec = GitHubSpec()
        assert spec.project_slug is None
        assert spec.repository_base is None
        assert spec.include_speaker is False
        assert not spec.is_configured

    def test_configured_spec(self):
        """Test creating a configured GitHubSpec."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            include_speaker=True,
        )
        assert spec.is_configured
        assert spec.include_speaker

    def test_derive_remote_url_not_configured(self):
        """Test URL derivation when not configured."""
        spec = GitHubSpec()
        assert spec.derive_remote_url("public", "de") is None

    def test_derive_remote_url_public(self):
        """Test URL derivation for public target."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
        )
        url = spec.derive_remote_url("public", "de")
        assert url == "https://github.com/Org/ml-course-de"

    def test_derive_remote_url_default(self):
        """Test URL derivation for default target."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
        )
        url = spec.derive_remote_url("default", "en")
        assert url == "https://github.com/Org/ml-course-en"

    def test_derive_remote_url_first_explicit_target(self):
        """Test URL derivation for first explicit target (no suffix)."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
        )
        url = spec.derive_remote_url("code-along", "de", is_first_target=True)
        assert url == "https://github.com/Org/ml-course-de"

    def test_derive_remote_url_other_explicit_target(self):
        """Test URL derivation for non-first explicit target."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
        )
        url = spec.derive_remote_url("completed", "de", is_first_target=False)
        assert url == "https://github.com/Org/ml-course-de-completed"

    def test_derive_remote_url_speaker_not_enabled(self):
        """Test URL derivation for speaker when not enabled."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            include_speaker=False,
        )
        url = spec.derive_remote_url("speaker", "de")
        assert url is None

    def test_derive_remote_url_speaker_enabled(self):
        """Test URL derivation for speaker when enabled."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            include_speaker=True,
        )
        url = spec.derive_remote_url("speaker", "de")
        assert url == "https://github.com/Org/ml-course-de-speaker"


class TestGitHubSpecRemoteTemplate:
    """Tests for GitHubSpec remote_template support."""

    def test_default_template_matches_original_behavior(self):
        """Without a template, URLs are derived the same as before."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
        )
        url = spec.derive_remote_url("public", "de")
        assert url == "https://github.com/Org/ml-course-de"

    def test_ssh_template_on_instance(self):
        """Template set on the GitHubSpec instance is used."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            remote_template="git@github.com-cam:Coding-Academy-Munich/{repo}.git",
        )
        url = spec.derive_remote_url("public", "de")
        assert url == "git@github.com-cam:Coding-Academy-Munich/ml-course-de.git"

    def test_ssh_template_with_suffix(self):
        """Template works correctly with target suffixes."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            remote_template="git@github.com-cam:Coding-Academy-Munich/{repo}.git",
        )
        url = spec.derive_remote_url("completed", "de")
        assert url == "git@github.com-cam:Coding-Academy-Munich/ml-course-de-completed.git"

    def test_template_parameter_overrides_instance(self):
        """The remote_template parameter takes precedence over instance attribute."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            remote_template="git@instance-host:{repo}.git",
        )
        url = spec.derive_remote_url(
            "public",
            "de",
            remote_template="git@param-host:MyOrg/{repo}.git",
        )
        assert url == "git@param-host:MyOrg/ml-course-de.git"

    def test_template_with_repository_base_placeholder(self):
        """Template can use {repository_base} placeholder."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            remote_template="{repository_base}/{repo}.git",
        )
        url = spec.derive_remote_url("public", "en")
        assert url == "https://github.com/Org/ml-course-en.git"

    def test_template_with_all_placeholders(self):
        """All placeholders are available in the template."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            remote_template="{repository_base}/{slug}-{lang}{suffix}",
        )
        url = spec.derive_remote_url("completed", "de")
        # This is equivalent to the default pattern
        assert url == "https://github.com/Org/ml-course-de-completed"

    def test_template_speaker_target_with_include_speaker(self):
        """Template works with speaker targets when enabled."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            include_speaker=True,
            remote_template="git@github.com:Org/{repo}.git",
        )
        url = spec.derive_remote_url("speaker", "de")
        assert url == "git@github.com:Org/ml-course-de-speaker.git"

    def test_template_speaker_target_without_include_speaker(self):
        """Speaker targets still return None when not enabled, regardless of template."""
        spec = GitHubSpec(
            project_slug="ml-course",
            repository_base="https://github.com/Org",
            include_speaker=False,
            remote_template="git@github.com:Org/{repo}.git",
        )
        url = spec.derive_remote_url("speaker", "de")
        assert url is None

    def test_template_not_configured_returns_none(self):
        """Template doesn't help when slug/base are missing."""
        spec = GitHubSpec(
            remote_template="git@github.com:Org/{repo}.git",
        )
        url = spec.derive_remote_url("public", "de")
        assert url is None

    def test_from_element_parses_remote_template(self):
        """XML parsing picks up the <remote-template> element."""
        from xml.etree import ElementTree as ETree

        xml = """<github>
            <repository-base>https://github.com/Org</repository-base>
            <remote-template>git@github.com-cam:Org/{repo}.git</remote-template>
        </github>"""
        element = ETree.fromstring(xml)
        spec = GitHubSpec.from_element(element)
        assert spec.remote_template == "git@github.com-cam:Org/{repo}.git"

    def test_from_element_without_remote_template(self):
        """XML parsing defaults to empty string when no <remote-template>."""
        from xml.etree import ElementTree as ETree

        xml = """<github>
            <repository-base>https://github.com/Org</repository-base>
        </github>"""
        element = ETree.fromstring(xml)
        spec = GitHubSpec.from_element(element)
        assert spec.remote_template == ""


class TestOutputRepo:
    """Tests for OutputRepo class."""

    def test_output_repo_properties(self, tmp_path: Path):
        """Test OutputRepo basic properties."""
        repo = OutputRepo(
            path=tmp_path,
            target_name="students",
            language="de",
            remote_url="https://github.com/Org/repo-de",
        )
        assert repo.path == tmp_path
        assert repo.target_name == "students"
        assert repo.language == "de"
        assert repo.remote_url == "https://github.com/Org/repo-de"
        assert repo.display_name == "students/de"
        assert repo.git_dir == tmp_path / ".git"

    def test_has_git_false(self, tmp_path: Path):
        """Test has_git when no .git directory."""
        repo = OutputRepo(path=tmp_path, target_name="test", language="de")
        assert not repo.has_git

    def test_has_git_true(self, tmp_path: Path):
        """Test has_git when .git directory exists."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        repo = OutputRepo(path=tmp_path, target_name="test", language="de")
        assert repo.has_git


class TestGitHelpers:
    """Tests for git helper functions."""

    def test_run_git(self, tmp_path: Path):
        """Test run_git executes correctly."""
        with patch("clm.cli.commands.git_ops.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "-C", str(tmp_path), "status"],
                returncode=0,
                stdout="",
                stderr="",
            )
            result = run_git(tmp_path, "status")
            assert result.returncode == 0
            mock_run.assert_called_once()

    def test_run_git_global(self):
        """Test run_git_global executes correctly."""
        with patch("clm.cli.commands.git_ops.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "version"],
                returncode=0,
                stdout="git version 2.40.0",
                stderr="",
            )
            result = run_git_global("version")
            assert result.returncode == 0
            mock_run.assert_called_once()

    def test_remote_exists_true(self):
        """Test remote_exists returns True for existing remote."""
        with patch("clm.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert remote_exists("https://github.com/org/repo") is True

    def test_remote_exists_false(self):
        """Test remote_exists returns False for non-existing remote."""
        with patch("clm.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert remote_exists("https://github.com/org/nonexistent") is False

    def test_remote_has_commits_true(self):
        """Test remote_has_commits returns True when remote has commits."""
        with patch("clm.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\trefs/heads/main\n")
            assert remote_has_commits("https://github.com/org/repo") is True

    def test_remote_has_commits_false_empty(self):
        """Test remote_has_commits returns False when remote is empty."""
        with patch("clm.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert remote_has_commits("https://github.com/org/repo") is False

    def test_remote_has_commits_false_error(self):
        """Test remote_has_commits returns False on error."""
        with patch("clm.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert remote_has_commits("https://github.com/org/repo") is False

    def test_has_uncommitted_changes_true(self, tmp_path: Path):
        """Test has_uncommitted_changes returns True when changes exist."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M file.txt\n")
            assert has_uncommitted_changes(tmp_path) is True

    def test_has_uncommitted_changes_false(self, tmp_path: Path):
        """Test has_uncommitted_changes returns False when clean."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            assert has_uncommitted_changes(tmp_path) is False

    def test_get_current_branch(self, tmp_path: Path):
        """Test get_current_branch returns branch name."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
            assert get_current_branch(tmp_path) == "main"

    def test_get_current_branch_default_on_error(self, tmp_path: Path):
        """Test get_current_branch returns 'master' on error."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert get_current_branch(tmp_path) == "master"

    def test_is_behind_remote(self, tmp_path: Path):
        """Test is_behind_remote detection."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            # First call: fetch, second call: rev-list
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=0, stdout="3\n"),  # rev-list
            ]
            behind, count = is_behind_remote(tmp_path, "main")
            assert behind is True
            assert count == 3

    def test_is_behind_remote_not_behind(self, tmp_path: Path):
        """Test is_behind_remote when not behind."""
        with patch("clm.cli.commands.git_ops.run_git") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=0, stdout="0\n"),  # rev-list
            ]
            behind, count = is_behind_remote(tmp_path, "main")
            assert behind is False
            assert count == 0


class TestFindOutputRepos:
    """Tests for find_output_repos function."""

    def test_resolves_paths_relative_to_course_root_not_spec_parent(self, tmp_path: Path):
        """Output paths should be relative to course root, not spec file parent.

        This test ensures the bug fix is working: when spec file is in a
        subdirectory (e.g., course-specs/), output paths should be relative
        to the grandparent (course root), not the spec file's parent.
        """
        # Create course structure mimicking real usage
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        # Create minimal spec file without explicit output targets
        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Test Kurs</de>
        <en>Test Course</en>
    </name>
    <prog-lang>Python</prog-lang>
</course>"""
        )

        repos = find_output_repos(spec_file)

        # Should look in tmp_path/output, NOT tmp_path/course-specs/output
        for repo in repos:
            path_str = str(repo.path)
            assert "course-specs" not in path_str, (
                f"Path incorrectly includes 'course-specs': {path_str}"
            )
            assert str(tmp_path / "output") in path_str, (
                f"Path should be relative to course root: {path_str}"
            )

    def test_finds_default_output_structure(self, tmp_path: Path):
        """Test that default output structure (public/speaker) is found."""
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Test</de>
        <en>Test</en>
    </name>
    <prog-lang>Python</prog-lang>
</course>"""
        )

        repos = find_output_repos(spec_file)

        # Should find public/De, public/En, speaker/De, speaker/En
        # (but speaker only if include_speaker is True, which defaults to False)
        expected_output = tmp_path / "output"
        target_names = {repo.target_name for repo in repos}
        assert "public" in target_names

        # Verify paths are correct
        for repo in repos:
            assert expected_output in repo.path.parents or repo.path.parent == expected_output

    def test_finds_explicit_output_targets(self, tmp_path: Path):
        """Test that explicit output targets from spec are found."""
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Test</de>
        <en>Test</en>
    </name>
    <prog-lang>Python</prog-lang>
    <output-targets>
        <output-target name="students">
            <path>./output/students</path>
            <languages><language>de</language><language>en</language></languages>
        </output-target>
    </output-targets>
</course>"""
        )

        repos = find_output_repos(spec_file)

        # Should find students/de, students/en
        assert len(repos) >= 2
        for repo in repos:
            assert repo.target_name == "students"
            # Path should be tmp_path/output/students/De or En, NOT course-specs/output/...
            assert "course-specs" not in str(repo.path)
            expected_base = tmp_path / "output" / "students"
            assert expected_base in repo.path.parents or str(expected_base) in str(repo.path)

    def test_output_paths_include_dir_name_for_default_targets(self, tmp_path: Path):
        """Output paths should include the course dir name, not a language directory.

        The git repo should be at output/public/Mein Kurs-de, not output/public/De/Mein Kurs.
        """
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Mein Kurs</de>
        <en>My Course</en>
    </name>
    <prog-lang>Python</prog-lang>
</course>"""
        )

        repos = find_output_repos(spec_file)

        # Path should use output_dir_name (fallback: sanitized name + lang suffix)
        for repo in repos:
            path_parts = repo.path.parts
            # No separate De/En language directory
            assert "De" not in path_parts
            assert "En" not in path_parts
            # Last part should be the dir name with language suffix
            assert path_parts[-1].endswith(f"-{repo.language}"), (
                f"Path should end with language-suffixed dir name: {repo.path}"
            )

    def test_output_paths_include_dir_name_for_explicit_targets(self, tmp_path: Path):
        """Output paths for explicit targets should include the dir name.

        When using explicit output-targets, the path should include
        the output_dir_name subdirectory without a separate language directory.
        """
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Mein Kurs</de>
        <en>My Course</en>
    </name>
    <prog-lang>Python</prog-lang>
    <output-targets>
        <output-target name="students">
            <path>./output/students</path>
            <languages><language>de</language><language>en</language></languages>
        </output-target>
    </output-targets>
</course>"""
        )

        repos = find_output_repos(spec_file)

        for repo in repos:
            path_parts = repo.path.parts
            # No separate De/En language directory
            assert "De" not in path_parts
            assert "En" not in path_parts
            # Last part should be the dir name with language suffix
            assert path_parts[-1].endswith(f"-{repo.language}"), (
                f"Path should end with language-suffixed dir name: {repo.path}"
            )


class TestDryRunMode:
    """Tests for dry-run mode functionality."""

    def test_run_git_in_dry_run_mode_does_not_execute(self, tmp_path: Path):
        """In dry-run mode, run_git should not execute the command."""
        _dry_run_mode.set(True)
        try:
            # This would fail if actually executed (no git repo)
            result = run_git(tmp_path, "status")

            # Should return a mock result
            assert result.returncode == 0
            assert result.stdout == ""
            assert result.stderr == ""
        finally:
            _dry_run_mode.set(False)

    def test_run_git_global_in_dry_run_mode_does_not_execute(self):
        """In dry-run mode, run_git_global should not execute the command."""
        _dry_run_mode.set(True)
        try:
            # This would actually work, but we want to test it doesn't execute
            result = run_git_global("--version")

            # Should return a mock result (empty, not the actual version)
            assert result.returncode == 0
            assert result.stdout == ""
        finally:
            _dry_run_mode.set(False)

    def test_run_git_executes_normally_when_not_dry_run(self, tmp_path: Path):
        """When dry-run is False, run_git should execute normally."""
        _dry_run_mode.set(False)

        # Running git version should work globally
        result = run_git_global("--version")
        assert result.returncode == 0
        assert "git version" in result.stdout

    def test_dry_run_mode_is_off_by_default(self):
        """Dry-run mode should be off by default."""
        # Reset to default
        _dry_run_mode.set(False)
        assert _dry_run_mode.get() is False

    def test_dry_run_output_quotes_paths_with_spaces(self, tmp_path: Path, capsys):
        """Dry-run output should properly quote paths containing spaces."""
        # Create a path with spaces
        path_with_spaces = tmp_path / "My Course" / "output dir"
        path_with_spaces.mkdir(parents=True)

        _dry_run_mode.set(True)
        try:
            run_git(path_with_spaces, "status")
            captured = capsys.readouterr()

            # The path should be quoted in the output
            # shlex.join quotes strings with spaces
            assert "My Course" in captured.out or "'My Course'" in captured.out
            # Either the path is quoted or the whole thing is properly escaped
            assert "Would run:" in captured.out
        finally:
            _dry_run_mode.set(False)

    def test_paths_with_spaces_in_actual_execution(self, tmp_path: Path):
        """Actual git execution should handle paths with spaces correctly.

        This test creates a git repo in a path with spaces and verifies
        commands work correctly.
        """
        # Create a path with spaces
        path_with_spaces = tmp_path / "My Course Output"
        path_with_spaces.mkdir(parents=True)

        _dry_run_mode.set(False)

        # Initialize a git repo in the path with spaces
        result = run_git(path_with_spaces, "init")
        assert result.returncode == 0, f"Git init failed: {result.stderr}"

        # Verify we can run status in the repo
        result = run_git(path_with_spaces, "status")
        assert result.returncode == 0, f"Git status failed: {result.stderr}"

        # Verify .git directory was created in the correct location
        assert (path_with_spaces / ".git").is_dir()


class TestPathsWithSpaces:
    """Tests for handling paths with special characters."""

    def test_find_output_repos_with_course_name_containing_spaces(self, tmp_path: Path):
        """Course names with spaces should produce valid paths (via fallback dir name)."""
        course_specs = tmp_path / "course-specs"
        course_specs.mkdir()
        spec_file = course_specs / "test.xml"

        # Course name with spaces
        spec_file.write_text(
            """<?xml version="1.0"?>
<course>
    <name>
        <de>Mein toller Kurs mit Leerzeichen</de>
        <en>My Great Course With Spaces</en>
    </name>
    <prog-lang>Python</prog-lang>
</course>"""
        )

        repos = find_output_repos(spec_file)

        # Verify paths include the fallback dir name (sanitized name + lang suffix)
        for repo in repos:
            path_str = str(repo.path)
            if repo.language == "de":
                assert "Mein toller Kurs mit Leerzeichen-de" in path_str
            else:
                assert "My Great Course With Spaces-en" in path_str

    def test_git_init_with_paths_containing_spaces(self, tmp_path: Path):
        """Git init should work correctly with paths containing spaces."""
        # Create directory structure with spaces
        output_dir = tmp_path / "output" / "public" / "De" / "My Test Course"
        output_dir.mkdir(parents=True)

        # Create a test file
        (output_dir / "test.txt").write_text("test content")

        _dry_run_mode.set(False)

        # Initialize git repo
        result = run_git(output_dir, "init")
        assert result.returncode == 0, f"Init failed: {result.stderr}"

        # Configure git identity (CI runners may not have this set)
        result = run_git(output_dir, "config", "user.email", "test@example.com")
        assert result.returncode == 0, f"Config email failed: {result.stderr}"
        result = run_git(output_dir, "config", "user.name", "Test User")
        assert result.returncode == 0, f"Config name failed: {result.stderr}"

        # Add and commit
        result = run_git(output_dir, "add", "-A")
        assert result.returncode == 0, f"Add failed: {result.stderr}"

        result = run_git(output_dir, "commit", "-m", "Initial commit")
        assert result.returncode == 0, f"Commit failed: {result.stderr}"

        # Verify repo was created in correct location
        assert (output_dir / ".git").is_dir()


@pytest.fixture()
def spec_file(tmp_path: Path) -> Path:
    """Create a minimal spec file for CLI tests."""
    f = tmp_path / "spec.xml"
    f.write_text(
        """<?xml version="1.0"?>
<course>
    <name><de>Test</de><en>Test</en></name>
    <prog-lang>Python</prog-lang>
</course>"""
    )
    return f


@pytest.fixture()
def mock_repo(tmp_path: Path) -> OutputRepo:
    """Create a mock output repo with .git dir."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    return OutputRepo(
        path=repo_path,
        target_name="test",
        language="de",
        remote_url="https://github.com/Org/repo-de",
    )


class TestCommitAmend:
    """Tests for commit --amend flag."""

    def test_commit_without_message_or_amend_raises_error(self, spec_file: Path):
        """commit without -m or --amend should produce a UsageError."""
        from click.testing import CliRunner

        from clm.cli.commands.git_ops import git_group

        runner = CliRunner()
        result = runner.invoke(git_group, ["commit", str(spec_file)])
        assert result.exit_code != 0
        assert "Either -m/--message or --amend must be provided" in result.output

    def test_commit_amend_no_edit(self, spec_file: Path, mock_repo: OutputRepo):
        """commit --amend without -m should use --no-edit."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with patch("clm.cli.commands.git_ops.run_git") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                runner.invoke(git_group, ["commit", str(spec_file), "--amend"])

                commit_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "commit" in c[0][1:]
                ]
                assert len(commit_calls) == 1
                args = commit_calls[0][0]
                assert "--amend" in args[1:]
                assert "--no-edit" in args[1:]

    def test_commit_amend_with_message(self, spec_file: Path, mock_repo: OutputRepo):
        """commit --amend -m 'msg' should use --amend -m."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with patch("clm.cli.commands.git_ops.run_git") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                runner.invoke(
                    git_group,
                    ["commit", str(spec_file), "--amend", "-m", "new msg"],
                )

                commit_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "commit" in c[0][1:]
                ]
                assert len(commit_calls) == 1
                args = commit_calls[0][0]
                assert "--amend" in args[1:]
                assert "-m" in args[1:]
                assert "new msg" in args[1:]

    def test_commit_amend_output_message(self, spec_file: Path, mock_repo: OutputRepo):
        """commit --amend should show 'Amended commit' in output."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with patch("clm.cli.commands.git_ops.run_git") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                result = runner.invoke(git_group, ["commit", str(spec_file), "--amend"])

                assert "Amended commit" in result.output


class TestPushForceWithLease:
    """Tests for push --force-with-lease flag."""

    def test_push_force_with_lease(self, spec_file: Path, mock_repo: OutputRepo):
        """push --force-with-lease should pass --force-with-lease to git."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                runner.invoke(
                    git_group,
                    ["push", str(spec_file), "--force-with-lease"],
                )

                push_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "push" in c[0][1:]
                ]
                assert len(push_calls) == 1
                args = push_calls[0][0]
                assert "--force-with-lease" in args[1:]

    def test_push_force_with_lease_output(self, spec_file: Path, mock_repo: OutputRepo):
        """push --force-with-lease should show 'Force-pushed' in output."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                result = runner.invoke(
                    git_group,
                    ["push", str(spec_file), "--force-with-lease"],
                )

                assert "Force-pushed to origin/" in result.output

    def test_push_normal_output(self, spec_file: Path, mock_repo: OutputRepo):
        """push without --force-with-lease should show 'Pushed' in output."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                result = runner.invoke(git_group, ["push", str(spec_file)])

                assert "Pushed to origin/" in result.output
                assert "Force-pushed" not in result.output


class TestSyncAmendAndForce:
    """Tests for sync --amend and --force-with-lease flags."""

    def test_sync_without_message_or_amend_raises_error(self, spec_file: Path):
        """sync without -m or --amend should produce a UsageError."""
        from click.testing import CliRunner

        from clm.cli.commands.git_ops import git_group

        runner = CliRunner()
        result = runner.invoke(git_group, ["sync", str(spec_file)])
        assert result.exit_code != 0
        assert "Either -m/--message or --amend must be provided" in result.output

    def test_sync_amend_skips_behind_check(self, spec_file: Path, mock_repo: OutputRepo):
        """sync --amend should skip the is_behind_remote check."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch("clm.cli.commands.git_ops.is_behind_remote") as mock_behind,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                runner.invoke(git_group, ["sync", str(spec_file), "--amend"])

                mock_behind.assert_not_called()

    def test_sync_amend_force_pushes(self, spec_file: Path, mock_repo: OutputRepo):
        """sync --amend should use --force-with-lease on push."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                result = runner.invoke(git_group, ["sync", str(spec_file), "--amend"])

                push_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "push" in c[0][1:]
                ]
                assert len(push_calls) == 1
                args = push_calls[0][0]
                assert "--force-with-lease" in args[1:]
                assert "Force-pushed to origin/" in result.output

    def test_sync_force_with_lease_without_amend(self, spec_file: Path, mock_repo: OutputRepo):
        """sync --force-with-lease -m 'msg' should force push without amending."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with (
                patch("clm.cli.commands.git_ops.run_git") as mock_run,
                patch(
                    "clm.cli.commands.git_ops.has_uncommitted_changes",
                    return_value=True,
                ),
                patch("clm.cli.commands.git_ops.is_behind_remote") as mock_behind,
                patch.object(OutputRepo, "has_remote", return_value=True),
            ):
                mock_run.return_value = MagicMock(returncode=0, stdout="master\n")

                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                runner.invoke(
                    git_group,
                    [
                        "sync",
                        str(spec_file),
                        "--force-with-lease",
                        "-m",
                        "update",
                    ],
                )

                mock_behind.assert_not_called()

                commit_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "commit" in c[0][1:]
                ]
                assert len(commit_calls) == 1
                args = commit_calls[0][0]
                assert "--amend" not in args[1:]

                push_calls = [
                    c for c in mock_run.call_args_list if len(c[0]) > 1 and "push" in c[0][1:]
                ]
                assert len(push_calls) == 1
                args = push_calls[0][0]
                assert "--force-with-lease" in args[1:]

    def test_sync_dry_run_amend(self, spec_file: Path, mock_repo: OutputRepo):
        """sync --amend --dry-run should show dry-run output."""
        with patch("clm.cli.commands.git_ops.find_output_repos") as mock_find:
            mock_find.return_value = [mock_repo]

            with patch.object(OutputRepo, "has_remote", return_value=True):
                from click.testing import CliRunner

                from clm.cli.commands.git_ops import git_group

                runner = CliRunner()
                result = runner.invoke(
                    git_group,
                    ["sync", str(spec_file), "--amend", "--dry-run"],
                )

                assert "DRY RUN MODE" in result.output
                assert "--amend" in result.output
                assert "--no-edit" in result.output
                assert "--force-with-lease" in result.output
