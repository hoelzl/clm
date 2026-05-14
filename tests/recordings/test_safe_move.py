"""Tests for the lock-aware :func:`safe_move` and :class:`FileLockedError`.

The forensic case behind these tests: ``shutil.move`` falls back to
``copy2 + os.unlink`` when ``os.rename`` fails, and on Windows that
combination silently leaves duplicates whenever the source is held open
by an Auphonic upload. ``safe_move`` switches to ``os.replace`` (no
fallback) plus a bounded retry loop so transient AV/indexer locks are
absorbed but a durable lock surfaces explicitly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from clm.recordings.workflow.safe_move import FileLockedError, safe_move


def test_safe_move_renames_file(tmp_path: Path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"data")
    dst = tmp_path / "out.bin"

    result = safe_move(src, dst)

    assert result == dst
    assert dst.read_bytes() == b"data"
    assert not src.exists()


def test_safe_move_creates_parent_directory(tmp_path: Path) -> None:
    src = tmp_path / "in.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "deep" / "nested" / "out.bin"

    safe_move(src, dst)

    assert dst.exists()


def test_safe_move_retries_then_succeeds(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"

    real_replace = __import__("os").replace
    calls = {"n": 0}

    def flaky_replace(s: str, d: str) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        real_replace(s, d)

    with patch("clm.recordings.workflow.safe_move.os.replace", side_effect=flaky_replace):
        safe_move(src, dst, retries=5, retry_interval=0.0)

    assert calls["n"] == 3
    assert dst.exists()


def test_safe_move_raises_after_persistent_lock(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"

    with patch(
        "clm.recordings.workflow.safe_move.os.replace",
        side_effect=PermissionError("never going to release"),
    ):
        with pytest.raises(FileLockedError) as exc_info:
            safe_move(src, dst, retries=2, retry_interval=0.0)

    assert exc_info.value.attempts == 3  # initial + retries
    assert exc_info.value.src == src
    assert exc_info.value.dst == dst
    assert isinstance(exc_info.value.last_error, PermissionError)


def test_safe_move_does_not_create_duplicate_on_lock(tmp_path: Path) -> None:
    """The forensic regression: lock-fail must NOT leave a copy at the destination."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"contents")
    dst = tmp_path / "dst.bin"

    with patch(
        "clm.recordings.workflow.safe_move.os.replace",
        side_effect=PermissionError("locked"),
    ):
        with pytest.raises(FileLockedError):
            safe_move(src, dst, retries=1, retry_interval=0.0)

    # The source must remain (lock prevented any data movement).
    assert src.exists()
    # The destination must NOT have been created — the bug we're fixing
    # is shutil.move's copy2 fallback that left a duplicate behind.
    assert not dst.exists()


def test_safe_move_propagates_non_lock_errors(tmp_path: Path) -> None:
    """A genuine ``OSError`` (not a lock) is raised on the first attempt."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"

    calls = {"n": 0}

    def boom(s: str, d: str) -> None:
        calls["n"] += 1
        raise OSError("disk full")

    with patch("clm.recordings.workflow.safe_move.os.replace", side_effect=boom):
        with pytest.raises(OSError, match="disk full"):
            safe_move(src, dst, retries=5, retry_interval=0.0)

    assert calls["n"] == 1
