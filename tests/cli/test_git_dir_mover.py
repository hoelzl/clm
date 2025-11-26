"""Tests for git directory mover utility.

Tests the GitDirMover context manager that temporarily moves .git directories.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clx.cli.git_dir_mover import GitDirMover, git_dir_mover


class TestGitDirMover:
    """Test GitDirMover class."""

    def test_init_with_directories(self, tmp_path):
        """Should initialize with a list of directories."""
        mover = GitDirMover([tmp_path])
        assert len(mover.directories) == 1
        assert mover.directories[0] == tmp_path

    def test_init_converts_to_paths(self, tmp_path):
        """Should convert string paths to Path objects."""
        mover = GitDirMover([str(tmp_path)])
        assert isinstance(mover.directories[0], Path)

    def test_init_keep_directory_default(self, tmp_path):
        """Should default keep_directory to False."""
        mover = GitDirMover([tmp_path])
        assert mover.keep_directory is False

    def test_init_keep_directory_true(self, tmp_path):
        """Should accept keep_directory=True."""
        mover = GitDirMover([tmp_path], keep_directory=True)
        assert mover.keep_directory is True


class TestGitDirMoverContextManager:
    """Test GitDirMover as context manager."""

    @pytest.fixture
    def git_dir_structure(self, tmp_path):
        """Create a directory structure with .git directories."""
        # Create main project with .git
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        git_dir = project_dir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("test config")
        (git_dir / "HEAD").write_text("ref: refs/heads/main")

        # Create a submodule with its own .git
        submodule_dir = project_dir / "submodule"
        submodule_dir.mkdir()
        submodule_git = submodule_dir / ".git"
        submodule_git.mkdir()
        (submodule_git / "config").write_text("submodule config")

        return project_dir

    def test_moves_git_directories(self, git_dir_structure):
        """Should move .git directories temporarily."""
        project_dir = git_dir_structure
        git_dir = project_dir / ".git"

        assert git_dir.exists()

        with GitDirMover([project_dir]) as mover:
            # .git should be moved
            assert not git_dir.exists()
            # Mover should track moved directories
            assert len(mover.moved_dirs) >= 1

    def test_restores_git_directories(self, git_dir_structure):
        """Should restore .git directories after context exits."""
        project_dir = git_dir_structure
        git_dir = project_dir / ".git"

        with GitDirMover([project_dir]):
            pass

        # .git should be restored
        assert git_dir.exists()
        assert (git_dir / "config").read_text() == "test config"

    def test_restores_nested_git_directories(self, git_dir_structure):
        """Should restore nested .git directories."""
        project_dir = git_dir_structure
        submodule_git = project_dir / "submodule" / ".git"

        with GitDirMover([project_dir]):
            assert not submodule_git.exists()

        assert submodule_git.exists()
        assert (submodule_git / "config").read_text() == "submodule config"

    def test_keep_directory_no_move(self, git_dir_structure):
        """Should not move directories when keep_directory=True."""
        project_dir = git_dir_structure
        git_dir = project_dir / ".git"

        with GitDirMover([project_dir], keep_directory=True) as mover:
            # .git should still exist
            assert git_dir.exists()
            # No directories should be tracked
            assert len(mover.moved_dirs) == 0

    def test_no_git_directories(self, tmp_path):
        """Should handle directories without .git gracefully."""
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        (plain_dir / "file.txt").write_text("content")

        with GitDirMover([plain_dir]) as mover:
            assert len(mover.moved_dirs) == 0

        assert (plain_dir / "file.txt").read_text() == "content"

    def test_multiple_directories(self, tmp_path):
        """Should handle multiple directories."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        for d in [dir1, dir2]:
            d.mkdir()
            git_dir = d / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(f"config for {d.name}")

        with GitDirMover([dir1, dir2]) as mover:
            assert not (dir1 / ".git").exists()
            assert not (dir2 / ".git").exists()
            assert len(mover.moved_dirs) == 2

        assert (dir1 / ".git").exists()
        assert (dir2 / ".git").exists()

    def test_cleans_temp_directory(self, git_dir_structure):
        """Should clean up temp directory after context exits."""
        project_dir = git_dir_structure
        temp_dir = None

        with GitDirMover([project_dir]) as mover:
            temp_dir = mover.temp_dir

        # Temp directory should be cleaned up
        assert temp_dir is not None
        assert not Path(temp_dir).exists()

    def test_restore_failure_raises_exception(self, git_dir_structure):
        """Should raise exception if restore fails."""
        project_dir = git_dir_structure
        git_dir = project_dir / ".git"
        submodule_git = project_dir / "submodule" / ".git"

        with patch("clx.cli.git_dir_mover.shutil.move") as mock_move:
            # First two calls succeed (move both .git dirs to temp)
            # Next two calls fail (restore both .git dirs from temp)
            mock_move.side_effect = [
                None,
                None,
                OSError("Permission denied"),
                OSError("Permission denied"),
            ]

            with pytest.raises(RuntimeError) as excinfo:
                with GitDirMover([project_dir]):
                    pass

            assert "Failed to restore" in str(excinfo.value)


class TestGitDirMoverFunction:
    """Test git_dir_mover convenience function."""

    def test_returns_mover(self, tmp_path):
        """Should return the GitDirMover instance."""
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        with git_dir_mover([plain_dir]) as mover:
            assert isinstance(mover, GitDirMover)

    def test_accepts_keep_directory(self, tmp_path):
        """Should pass keep_directory parameter."""
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()

        with git_dir_mover([plain_dir], keep_directory=True) as mover:
            assert mover.keep_directory is True
