"""Tests for the atomic byte-write helper (split from path_utils_test in #802/A6)."""

import errno
import os
from pathlib import Path

import pytest

from clm.infrastructure.utils.path_utils import atomic_write_bytes


class TestAtomicWriteBytes:
    """Verify the cache-hit write path stays robust under transient OS errors."""

    def test_writes_bytes_and_creates_parent_dir(self, tmp_path):
        target = tmp_path / "nested" / "img" / "diagram.png"
        atomic_write_bytes(target, b"\x89PNGdata")

        assert target.read_bytes() == b"\x89PNGdata"
        # No temp leftovers in the destination directory.
        assert list(target.parent.glob("*.tmp")) == []

    def test_overwrites_existing_file_atomically(self, tmp_path):
        target = tmp_path / "diagram.png"
        target.write_bytes(b"old")

        atomic_write_bytes(target, b"new-bytes")

        assert target.read_bytes() == b"new-bytes"

    def test_retries_on_transient_einval_then_succeeds(self, tmp_path, monkeypatch):
        target = tmp_path / "img" / "diagram.png"

        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(errno.EINVAL, "Invalid argument", str(dst))
            return real_replace(src, dst)

        # Sleep is the only non-trivial side effect on the retry path; stub it
        # so the test stays fast.
        monkeypatch.setattr(os, "replace", flaky_replace)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        atomic_write_bytes(target, b"payload")

        assert target.read_bytes() == b"payload"
        assert calls["n"] == 2
        # The failed-attempt temp file must be cleaned up.
        assert list(target.parent.glob("*.tmp")) == []

    def test_non_transient_oserror_propagates_immediately(self, tmp_path, monkeypatch):
        target = tmp_path / "diagram.png"
        calls = {"n": 0}

        def always_enospc(src, dst):
            calls["n"] += 1
            raise OSError(errno.ENOSPC, "No space left", str(dst))

        monkeypatch.setattr(os, "replace", always_enospc)

        with pytest.raises(OSError) as excinfo:
            atomic_write_bytes(target, b"payload")

        assert excinfo.value.errno == errno.ENOSPC
        # Single attempt — no retries on non-transient errnos.
        assert calls["n"] == 1
        assert not target.exists()
        assert list(target.parent.glob("*.tmp")) == []

    def test_gives_up_after_max_retries(self, tmp_path, monkeypatch):
        target = tmp_path / "diagram.png"
        calls = {"n": 0}

        def always_einval(src, dst):
            calls["n"] += 1
            raise OSError(errno.EINVAL, "Invalid argument", str(dst))

        monkeypatch.setattr(os, "replace", always_einval)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        with pytest.raises(OSError) as excinfo:
            atomic_write_bytes(target, b"payload", max_retries=3)

        assert excinfo.value.errno == errno.EINVAL
        assert calls["n"] == 3
        assert list(target.parent.glob("*.tmp")) == []

    def test_uses_unique_temp_name_per_attempt(self, tmp_path, monkeypatch):
        # If a worker briefly held a temp file open, retries must not reuse
        # the same temp name (which could be the file currently locked).
        target = tmp_path / "diagram.png"
        seen_temps: list[str] = []

        original_write_bytes = Path.write_bytes

        def recording_write_bytes(self, data):
            seen_temps.append(self.name)
            return original_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)

        real_replace = os.replace

        def flaky_replace(src, dst):
            if len(seen_temps) < 2:
                raise OSError(errno.EACCES, "Permission denied", str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        atomic_write_bytes(target, b"payload")

        # We saw at least two different temp names.
        assert len(seen_temps) >= 2
        assert len(set(seen_temps)) == len(seen_temps)
