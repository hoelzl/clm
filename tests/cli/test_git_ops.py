"""Unit tests for git operations commands."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.cli.commands.git_ops import (
    OutputRepo,
    find_output_repos,
    get_current_branch,
    has_uncommitted_changes,
    is_behind_remote,
    remote_exists,
    remote_has_commits,
    run_git,
    run_git_global,
)
from clx.core.course_spec import GitHubSpec


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
        with patch("clx.cli.commands.git_ops.subprocess.run") as mock_run:
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
        with patch("clx.cli.commands.git_ops.subprocess.run") as mock_run:
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
        with patch("clx.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert remote_exists("https://github.com/org/repo") is True

    def test_remote_exists_false(self):
        """Test remote_exists returns False for non-existing remote."""
        with patch("clx.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert remote_exists("https://github.com/org/nonexistent") is False

    def test_remote_has_commits_true(self):
        """Test remote_has_commits returns True when remote has commits."""
        with patch("clx.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\trefs/heads/main\n")
            assert remote_has_commits("https://github.com/org/repo") is True

    def test_remote_has_commits_false_empty(self):
        """Test remote_has_commits returns False when remote is empty."""
        with patch("clx.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            assert remote_has_commits("https://github.com/org/repo") is False

    def test_remote_has_commits_false_error(self):
        """Test remote_has_commits returns False on error."""
        with patch("clx.cli.commands.git_ops.run_git_global") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert remote_has_commits("https://github.com/org/repo") is False

    def test_has_uncommitted_changes_true(self, tmp_path: Path):
        """Test has_uncommitted_changes returns True when changes exist."""
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M file.txt\n")
            assert has_uncommitted_changes(tmp_path) is True

    def test_has_uncommitted_changes_false(self, tmp_path: Path):
        """Test has_uncommitted_changes returns False when clean."""
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            assert has_uncommitted_changes(tmp_path) is False

    def test_get_current_branch(self, tmp_path: Path):
        """Test get_current_branch returns branch name."""
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
            assert get_current_branch(tmp_path) == "main"

    def test_get_current_branch_default_on_error(self, tmp_path: Path):
        """Test get_current_branch returns 'master' on error."""
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert get_current_branch(tmp_path) == "master"

    def test_is_behind_remote(self, tmp_path: Path):
        """Test is_behind_remote detection."""
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
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
        with patch("clx.cli.commands.git_ops.run_git") as mock_run:
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
