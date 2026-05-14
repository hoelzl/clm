"""Tests for :class:`PendingRenameQueue` — the lock-deferred rename buffer.

The queue is what saves the recording pipeline from the
"Auphonic-upload-locks-the-file" race: a rename that ``safe_move``
declares lockedmainstreams into the queue, the new recording lands
unimpeded, and a later drain (typically wired to job-terminal events)
re-attempts the rename once the upload finishes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clm.recordings.workflow.rename_queue import PendingRename, PendingRenameQueue
from clm.recordings.workflow.safe_move import FileLockedError


def _force_locked(*_args, **_kwargs) -> None:
    raise FileLockedError(Path("src"), Path("dst"), 4, PermissionError("WinError 32"))


def test_try_or_defer_succeeds_immediately(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    callbacks: list[tuple[Path, Path]] = []
    ok = queue.try_or_defer(src, dst, reason="t", on_success=lambda s, d: callbacks.append((s, d)))

    assert ok is True
    assert dst.exists()
    assert len(queue) == 0
    assert callbacks == [(src, dst)]


def test_try_or_defer_enqueues_on_lock(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    with patch("clm.recordings.workflow.rename_queue.safe_move", side_effect=_force_locked):
        ok = queue.try_or_defer(src, dst, reason="cascade")

    assert ok is False
    assert len(queue) == 1
    [entry] = queue.snapshot()
    assert entry.src == src and entry.dst == dst and entry.reason == "cascade"


def test_enqueue_is_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    with patch("clm.recordings.workflow.rename_queue.safe_move", side_effect=_force_locked):
        queue.try_or_defer(src, dst, reason="r1")
        queue.try_or_defer(src, dst, reason="r2")  # same (src,dst) — should not double-enqueue

    assert len(queue) == 1


def test_drain_runs_succeeded_entries_and_clears_them(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    with patch("clm.recordings.workflow.rename_queue.safe_move", side_effect=_force_locked):
        queue.try_or_defer(src, dst, reason="t")

    succeeded, failed = queue.drain()

    assert len(succeeded) == 1
    assert succeeded[0].src == src
    assert failed == []
    assert dst.exists()
    assert not src.exists()
    assert len(queue) == 0


def test_drain_keeps_still_locked_entries_queued(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    with patch("clm.recordings.workflow.rename_queue.safe_move", side_effect=_force_locked):
        queue.try_or_defer(src, dst, reason="t")
        succeeded, failed = queue.drain()

    assert succeeded == []
    assert failed == []
    assert len(queue) == 1


def test_drain_predicate_filters_entries(tmp_path: Path) -> None:
    a_src = tmp_path / "a.bin"
    a_src.write_bytes(b"x")
    a_dst = tmp_path / "a-renamed.bin"
    b_src = tmp_path / "b.bin"
    b_src.write_bytes(b"y")
    b_dst = tmp_path / "b-renamed.bin"

    queue = PendingRenameQueue()
    with patch("clm.recordings.workflow.rename_queue.safe_move", side_effect=_force_locked):
        queue.try_or_defer(a_src, a_dst, reason="a")
        queue.try_or_defer(b_src, b_dst, reason="b")

    succeeded, _ = queue.drain(predicate=lambda e: e.src == a_src)

    assert len(succeeded) == 1
    assert a_dst.exists()
    assert not b_dst.exists()
    assert len(queue) == 1  # b still pending


def test_drain_drops_vanished_sources(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    queue._enqueue(PendingRename(src=src, dst=dst, reason="manual"))
    src.unlink()  # somebody else moved/deleted it before drain
    succeeded, failed = queue.drain()

    assert succeeded == []
    assert failed == []
    assert len(queue) == 0


def test_drain_evicts_permanent_failures(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst = tmp_path / "dst.bin"
    queue = PendingRenameQueue()

    queue._enqueue(PendingRename(src=src, dst=dst, reason="manual"))
    with patch(
        "clm.recordings.workflow.rename_queue.safe_move",
        side_effect=OSError("disk full"),
    ):
        succeeded, failed = queue.drain()

    assert succeeded == []
    assert len(failed) == 1
    assert isinstance(failed[0][1], OSError)
    # Permanent failures don't loop forever — they're evicted.
    assert len(queue) == 0
