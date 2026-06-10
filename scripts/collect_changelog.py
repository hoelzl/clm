#!/usr/bin/env python3
"""Fold changelog.d/ fragments into CHANGELOG.md as a new release section.

Usage:
    python scripts/collect_changelog.py 1.12.0
    python scripts/collect_changelog.py 1.12.0 --date 2026-06-15
    python scripts/collect_changelog.py 1.12.0 --dry-run

Changelog entries are written as *fragment files* under ``changelog.d/``
(one file per PR, named ``<pr-or-issue>-<slug>.<type>.md``) instead of
being inserted into CHANGELOG.md's ``[Unreleased]`` section directly —
concurrent PRs all inserting at the same lines was the dominant source
of merge conflicts. At release time this script:

1. groups the fragments by type into Keep-a-Changelog sections
   (Added / Changed / Deprecated / Removed / Fixed / Security),
2. prepends a ``## [X.Y.Z] - DATE`` section to CHANGELOG.md (folding in
   any entries that were still added to ``[Unreleased]`` by hand), and
3. deletes the collected fragment files.

See ``changelog.d/README.md`` for the fragment conventions.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
FRAGMENTS_DIR = REPO_ROOT / "changelog.d"

# Keep-a-Changelog section order; fragment type suffix = lowercase name.
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
TYPE_TO_SECTION = {name.lower(): name for name in SECTIONS}

# Files in changelog.d/ that are not fragments.
NON_FRAGMENTS = {"README.md", ".gitkeep"}

FRAGMENT_NAME_RE = re.compile(r".+\.(?P<type>[a-z]+)\.md")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

# Boilerplate kept under the (otherwise empty) Unreleased heading; the
# parser strips it so it never leaks into a release section.
UNRELEASED_NOTE = (
    "Unreleased changes are collected as fragment files in"
    " [`changelog.d/`](changelog.d/)\nand folded into this file by"
    " `scripts/collect_changelog.py` at release time.\n"
)


class ChangelogError(Exception):
    """A condition that should abort the collection with a message."""


def split_changelog(text: str) -> tuple[str, str, str]:
    """Split CHANGELOG.md into (head incl. Unreleased heading, Unreleased
    body, tail from the first released section on)."""
    heading = re.search(r"^## \[Unreleased\][^\n]*\n", text, re.MULTILINE)
    if heading is None:
        raise ChangelogError(f"no '## [Unreleased]' heading found in {CHANGELOG}")
    head = text[: heading.end()]
    rest = text[heading.end() :]
    next_release = re.search(r"^## ", rest, re.MULTILINE)
    if next_release is None:
        return head, rest, ""
    return head, rest[: next_release.start()], rest[next_release.start() :]


def parse_unreleased_body(body: str) -> dict[str, str]:
    """Parse hand-written ``### Section`` blocks out of the Unreleased body.

    Returns {canonical section name: stripped block text} for non-empty
    sections. Unknown headings or content outside any section are errors —
    silently dropping changelog text is the one failure mode this script
    must never have.
    """
    body = body.replace(UNRELEASED_NOTE, "")
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        block = "\n".join(buf).strip()
        if not block:
            return
        if current is None:
            raise ChangelogError(
                f"found content in [Unreleased] outside any '### Section' heading:\n{block[:200]}"
            )
        sections[current] = block

    for line in body.splitlines():
        match = re.fullmatch(r"### +(.+?)\s*", line)
        if match:
            flush()
            buf = []
            current = match.group(1)
            if current not in SECTIONS:
                raise ChangelogError(
                    f"unknown section '### {current}' in [Unreleased]; "
                    f"expected one of: {', '.join(SECTIONS)}"
                )
        else:
            buf.append(line)
    flush()
    return sections


def load_fragments(fragments_dir: Path) -> dict[str, list[str]]:
    """Read fragment files, returning {section name: [entry blocks]} with
    entries ordered by filename."""
    if not fragments_dir.is_dir():
        raise ChangelogError(f"fragments directory {fragments_dir} does not exist")
    fragments: dict[str, list[str]] = {}
    for path in sorted(fragments_dir.iterdir()):
        if path.name in NON_FRAGMENTS:
            continue
        match = FRAGMENT_NAME_RE.fullmatch(path.name)
        if match is None:
            raise ChangelogError(
                f"unrecognized file in {fragments_dir.name}/: '{path.name}' "
                "(expected '<pr-or-issue>-<slug>.<type>.md')"
            )
        section = TYPE_TO_SECTION.get(match.group("type"))
        if section is None:
            raise ChangelogError(
                f"unknown fragment type '.{match.group('type')}.md' in '{path.name}'; "
                f"expected one of: {', '.join(sorted(TYPE_TO_SECTION))}"
            )
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ChangelogError(f"fragment '{path.name}' is empty")
        fragments.setdefault(section, []).append(text)
    return fragments


def build_release_section(
    version: str,
    release_date: str,
    leftover: dict[str, str],
    fragments: dict[str, list[str]],
) -> str:
    parts = [f"## [{version}] - {release_date}\n"]
    for section in SECTIONS:
        blocks = ([leftover[section]] if section in leftover else []) + fragments.get(section, [])
        if blocks:
            parts.append(f"\n### {section}\n\n")
            parts.append("\n\n".join(blocks) + "\n")
    return "".join(parts)


def collect(version: str, release_date: str, dry_run: bool) -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    if f"## [{version}]" in text:
        raise ChangelogError(f"CHANGELOG.md already has a section for {version}")

    head, body, tail = split_changelog(text)
    leftover = parse_unreleased_body(body)
    fragments = load_fragments(FRAGMENTS_DIR)
    if not leftover and not fragments:
        raise ChangelogError(
            f"nothing to collect: no fragments in {FRAGMENTS_DIR.name}/ and "
            "the [Unreleased] section is empty"
        )

    release_section = build_release_section(version, release_date, leftover, fragments)
    new_text = (
        head + "\n" + UNRELEASED_NOTE + "\n" + release_section + ("\n" + tail if tail else "")
    )
    collected = [path for path in sorted(FRAGMENTS_DIR.iterdir()) if path.name not in NON_FRAGMENTS]

    if dry_run:
        print(release_section, end="")
        print(f"\n[dry-run] would update {CHANGELOG.name} and delete:", file=sys.stderr)
        for path in collected:
            print(f"[dry-run]   {FRAGMENTS_DIR.name}/{path.name}", file=sys.stderr)
        return 0

    CHANGELOG.write_text(new_text, encoding="utf-8", newline="\n")
    for path in collected:
        path.unlink()
    print(f"CHANGELOG.md: added section [{version}] - {release_date}")
    print(f"deleted {len(collected)} fragment(s) from {FRAGMENTS_DIR.name}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="release version, e.g. 1.12.0")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="release date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the release section without modifying anything",
    )
    args = parser.parse_args()

    if not VERSION_RE.fullmatch(args.version):
        print(f"error: '{args.version}' is not an X.Y.Z version", file=sys.stderr)
        return 1
    try:
        return collect(args.version, args.date, args.dry_run)
    except ChangelogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
