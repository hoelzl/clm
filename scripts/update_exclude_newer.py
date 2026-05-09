#!/usr/bin/env python3
"""Update the exclude-newer date in pyproject.toml and refresh uv.lock.

Usage:
    python scripts/update_exclude_newer.py          # sets to 14 days ago
    python scripts/update_exclude_newer.py 2026-04-01  # sets to specific date

After editing pyproject.toml, this script invokes ``uv lock`` so that
``uv.lock``'s ``[options].exclude-newer`` is realigned with the new pin.
The two values must agree: when they don't, ``uv run`` silently re-locks
the working tree on the next invocation, surfacing as a mystery
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


def main() -> int:
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = (date.today() - timedelta(days=DEFAULT_DAYS)).isoformat()

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
