#!/usr/bin/env python3
"""Update the exclude-newer timestamp in pyproject.toml and refresh uv.lock.

Usage:
    python scripts/update_exclude_newer.py                       # 14 days ago
    python scripts/update_exclude_newer.py 2026-04-01            # specific date
    python scripts/update_exclude_newer.py 2026-04-02T00:00:00Z  # exact timestamp

A bare date ``D`` means "allow packages published through the end of day
``D`` (UTC)" and is canonicalized to ``<D+1>T00:00:00Z`` — the exact form
uv itself normalizes a bare date to. Pinning the full timestamp in
pyproject.toml keeps ``uv lock --check`` / ``uv sync --locked`` happy on a
clean checkout: uv compares exclude-newer *timestamps* exactly, so any
other representation (bare date, timezone-shifted local midnight) makes uv
consider the lockfile stale (issue #524).

After editing pyproject.toml, this script invokes ``uv lock`` so that
``uv.lock``'s ``[options].exclude-newer`` is realigned with the new pin.
The two values must agree exactly: when they don't, ``uv run`` silently
re-locks the working tree on the next invocation, surfacing as a mystery
``uv.lock`` modification. See ``scripts/check_exclude_newer.py`` for the
companion drift check.
"""

import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
PATTERN = re.compile(r'(exclude-newer\s*=\s*)"[^"]*"')
DEFAULT_DAYS = 14


def canonical_timestamp(value: str) -> str:
    """Return uv's canonical exclude-newer form for ``value``.

    A bare ``YYYY-MM-DD`` date becomes ``<date+1>T00:00:00Z`` (uv's own
    normalization: the whole day is included, so the cutoff is the next UTC
    midnight). A full timestamp is passed through unchanged so callers can
    pin an exact instant.
    """
    if "T" in value:
        return value
    day = date.fromisoformat(value)
    return f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z"


def main() -> int:
    if len(sys.argv) > 1:
        raw_target = sys.argv[1]
    else:
        raw_target = (date.today() - timedelta(days=DEFAULT_DAYS)).isoformat()

    try:
        target = canonical_timestamp(raw_target)
    except ValueError:
        print(
            f"error: {raw_target!r} is neither a YYYY-MM-DD date nor a timestamp",
            file=sys.stderr,
        )
        return 1

    text = PYPROJECT.read_text()
    new_text, count = PATTERN.subn(rf'\1"{target}"', text, count=1)
    if count == 0:
        print("error: exclude-newer not found in pyproject.toml", file=sys.stderr)
        return 1

    PYPROJECT.write_text(new_text)
    print(f"exclude-newer set to {target}")

    # Realign uv.lock so the next `uv run` doesn't silently re-lock.
    # The bump-without-lock-refresh mistake is the whole reason this
    # script needs to do two steps atomically.
    if shutil.which("uv") is None:
        print(
            "error: 'uv' not found on PATH — pyproject.toml was updated but "
            "uv.lock is now stale. Install uv and run 'uv lock' manually, or "
            "rerun this script with uv on PATH.",
            file=sys.stderr,
        )
        return 2

    print("running 'uv lock' to align uv.lock metadata...")
    result = subprocess.run(["uv", "lock"], cwd=PYPROJECT.parent)
    if result.returncode != 0:
        print(
            f"error: 'uv lock' exited with status {result.returncode}; "
            "pyproject.toml was updated but uv.lock is now stale.",
            file=sys.stderr,
        )
        return result.returncode
    print("uv.lock realigned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
