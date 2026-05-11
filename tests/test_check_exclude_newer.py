"""Tests for ``scripts/check_exclude_newer.py``.

The script keeps pyproject.toml's ``[tool.uv].exclude-newer`` and uv.lock's
``[options].exclude-newer`` from drifting; the tests pin down the
agreement / disagreement / malformed-input cases.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_exclude_newer.py"
_spec = importlib.util.spec_from_file_location("check_exclude_newer", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
check_exclude_newer = importlib.util.module_from_spec(_spec)
sys.modules["check_exclude_newer"] = check_exclude_newer
_spec.loader.exec_module(check_exclude_newer)


def _write_pyproject(path: Path, pin: str | None) -> None:
    body = "[tool.uv]\n"
    if pin is not None:
        body += f'exclude-newer = "{pin}"\n'
    path.write_text(body, encoding="utf-8")


def _write_lock(path: Path, pin: str | None) -> None:
    body = "[options]\n"
    if pin is not None:
        body += f'exclude-newer = "{pin}"\n'
    path.write_text(body, encoding="utf-8")


class TestCheck:
    def test_dates_match_exactly(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, "2026-04-20")
        _write_lock(lock, "2026-04-20")

        ok, message = check_exclude_newer.check(pyproject, lock)
        assert ok is True
        assert message == ""

    def test_lock_has_full_timestamp_with_matching_date(self, tmp_path):
        # uv.lock typically stores the wall-clock end-of-day form;
        # pyproject stores the date-only form. The check compares prefixes.
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, "2026-04-20")
        _write_lock(lock, "2026-04-20T22:00:00Z")

        ok, _ = check_exclude_newer.check(pyproject, lock)
        assert ok is True

    def test_drift_is_reported(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, "2026-04-20")
        _write_lock(lock, "2026-04-18T22:00:00Z")  # older than pyproject

        ok, message = check_exclude_newer.check(pyproject, lock)
        assert ok is False
        assert "2026-04-20" in message
        assert "2026-04-18" in message
        # Remediation guidance must be present so a developer can fix it
        # without consulting external docs.
        assert "uv lock" in message

    def test_lock_ahead_of_pyproject_is_also_drift(self, tmp_path):
        # The asymmetric case: someone hand-edited uv.lock or `uv lock`-d
        # against a different env var without bumping pyproject.
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, "2026-04-20")
        _write_lock(lock, "2026-04-22T22:00:00Z")

        ok, _ = check_exclude_newer.check(pyproject, lock)
        assert ok is False

    def test_pyproject_missing_pin_raises(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, None)
        _write_lock(lock, "2026-04-20T22:00:00Z")

        with pytest.raises(SystemExit) as excinfo:
            check_exclude_newer.check(pyproject, lock)
        assert "[tool.uv].exclude-newer" in str(excinfo.value)

    def test_lock_missing_pin_raises(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        lock = tmp_path / "uv.lock"
        _write_pyproject(pyproject, "2026-04-20")
        _write_lock(lock, None)

        with pytest.raises(SystemExit) as excinfo:
            check_exclude_newer.check(pyproject, lock)
        assert "[options].exclude-newer" in str(excinfo.value)


class TestRepoState:
    """Sanity check the actual repo files (not fixtures): if this fails,
    pre-commit would also fail on master, which is the symptom we want
    to surface immediately."""

    def test_repo_pyproject_and_lock_agree(self):
        ok, message = check_exclude_newer.check(
            check_exclude_newer.PYPROJECT, check_exclude_newer.LOCK
        )
        assert ok, message
