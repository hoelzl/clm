#!/usr/bin/env python3
"""Update the exclude-newer date in pyproject.toml.

Usage:
    python scripts/update_exclude_newer.py          # sets to 14 days ago
    python scripts/update_exclude_newer.py 2026-04-01  # sets to specific date
"""

import re
import sys
from datetime import date, timedelta
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
PATTERN = re.compile(r'(exclude-newer\s*=\s*)"[^"]*"')
DEFAULT_DAYS = 14


def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = (date.today() - timedelta(days=DEFAULT_DAYS)).isoformat()

    text = PYPROJECT.read_text()
    new_text, count = PATTERN.subn(rf'\1"{target}"', text, count=1)
    if count == 0:
        print("error: exclude-newer not found in pyproject.toml", file=sys.stderr)
        sys.exit(1)

    PYPROJECT.write_text(new_text)
    print(f"exclude-newer set to {target}")


if __name__ == "__main__":
    main()
