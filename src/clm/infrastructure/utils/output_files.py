"""Writing worker output files, and checking them before a job-cache hit (#799 C6, C11).

A worker writes each output file atomically (a sibling temp file, then
``os.replace``), so a reader never sees a half-written file and a crash mid-write
leaves the previous version in place. The worker records each file's SHA-256 in
the job-cache entry. Before the host reuses an entry instead of running the job,
it checks the files on disk still have those digests: a file that was torn,
overwritten by a stale worker or edited by hand is rebuilt, not trusted.

Entries written before digests existed carry none; for those, the file only has
to exist, as before.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from clm.infrastructure.utils.path_utils import atomic_write_bytes

# Job-cache metadata keys: the main output's digest, and a name -> digest map
# for the companion files listed under ``companion_files``.
OUTPUT_DIGEST_KEY = "sha256"
COMPANION_DIGESTS_KEY = "companion_sha256"

_CHUNK = 1024 * 1024


def write_output_file(path: Path, data: bytes) -> str:
    """Write *data* to *path* atomically; return its SHA-256 hex digest."""
    atomic_write_bytes(path, data)
    return hashlib.sha256(data).hexdigest()


def write_output_text(path: Path, text: str) -> str:
    """Write *text* as UTF-8 with the newlines it has; return its digest.

    No newline translation: Windows Direct workers and Linux Docker workers must
    produce byte-identical output.
    """
    return write_output_file(path, text.encode("utf-8"))


def file_digest(path: Path) -> str:
    """SHA-256 hex digest of the file at *path*."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def unusable_outputs(
    expected: Mapping[Path, str | None],
) -> list[tuple[Path, str]]:
    """Files that can't back a job-cache hit, with the reason for each.

    Args:
        expected: Each output path, mapped to the digest its cache entry
            recorded, or ``None`` if the entry predates digests.

    Returns:
        ``(path, reason)`` for every file that is missing, unreadable, or whose
        content no longer matches its digest. Empty means the hit is usable.
    """
    problems: list[tuple[Path, str]] = []
    for path, digest in expected.items():
        if not path.exists():
            problems.append((path, "missing"))
            continue
        if digest is None:
            continue
        try:
            actual = file_digest(path)
        except OSError as exc:
            problems.append((path, f"unreadable ({exc})"))
            continue
        if actual != digest:
            problems.append((path, "content differs from the cached result"))
    return problems


def recorded_digests(
    output_path: Path, companion_paths: list[Path], metadata: Mapping[str, Any] | None
) -> dict[Path, str | None]:
    """Map the main output and its companions to the digests *metadata* recorded."""
    metadata = metadata or {}
    main = metadata.get(OUTPUT_DIGEST_KEY)
    companions = metadata.get(COMPANION_DIGESTS_KEY)
    if not isinstance(companions, Mapping):
        companions = {}
    result: dict[Path, str | None] = {output_path: main if isinstance(main, str) else None}
    for path in companion_paths:
        digest = companions.get(path.name)
        result[path] = digest if isinstance(digest, str) else None
    return result
