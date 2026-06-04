"""Tests for CLM log-path resolution, including the ``CLM_LOG_DIR`` override.

The override exists so that pytest-xdist workers (and any other processes that
must not share the single global ``clm.log``) can each point at their own
directory. See ``clm.infrastructure.logging.log_paths``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.infrastructure.logging import log_paths
from clm.infrastructure.logging.log_paths import (
    LOG_DIR_ENV_VAR,
    get_log_dir,
    get_main_log_path,
    get_worker_log_dir,
)


def test_get_log_dir_honours_env_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "custom-logs"
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(target))

    result = get_log_dir()

    assert result == target
    assert result.is_dir()  # created on access


def test_get_log_dir_creates_nested_override_dir(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "a" / "b" / "logs"
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(target))

    assert not target.exists()
    assert get_log_dir() == target
    assert target.is_dir()


def test_main_and_worker_paths_follow_override(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "logs"
    monkeypatch.setenv(LOG_DIR_ENV_VAR, str(target))

    assert get_main_log_path() == target / "clm.log"
    assert get_worker_log_dir() == target / "workers"


def test_empty_override_falls_back_to_platform_default(monkeypatch, tmp_path: Path) -> None:
    # An empty value is falsy and must not redirect to the current directory
    # (``Path("")``); it falls back to the platform default instead.
    monkeypatch.setenv(LOG_DIR_ENV_VAR, "")
    sentinel = tmp_path / "platform-default"
    monkeypatch.setattr(
        log_paths.platformdirs,
        "user_log_dir",
        lambda *a, **k: str(sentinel),
    )

    result = get_log_dir()

    assert result == sentinel
    assert result != Path("")


def test_no_override_uses_platformdirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(LOG_DIR_ENV_VAR, raising=False)
    sentinel = tmp_path / "platform-default"
    monkeypatch.setattr(
        log_paths.platformdirs,
        "user_log_dir",
        lambda *a, **k: str(sentinel),
    )

    assert get_log_dir() == sentinel
