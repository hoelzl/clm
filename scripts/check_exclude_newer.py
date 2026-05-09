#!/usr/bin/env python3
"""Verify that pyproject.toml and uv.lock agree on exclude-newer.

Exits 0 when ``[tool.uv].exclude-newer`` in pyproject.toml matches the
date prefix of ``[options].exclude-newer`` in uv.lock; exits 1 with a
clear remediation message when they drift apart.

Wired into ``.pre-commit-config.yaml`` so a commit that bumps the pin in
pyproject.toml without refreshing uv.lock is rejected before it can
land. Without this guard the lockfile silently re-locks on the next
``uv run``, surfacing as a mystery uv.lock modification in someone
else's working tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"


def _read_pyproject_pin(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    pin = data.get("tool", {}).get("uv", {}).get("exclude-newer")
    if pin is None:
        raise SystemExit(f"error: {pyproject_path} has no [tool.uv].exclude-newer setting")
    if not isinstance(pin, str):
        raise SystemExit(f"error: {pyproject_path} [tool.uv].exclude-newer is not a string")
    return pin


def _read_lock_pin(lock_path: Path) -> str:
    with lock_path.open("rb") as f:
        data = tomllib.load(f)
    pin = data.get("options", {}).get("exclude-newer")
    if pin is None:
        raise SystemExit(
            f"error: {lock_path} has no [options].exclude-newer setting "
            "(was the lockfile regenerated against a uv that doesn't "
            "record this field?)"
        )
    if not isinstance(pin, str):
        raise SystemExit(f"error: {lock_path} [options].exclude-newer is not a string")
    return pin


def check(pyproject_path: Path, lock_path: Path) -> tuple[bool, str]:
    """Return ``(ok, message)``. ``message`` is empty on success."""
    pyproject_pin = _read_pyproject_pin(pyproject_path)
    lock_pin = _read_lock_pin(lock_path)

    # pyproject stores e.g. "2026-04-20"; uv.lock stores e.g.
    # "2026-04-20T22:00:00Z". Compare the date prefix.
    if lock_pin.startswith(pyproject_pin):
        return True, ""

    return False, (
        f"pyproject.toml [tool.uv].exclude-newer is {pyproject_pin!r} but "
        f"uv.lock [options].exclude-newer is {lock_pin!r}.\n"
        "These must agree; otherwise 'uv run' will silently re-lock the "
        "working tree on the next invocation.\n"
        "Fix: run 'uv lock' (or 'python scripts/update_exclude_newer.py "
        "<date>') and commit the resulting uv.lock alongside pyproject.toml."
    )


def main() -> int:
    ok, message = check(PYPROJECT, LOCK)
    if ok:
        return 0
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
