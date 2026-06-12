"""Strip entries from on-disk HTTP-replay cassettes by request host.

CLM's HTTP-replay bootstrap now sets ``ignore_hosts`` so telemetry
endpoints (LangSmith etc.) don't enter new cassettes going forward.
Existing cassettes recorded before that fix still carry those entries
and will continue to bloat git diffs until purged. This script reads
each cassette in place, drops any interaction whose request URI's host
is in the configured list, and rewrites the cassette via vcrpy's
persister so the on-disk format stays consistent with what CLM
produces.

Defaults to LangSmith. Override with --host (repeatable) for other
telemetry/observability endpoints whose request bodies are too volatile
to dedupe cleanly.

Usage:

    uv run python scripts/strip_cassette_hosts.py <root_dir>
    uv run python scripts/strip_cassette_hosts.py <root_dir> --host api.smith.langchain.com --host api.langfuse.com
    uv run python scripts/strip_cassette_hosts.py <root_dir> --dry-run

Exit code 0 if all cassettes processed successfully (whether or not
anything was stripped); 1 if any cassette failed to load/save; 2 on
argument errors.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_DEFAULT_HOSTS = ("api.smith.langchain.com",)
_CASSETTE_GLOB = "*.http-cassette.yaml"


def _request_host(request: Any) -> str:
    """Best-effort hostname extraction from a vcr Request."""
    try:
        host = getattr(request, "host", None)
        if host:
            return str(host)
    except Exception:  # noqa: BLE001 — defensive
        pass
    uri = str(getattr(request, "uri", "") or "")
    if "://" in uri:
        return uri.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return ""


def _iter_cassette_paths(root: Path) -> Iterable[Path]:
    for path in root.rglob(_CASSETTE_GLOB):
        if path.is_file():
            yield path


def strip_cassette(
    path: Path,
    hosts_to_drop: set[str],
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """Return (entries_before, entries_after). Writes only if non-dry-run.

    Skips cassettes that can't be loaded (logged + returned as (0, 0))
    so a corrupt file doesn't abort a course-wide cleanup.
    """
    from clm.infrastructure.http_replay_mitm.vcr_format import (
        load_cassette,
        serialize_cassette,
    )

    try:
        requests, responses = load_cassette(path)
    except Exception as exc:  # noqa: BLE001 — defensive
        print(
            f"  ! skipping {path.name}: load failed ({type(exc).__name__}: {exc})", file=sys.stderr
        )
        return (0, 0)

    before = len(requests)
    keep_requests = []
    keep_responses = []
    for req, resp in zip(requests, responses, strict=False):
        host = _request_host(req)
        if host in hosts_to_drop:
            continue
        keep_requests.append(req)
        keep_responses.append(resp)
    after = len(keep_requests)

    if after == before:
        return (before, after)

    if dry_run:
        return (before, after)

    payload = serialize_cassette({"requests": keep_requests, "responses": keep_responses})
    tmp = path.parent / f"{path.name}.tmp-strip"
    try:
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return (before, after)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        help="Directory to walk for *.http-cassette.yaml files (recursive)",
    )
    parser.add_argument(
        "--host",
        action="append",
        default=None,
        metavar="HOST",
        help=(
            f"Hostname whose entries should be removed (repeatable). "
            f"Defaults to {list(_DEFAULT_HOSTS)} when omitted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing any cassette.",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    hosts_to_drop = set(args.host) if args.host else set(_DEFAULT_HOSTS)
    print(f"Stripping hosts: {sorted(hosts_to_drop)}")
    if args.dry_run:
        print("(dry-run: no files will be modified)")

    total_files = 0
    total_changed = 0
    total_dropped = 0
    failures = 0
    for path in sorted(_iter_cassette_paths(root)):
        total_files += 1
        try:
            before, after = strip_cassette(path, hosts_to_drop, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 — defensive
            print(f"  ! {path}: failed ({type(exc).__name__}: {exc})", file=sys.stderr)
            failures += 1
            continue
        if before != after:
            total_changed += 1
            total_dropped += before - after
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"  - {rel}: {before} -> {after}  (dropped {before - after})")

    print()
    print(f"Cassettes scanned:  {total_files}")
    print(f"Cassettes modified: {total_changed}")
    print(f"Entries dropped:    {total_dropped}")
    if failures:
        print(f"Cassettes failed:   {failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
