"""Tests for ``scripts/update_exclude_newer.py``.

The script must atomically (a) edit pyproject.toml and (b) refresh
uv.lock by shelling out to ``uv lock``. The contract is the whole point
of the script's existence — bumping pyproject without realigning the
lockfile is the bug class this script is supposed to prevent.

We don't actually invoke ``uv lock`` from these tests; instead we
monkeypatch ``subprocess.run`` and ``shutil.which`` so the tests stay
hermetic and fast.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_exclude_newer.py"
_spec = importlib.util.spec_from_file_location("update_exclude_newer", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
update_exclude_newer = importlib.util.module_from_spec(_spec)
sys.modules["update_exclude_newer"] = update_exclude_newer
_spec.loader.exec_module(update_exclude_newer)


@pytest.fixture
def fake_pyproject(tmp_path, monkeypatch):
    """Redirect the script at a tmp pyproject.toml so tests don't mutate
    the real one."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.uv]\nexclude-newer = "2025-12-01"\n', encoding="utf-8")
    monkeypatch.setattr(update_exclude_newer, "PYPROJECT", pyproject)
    return pyproject


class _RecordingRun:
    """Substitute for ``subprocess.run`` that records calls and returns
    a configurable returncode."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), "kwargs": kwargs})
        return subprocess.CompletedProcess(args=cmd, returncode=self.returncode)


class TestEditPhase:
    def test_explicit_date_is_canonicalized(self, fake_pyproject, monkeypatch):
        # A bare date means "allow the whole day", so the pin becomes the
        # next UTC midnight — the exact form uv normalizes a bare date to
        # (issue #524).
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", _RecordingRun())

        rc = update_exclude_newer.main()

        assert rc == 0
        assert 'exclude-newer = "2026-04-21T00:00:00Z"' in fake_pyproject.read_text()

    def test_month_rollover_is_handled(self, fake_pyproject, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-30"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", _RecordingRun())

        rc = update_exclude_newer.main()

        assert rc == 0
        assert 'exclude-newer = "2026-05-01T00:00:00Z"' in fake_pyproject.read_text()

    def test_full_timestamp_is_passed_through(self, fake_pyproject, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20T12:30:00Z"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", _RecordingRun())

        rc = update_exclude_newer.main()

        assert rc == 0
        assert 'exclude-newer = "2026-04-20T12:30:00Z"' in fake_pyproject.read_text()

    def test_invalid_argument_is_rejected(self, fake_pyproject, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "not-a-date"])
        monkeypatch.setattr(
            update_exclude_newer.subprocess,
            "run",
            lambda *a, **kw: pytest.fail("uv lock must not run for an invalid argument"),
        )

        rc = update_exclude_newer.main()

        assert rc == 1
        assert "not-a-date" in capsys.readouterr().err
        # pyproject must be untouched.
        assert 'exclude-newer = "2025-12-01"' in fake_pyproject.read_text()

    def test_default_is_14_days_ago_canonicalized(self, fake_pyproject, monkeypatch):
        from datetime import date, timedelta

        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", _RecordingRun())

        rc = update_exclude_newer.main()

        assert rc == 0
        # 14 days ago, canonicalized to the following UTC midnight.
        expected = (date.today() - timedelta(days=13)).isoformat()
        assert f'exclude-newer = "{expected}T00:00:00Z"' in fake_pyproject.read_text()

    def test_missing_pin_in_pyproject_returns_error(self, tmp_path, monkeypatch):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.uv]\n# no pin here\n", encoding="utf-8")
        monkeypatch.setattr(update_exclude_newer, "PYPROJECT", pyproject)
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20"])
        # uv.run/which should not be reached.
        monkeypatch.setattr(
            update_exclude_newer.subprocess,
            "run",
            lambda *a, **kw: pytest.fail("uv lock must not run when edit fails"),
        )

        rc = update_exclude_newer.main()

        assert rc == 1


class TestUvLockPhase:
    def test_uv_lock_is_invoked_after_edit(self, fake_pyproject, monkeypatch):
        recorder = _RecordingRun()
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", recorder)

        rc = update_exclude_newer.main()

        assert rc == 0
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["cmd"] == ["uv", "lock"]
        # Must run from the directory containing the project's pyproject.toml,
        # otherwise uv resolves the wrong workspace.
        assert recorder.calls[0]["kwargs"].get("cwd") == fake_pyproject.parent

    def test_missing_uv_binary_returns_clear_error(self, fake_pyproject, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: None)
        # subprocess.run must NOT be called when uv is missing — the script
        # has to bail out before the spawn.
        monkeypatch.setattr(
            update_exclude_newer.subprocess,
            "run",
            lambda *a, **kw: pytest.fail("subprocess.run called when uv is missing"),
        )

        rc = update_exclude_newer.main()

        assert rc == 2
        # pyproject was still updated — but the user must be told the lockfile
        # is now stale so they can recover.
        assert 'exclude-newer = "2026-04-21T00:00:00Z"' in fake_pyproject.read_text()
        err = capsys.readouterr().err
        assert "uv.lock" in err
        assert "stale" in err.lower()

    def test_uv_lock_failure_propagates_returncode(self, fake_pyproject, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["update_exclude_newer.py", "2026-04-20"])
        monkeypatch.setattr(update_exclude_newer.shutil, "which", lambda _: "/usr/bin/uv")
        monkeypatch.setattr(update_exclude_newer.subprocess, "run", _RecordingRun(returncode=42))

        rc = update_exclude_newer.main()

        assert rc == 42
        err = capsys.readouterr().err
        assert "uv lock" in err
        assert "42" in err
