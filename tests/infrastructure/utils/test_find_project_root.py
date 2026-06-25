"""Tests for :func:`clm.infrastructure.utils.path_utils.find_project_root` (issue #477).

The walk-up makes config / cache / DB discovery behave identically from any
subdirectory of a project, the way ``git`` / ``uv`` / ``ruff`` do.
"""

from __future__ import annotations

from pathlib import Path

from clm.infrastructure.utils.path_utils import find_project_root


def _has_marker_ancestor(path: Path) -> bool:
    """Whether any ancestor of ``path`` already carries a project-root marker.

    Used to skip the "no marker → fall back to start" assertion when the test's
    temp dir happens to live under a marked tree (so the test stays deterministic
    regardless of where the OS puts temp files).
    """
    for directory in (path, *path.parents):
        if (directory / "pyproject.toml").is_file():
            return True
        if (directory / ".clm" / "config.toml").is_file():
            return True
        if (directory / "clm.toml").is_file():
            return True
        if (directory / ".git").exists():
            return True
    return False


def test_returns_root_with_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    assert find_project_root(tmp_path) == tmp_path.resolve()


def test_walks_up_from_nested_subdir(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    sub = tmp_path / "slides" / "module_410" / "topic_031"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


def test_clm_config_toml_marker(tmp_path: Path):
    (tmp_path / ".clm").mkdir()
    (tmp_path / ".clm" / "config.toml").write_text("", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


def test_clm_toml_marker(tmp_path: Path):
    (tmp_path / "clm.toml").write_text("", encoding="utf-8")
    sub = tmp_path / "x"
    sub.mkdir()
    assert find_project_root(sub) == tmp_path.resolve()


def test_git_dir_marker(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "deep" / "nest"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


def test_git_file_marker_recognizes_worktree(tmp_path: Path):
    # A linked git worktree records its ``.git`` as a FILE (a gitdir pointer),
    # not a directory; ``.exists()`` (not ``is_dir``) must still recognize it.
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n", encoding="utf-8")
    sub = tmp_path / "slides" / "topic"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


def test_nearest_pyproject_wins(tmp_path: Path):
    # An inner project nested under an outer one: the nearest pyproject (the one
    # that would carry [tool.clm]) must win.
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    sub = inner / "sub"
    sub.mkdir()
    assert find_project_root(sub) == inner.resolve()


def test_bare_clm_dir_is_not_a_marker(tmp_path: Path):
    # A *topic* directory has its own ``.clm/`` (voiceover scratch + sync ledger).
    # A bare ``.clm`` dir (no config.toml) must NOT stop the walk there, or the
    # ascent halts at the topic and #477 is not fixed. The real root is found via
    # its pyproject.toml above.
    (tmp_path / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    topic = tmp_path / "slides" / "topic_031"
    topic.mkdir(parents=True)
    (topic / ".clm").mkdir()  # bare, no config.toml — voiceover/ledger scratch
    assert find_project_root(topic) == tmp_path.resolve()


def test_no_marker_falls_back_to_start(tmp_path: Path):
    lonely = tmp_path / "no" / "markers" / "here"
    lonely.mkdir(parents=True)
    if _has_marker_ancestor(tmp_path):  # pragma: no cover - environment-dependent
        import pytest

        pytest.skip("temp dir lives under a marked project; fallback not isolatable")
    assert find_project_root(lonely) == lonely.resolve()


def test_default_start_is_cwd(tmp_path: Path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[tool.clm]\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert find_project_root() == tmp_path.resolve()
