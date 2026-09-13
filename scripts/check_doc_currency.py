#!/usr/bin/env python3
"""Check the doc-currency headers of the discussion layers.

Every Markdown file under ``docs/claude/discussions/`` (the resumable
discussions stack: ``README.md``, ``register.md``, each thread's ``state.md``
and its cleaned transcripts -- see ``docs/claude/discussions/README.md``)
must carry a status header:

    ---
    status: active | superseded | archived
    updated: YYYY-MM-DD
    review-by: YYYY-MM-DD      # required only while status == active
    ---

This gate checks the *structural* contract (header present, valid status,
parseable dates) as a HARD failure — that is time-invariant, so it can safely
block a PR. A doc merely *past* its ``review-by`` is reported as a SOFT warning
(historical, needs re-verification — but time passing must not break unrelated
PRs). Pass ``--strict`` to also fail on past-review-by (e.g. a scheduled audit).

Deliberately dependency-free (no PyYAML) so it runs with a bare Python. The
rest of ``docs/`` carries no such header in this repository; the scope is
only the discussions tree, where the header is what tells a rewritten
``state.md`` from a frozen transcript.

Usage:
    python scripts/check_doc_currency.py            # hard checks only
    python scripts/check_doc_currency.py --strict   # also fail on past review-by
    python scripts/check_doc_currency.py --github    # emit GitHub Actions annotations
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOTS = ("docs/claude/discussions",)
VALID_STATUS = {"active", "superseded", "archived"}
REPO_ROOT = Path(__file__).resolve().parent.parent


class Problem:
    __slots__ = ("path", "line", "level", "msg")

    def __init__(self, path: Path, level: str, msg: str, line: int = 1) -> None:
        self.path = path
        self.level = level  # "error" | "warning"
        self.msg = msg
        self.line = line


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Return the top YAML frontmatter as a flat str->str dict, or None if absent.

    Only the simple ``key: value`` lines we require are parsed — no nested YAML.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return None  # no closing fence -> malformed, treat as missing


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def check_file(path: Path, today: dt.date) -> list[Problem]:
    rel = path.relative_to(REPO_ROOT)
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        return [
            Problem(
                rel, "error", "missing status frontmatter (see docs/claude/discussions/README.md)"
            )
        ]

    problems: list[Problem] = []

    status = fm.get("status")
    if status is None:
        problems.append(Problem(rel, "error", "frontmatter missing 'status:'"))
    elif status not in VALID_STATUS:
        problems.append(
            Problem(
                rel, "error", f"invalid status '{status}' (expected one of {sorted(VALID_STATUS)})"
            )
        )

    if "updated" not in fm:
        problems.append(Problem(rel, "error", "frontmatter missing 'updated:'"))
    elif parse_date(fm["updated"]) is None:
        problems.append(
            Problem(rel, "error", f"'updated: {fm['updated']}' is not a YYYY-MM-DD date")
        )

    if status == "active":
        review_by = fm.get("review-by")
        if review_by is None:
            problems.append(Problem(rel, "error", "active doc missing 'review-by:'"))
        else:
            when = parse_date(review_by)
            if when is None:
                problems.append(
                    Problem(rel, "error", f"'review-by: {review_by}' is not a YYYY-MM-DD date")
                )
            elif when < today:
                problems.append(
                    Problem(
                        rel, "warning", f"past its review-by ({review_by}) — re-verify or archive"
                    )
                )
    return problems


def iter_docs() -> list[Path]:
    found: list[Path] = []
    for root in ROOTS:
        base = REPO_ROOT / root
        if base.is_dir():
            found.extend(sorted(base.rglob("*.md")))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="fail (not just warn) on past review-by")
    ap.add_argument(
        "--github",
        action="store_true",
        help="emit GitHub Actions ::error::/::warning:: annotations",
    )
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args()

    today = parse_date(args.today) if args.today else dt.date.today()
    if today is None:
        print(f"invalid --today value: {args.today!r}", file=sys.stderr)
        return 2

    all_problems: list[Problem] = []
    for path in iter_docs():
        all_problems.extend(check_file(path, today))

    errors = [p for p in all_problems if p.level == "error"]
    warnings = [p for p in all_problems if p.level == "warning"]
    if args.strict:
        # promote past-review-by warnings to errors
        errors += warnings
        warnings = []

    for p in warnings:
        if args.github:
            print(f"::warning file={p.path}::{p.msg}")
        else:
            print(f"  WARN  {p.path}: {p.msg}")
    for p in errors:
        if args.github:
            print(f"::error file={p.path}::{p.msg}")
        else:
            print(f"  FAIL  {p.path}: {p.msg}")

    checked = len(iter_docs())
    print(
        f"\nchecked {checked} docs under {', '.join(ROOTS)}: {len(errors)} error(s), {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
