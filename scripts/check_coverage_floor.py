"""Phase 7 item 4 (#801): per-module coverage floors on what Phase 8 moves.

The re-layering (#802) relocates ``build.py``, ``course.py``, ``path_utils.py``
and the backends. A refactor of those modules with sinking test coverage is
exactly how a "mechanical move" quietly sheds behavior, so their line coverage
is floored here and checked in CI's unit job. Floors are set a few points
below the measured baseline (so ordinary churn does not flake the gate) and
should be RAISED as coverage genuinely improves — never lowered to make a
red gate green without a maintainer decision.

The handover's landmine applies: a coverage number can be met by tautological
tests. The floor is the cheap tripwire; the Phase-7 review round and the
golden suite are what make the number mean something.

Usage::

    python scripts/check_coverage_floor.py coverage.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: filename suffix (as it appears in coverage.xml) -> minimum line-rate %.
#: Baselines measured 2026-08-06 on the unit suite (`-m "not slow and not
#: integration and not e2e and not docker" --cov=src/clm`); floors sit ~5
#: points below the measurement.
FLOORS: dict[str, float] = {
    "cli/commands/build.py": 75.0,  # measured 2026-08-06: 80.9
    "core/course.py": 84.0,  # measured 2026-08-06: 89.5
    "infrastructure/utils/path_utils.py": 91.0,  # measured 2026-08-06: 96.5
    "infrastructure/backend.py": 95.0,  # measured 2026-08-06: 100.0
    "infrastructure/backends/local_ops_backend.py": 83.0,  # measured 2026-08-06: 88.6
    "infrastructure/backends/sqlite_backend.py": 79.0,  # measured 2026-08-06: 84.2
}


def module_line_rates(coverage_xml: Path) -> dict[str, float]:
    """``suffix -> line-rate %`` for every floored module found in the report."""
    tree = ET.parse(coverage_xml)
    rates: dict[str, float] = {}
    for cls in tree.iter("class"):
        filename = (cls.get("filename") or "").replace("\\", "/")
        for suffix in FLOORS:
            if filename.endswith(suffix):
                rates[suffix] = float(cls.get("line-rate") or 0.0) * 100.0
    return rates


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    report = Path(sys.argv[1])
    if not report.is_file():
        print(f"ERROR: {report} not found", file=sys.stderr)
        return 2
    rates = module_line_rates(report)
    failures = 0
    for suffix, floor in sorted(FLOORS.items()):
        rate = rates.get(suffix)
        if rate is None:
            failures += 1
            print(f"FLOOR MISS  {suffix}: not present in the coverage report")
            continue
        verdict = "ok" if rate >= floor else "BELOW FLOOR"
        if rate < floor:
            failures += 1
        print(f"{verdict:>12}  {suffix}: {rate:.1f}% (floor {floor:.1f}%)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
