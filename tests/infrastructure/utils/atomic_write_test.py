"""Tests for the atomic byte-write helper (split from path_utils_test in #802/A6)."""

import errno
import os
from pathlib import Path

import pytest

from clm.infrastructure.utils.path_utils import atomic_write_all, atomic_write_bytes


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


def _flaky_replace_for(monkeypatch, failing_name: str, *, times: int) -> dict[str, int]:
    """Make ``os.replace`` onto ``failing_name`` raise WinError-5-style
    ``PermissionError`` the first ``times`` times; stub the backoff sleep."""
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        if Path(dst).name == failing_name and calls["n"] < times:
            calls["n"] += 1
            raise PermissionError(errno.EACCES, "Access is denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)
    return calls


class TestAtomicWriteAllReplacePhase:
    """Regression tests for #1099: the multi-file replace phase.

    ``atomic_write_all`` used a bare ``os.replace`` per target, so one
    transient Windows ``PermissionError`` (Defender / indexer / editor
    handle) on the second file left the first one replaced — a one-sided
    DE/EN pair.
    """

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        de = tmp_path / "bullets.de.py"
        en = tmp_path / "bullets.en.py"
        de.write_text("old de\n", encoding="utf-8")
        en.write_text("old en\n", encoding="utf-8")
        return de, en

    def test_transient_replace_error_is_retried(self, tmp_path, monkeypatch):
        de, en = self._pair(tmp_path)
        calls = _flaky_replace_for(monkeypatch, en.name, times=1)

        atomic_write_all([(de, "new de\n"), (en, "new en\n")])

        assert calls["n"] == 1
        assert de.read_text(encoding="utf-8") == "new de\n"
        assert en.read_text(encoding="utf-8") == "new en\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_persistent_replace_error_rolls_back_replaced_targets(self, tmp_path, monkeypatch):
        de, en = self._pair(tmp_path)
        _flaky_replace_for(monkeypatch, en.name, times=10_000)

        with pytest.raises(PermissionError):
            atomic_write_all([(de, "new de\n"), (en, "new en\n")])

        # The already-replaced DE target is restored: no one-sided pair.
        assert de.read_text(encoding="utf-8") == "old de\n"
        assert en.read_text(encoding="utf-8") == "old en\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_rollback_removes_targets_that_did_not_exist(self, tmp_path, monkeypatch):
        created = tmp_path / "voiceover_t.de.py"
        existing = tmp_path / "voiceover_t.en.py"
        existing.write_text("old en\n", encoding="utf-8")
        _flaky_replace_for(monkeypatch, existing.name, times=10_000)

        with pytest.raises(PermissionError):
            atomic_write_all([(created, "new de\n"), (existing, "new en\n")])

        assert not created.exists()
        assert existing.read_text(encoding="utf-8") == "old en\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_interrupt_during_backoff_rolls_back(self, tmp_path, monkeypatch):
        de, en = self._pair(tmp_path)
        _flaky_replace_for(monkeypatch, en.name, times=10_000)

        def interrupted_sleep(_s):
            raise KeyboardInterrupt

        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", interrupted_sleep)

        with pytest.raises(KeyboardInterrupt):
            atomic_write_all([(de, "new de\n"), (en, "new en\n")])

        assert de.read_text(encoding="utf-8") == "old de\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_zero_retries_still_replaces(self, tmp_path):
        de, en = self._pair(tmp_path)

        atomic_write_all([(de, "new de\n"), (en, "new en\n")], max_retries=0)

        assert de.read_text(encoding="utf-8") == "new de\n"
        assert en.read_text(encoding="utf-8") == "new en\n"

    def test_half_written_temp_is_cleaned_up(self, tmp_path, monkeypatch):
        de, en = self._pair(tmp_path)
        real_write_text = Path.write_text

        def failing_write_text(self, data, *args, **kwargs):
            if self.name == en.name + ".tmp":
                real_write_text(self, data[:3], *args, **kwargs)
                raise OSError(errno.ENOSPC, "No space left", str(self))
            return real_write_text(self, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write_text)

        with pytest.raises(OSError):
            atomic_write_all([(de, "new de\n"), (en, "new en\n")])

        assert de.read_text(encoding="utf-8") == "old de\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_non_transient_replace_error_is_not_retried(self, tmp_path, monkeypatch):
        de, en = self._pair(tmp_path)
        real_replace = os.replace
        calls = {"n": 0}

        def enospc_on_en(src, dst):
            if Path(dst).name == en.name:
                calls["n"] += 1
                raise OSError(errno.ENOSPC, "No space left", str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", enospc_on_en)

        with pytest.raises(OSError) as excinfo:
            atomic_write_all([(de, "new de\n"), (en, "new en\n")])

        assert excinfo.value.errno == errno.ENOSPC
        assert calls["n"] == 1
        assert de.read_text(encoding="utf-8") == "old de\n"
        assert list(tmp_path.glob("*.tmp")) == []
