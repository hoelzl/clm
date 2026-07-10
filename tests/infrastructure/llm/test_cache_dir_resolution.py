"""Tests for LLM cache-dir resolution + provenance (:mod:`clm.infrastructure.llm.cache`).

Covers :func:`describe_cache_dir` (pure resolution + provenance) and the
git-worktree anchoring of a relative ``[tool.clm] cache_dir`` — the fix for
sync watermarks "disappearing" when run from a worktree, where a relative
``../shared-cache`` previously resolved under the worktree instead of beside
the main checkout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clm.infrastructure.llm.cache import describe_cache_dir, resolve_cache_dir


@pytest.fixture
def git_available():
    if shutil.which("git") is None:  # pragma: no cover - CI always has git
        pytest.skip("git not available")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path, *, cache_dir_value: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "pyproject.toml").write_text(
        f'[tool.clm]\ncache_dir = "{cache_dir_value}"\n', encoding="utf-8"
    )
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "init", cwd=path)


class TestDescribeCacheDir:
    def test_cli_override_source(self, tmp_path: Path):
        chosen = tmp_path / "explicit"
        res = describe_cache_dir(cli_override=chosen, repo_root=tmp_path)
        assert res.source == "cli"
        assert res.path == chosen
        # describe is pure: it must NOT create the directory
        assert not chosen.exists()

    def test_env_source(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "from-env"
        monkeypatch.setenv("CLM_CACHE_DIR", str(target))
        res = describe_cache_dir(repo_root=tmp_path)
        assert res.source == "env"
        assert res.path == target

    def test_pyproject_relative_source(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.clm]\ncache_dir = "custom-cache"\n', encoding="utf-8"
        )
        res = describe_cache_dir(repo_root=tmp_path)
        assert res.source == "pyproject"
        assert res.configured_value == "custom-cache"
        assert res.path == tmp_path / "custom-cache"
        # explicit repo_root → no worktree detection
        assert res.main_worktree_root is None

    def test_default_source(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        res = describe_cache_dir(repo_root=tmp_path)
        assert res.source == "default"
        assert res.path == tmp_path / ".clm-cache"

    def test_resolve_cache_dir_creates_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        resolved = resolve_cache_dir(repo_root=tmp_path)
        assert resolved == tmp_path / ".clm-cache"
        assert resolved.is_dir()  # wrapper ensures existence


class TestSubdirWalkUp:
    """Issue #477: from a subdir, describe_cache_dir must discover the project
    root (walk up) rather than treating cwd as root.

    A real git repo is used so the worktree-anchoring git lookup is deterministic
    (a main worktree → no re-anchor), independent of any ambient repo the temp
    dir might sit under.
    """

    def test_relative_cache_dir_found_from_subdir(self, tmp_path: Path, monkeypatch, git_available):
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo, cache_dir_value="../shared-cache")
        sub = repo / "slides" / "module_410" / "topic_031"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        # repo_root=None → cwd-based; the walk-up finds repo/pyproject.toml.
        res = describe_cache_dir()
        assert res.source == "pyproject"
        assert res.configured_value == "../shared-cache"
        # Anchored to the discovered root (repo), NOT the subdir → repo's sibling.
        assert res.path.resolve() == (tmp_path / "shared-cache").resolve()

    def test_start_anchor_walks_up_without_chdir(self, tmp_path: Path, monkeypatch, git_available):
        # start= anchors the walk-up at a path argument instead of cwd (the
        # voiceover cache passes the deck directory, issue #568), while
        # keeping repo_root=None so worktree detection stays active.
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        repo = tmp_path / "repo"
        _init_repo(repo, cache_dir_value="../shared-cache")
        sub = repo / "slides" / "module_410" / "topic_031"
        sub.mkdir(parents=True)
        res = describe_cache_dir(start=sub)
        assert res.source == "pyproject"
        assert res.path.resolve() == (tmp_path / "shared-cache").resolve()

    def test_default_anchors_to_root_from_subdir(self, tmp_path: Path, monkeypatch, git_available):
        # No [tool.clm] cache_dir → the default <root>/.clm-cache must anchor to
        # the discovered root, never creating a stray <subdir>/.clm-cache (#477).
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", cwd=repo)
        (repo / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
        sub = repo / "slides" / "topic_031"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        res = describe_cache_dir()
        assert res.source == "default"
        assert res.path.resolve() == (repo / ".clm-cache").resolve()
        assert res.path.resolve() != (sub / ".clm-cache").resolve()


class TestWorktreeAnchoring:
    def test_relative_cache_dir_anchors_to_main_root_from_worktree(
        self, tmp_path: Path, monkeypatch, git_available
    ):
        # Main repo at tmp_path/repo with a relative cache_dir pointing at a
        # sibling of the MAIN checkout.
        repo = tmp_path / "repo"
        _init_repo(repo, cache_dir_value="../shared")
        # Linked worktree nested at a DIFFERENT depth so a worktree-anchored
        # resolution (the bug) would land somewhere other than the main one.
        wt = tmp_path / "wts" / "feature"
        _git("worktree", "add", str(wt), "-b", "feature", cwd=repo)

        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        monkeypatch.chdir(wt)
        res = describe_cache_dir()  # repo_root=None → cwd-based, worktree-aware

        assert res.source == "pyproject"
        assert res.main_worktree_root is not None
        assert res.main_worktree_root.resolve() == repo.resolve()
        # Anchored to the MAIN repo root → tmp_path/shared, NOT tmp_path/wts/shared.
        assert res.path.resolve() == (tmp_path / "shared").resolve()

    def test_main_worktree_anchors_to_its_own_root(
        self, tmp_path: Path, monkeypatch, git_available
    ):
        repo = tmp_path / "repo"
        _init_repo(repo, cache_dir_value="../shared")
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        monkeypatch.chdir(repo)
        res = describe_cache_dir()
        # The main worktree has no separate main root to re-anchor to.
        assert res.main_worktree_root is None
        assert res.path.resolve() == (tmp_path / "shared").resolve()

    def test_explicit_repo_root_opts_out_of_detection(
        self, tmp_path: Path, monkeypatch, git_available
    ):
        repo = tmp_path / "repo"
        _init_repo(repo, cache_dir_value="../shared")
        wt = tmp_path / "wts" / "feature"
        _git("worktree", "add", str(wt), "-b", "feature", cwd=repo)
        monkeypatch.delenv("CLM_CACHE_DIR", raising=False)
        # Passing repo_root explicitly anchors to it verbatim (no git lookup),
        # preserving the documented contract for callers that know their root.
        res = describe_cache_dir(repo_root=wt)
        assert res.main_worktree_root is None
        assert res.path.resolve() == (wt.parent / "shared").resolve()
