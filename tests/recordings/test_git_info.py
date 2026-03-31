"""Tests for git info capture."""

from __future__ import annotations

from pathlib import Path

from clm.recordings.git_info import get_git_info


class TestGetGitInfo:
    def test_returns_commit_for_git_repo(self, tmp_path: Path):
        """Test against a real git repo (this project itself)."""
        # Use the CLM project directory as a known git repo
        clm_root = Path(__file__).resolve().parents[2]
        if not (clm_root / ".git").exists():
            import pytest

            pytest.skip("Not running inside a git repo")

        info = get_git_info(clm_root)
        assert info["commit"] is not None
        assert len(info["commit"]) == 40  # full SHA
        assert isinstance(info["dirty"], bool)

    def test_returns_none_for_non_git_dir(self, tmp_path: Path):
        info = get_git_info(tmp_path)
        assert info["commit"] is None
        assert info["dirty"] is None
