"""Tests for core git provenance capture (issue #208, step 1)."""

import shutil
import subprocess

import pytest

from clm.core.git_info import get_git_info


def test_get_git_info_outside_repo_returns_none(tmp_path):
    # A bare temp directory is not a git work tree, so both values are None
    # and capture never raises.
    info = get_git_info(tmp_path)
    assert info == {"commit": None, "dirty": None}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_get_git_info_reports_commit_and_dirty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "init")

    clean = get_git_info(repo)
    assert clean["commit"] is not None
    assert len(clean["commit"]) >= 7
    assert clean["dirty"] is False

    # An uncommitted change flips dirty without changing the commit.
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    dirty = get_git_info(repo)
    assert dirty["commit"] == clean["commit"]
    assert dirty["dirty"] is True
