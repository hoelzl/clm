"""Per-build output-write registry for deduplication and collision warning.

The build can legitimately produce identical writes to the same output path
(e.g., two topics that share a ``<include>``-sourced file), and it can also
produce *different* writes to the same path silently (the historical last-
writer-wins behavior). The :class:`OutputWriteRegistry` is a per-build
singleton that records every write the build is about to make, decides
whether the new write is a duplicate of a previous one, and surfaces
conflicts so the build report can name both source files.

This module is standalone; it has no dependency on the backend, the build
reporter, or the workers. Integration with the build pipeline is done by
the call sites (the backend's copy hook and the notebook-output writer)
which call :meth:`OutputWriteRegistry.record_write` and act on the
returned :class:`WriteOutcome`.

Image paths (anything under an ``img/`` segment) are intentionally not
handled here — the existing :class:`clm.core.image_registry.ImageRegistry`
remains the sole reporter for image collisions. Use :func:`is_image_path`
to test a source path before deciding which registry to use.
"""

from __future__ import annotations

import hashlib
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Final

from attrs import Factory, define, field, frozen

logger = logging.getLogger(__name__)


DEFAULT_HASH_LIMIT_MB: Final[int] = 50
_ENV_HASH_LIMIT_MB: Final[str] = "CLM_OUTPUT_DEDUP_HASH_LIMIT_MB"

_HASH_DIGEST_SIZE: Final[int] = 16
_HASH_READ_CHUNK: Final[int] = 64 * 1024


def _resolve_hash_limit_bytes() -> int:
    """Resolve the byte threshold above which content hashing is skipped.

    Reads ``CLM_OUTPUT_DEDUP_HASH_LIMIT_MB`` (megabytes) from the
    environment, falling back to :data:`DEFAULT_HASH_LIMIT_MB`. A negative
    or non-numeric value logs a warning and uses the default.
    """
    raw = os.environ.get(_ENV_HASH_LIMIT_MB)
    if raw is None:
        return DEFAULT_HASH_LIMIT_MB * 1024 * 1024
    try:
        mb = int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; falling back to default %d MB",
            _ENV_HASH_LIMIT_MB,
            raw,
            DEFAULT_HASH_LIMIT_MB,
        )
        return DEFAULT_HASH_LIMIT_MB * 1024 * 1024
    if mb < 0:
        logger.warning(
            "Negative %s=%r; falling back to default %d MB",
            _ENV_HASH_LIMIT_MB,
            raw,
            DEFAULT_HASH_LIMIT_MB,
        )
        return DEFAULT_HASH_LIMIT_MB * 1024 * 1024
    return mb * 1024 * 1024


def _hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=_HASH_DIGEST_SIZE).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=_HASH_DIGEST_SIZE)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_image_path(source_path: Path) -> bool:
    """Return ``True`` iff this source is owned by :class:`ImageRegistry`.

    The new registry skips these paths so the existing ``image_collision``
    warning channel remains the sole reporter for image-path conflicts.
    Mirrors :func:`clm.core.image_registry.get_relative_img_path`'s detection
    rule (presence of an ``img`` segment in the path).
    """
    return "img" in source_path.parts


class WriteOutcome(Enum):
    """What the caller should do about an attempted write."""

    FIRST_WRITE = "first_write"
    """No prior entry for this output path; caller proceeds with the write."""

    DEDUP = "dedup"
    """A previous write produced byte-identical content; caller skips."""

    CONFLICT = "conflict"
    """A previous write produced different content; caller proceeds (last-
    writer-wins) and the caller should surface the conflict warning."""

    LARGE_FILE_COLLISION = "large_file_collision"
    """Either the previous or the new write is over the hash size limit;
    the caller proceeds (last-writer-wins) and the reporter emits a single
    summary at end-of-build instead of per-conflict warnings."""


@define
class OutputWriteEntry:
    """Cumulative state for one absolute output path."""

    output_path: Path
    content_hash: str
    """Hash of the *most recent* writer's content. Used for dedup checks
    against the next write. May change on every CONFLICT outcome."""

    first_writer_source: Path | None = None
    first_writer_hash: str = ""
    """Hash captured at the first write; never overwritten. Retained
    purely for diagnostics (the build reporter names both first and
    last writer's hashes in conflict records)."""

    last_writer_source: Path | None = None
    last_writer_hash: str = ""
    dedup_count: int = 0
    conflict_count: int = 0
    is_large_file: bool = False


@frozen
class WriteResult:
    """Return value of :meth:`OutputWriteRegistry.record_write`."""

    outcome: WriteOutcome
    entry: OutputWriteEntry


@define
class OutputWriteRegistry:
    """Per-build registry tracking output writes.

    The registry is keyed by absolute output path. For each path it records
    the content hash, the source of the first writer, and counts of subsequent
    identical (``dedup_count``) and differing (``conflict_count``) writes.
    Hashing is BLAKE2b-128, chosen for speed; the rare hash collision is not
    a correctness concern (the cost is a false-dedup-skip).

    Files larger than :data:`DEFAULT_HASH_LIMIT_MB` (configurable via the
    ``CLM_OUTPUT_DEDUP_HASH_LIMIT_MB`` environment variable) bypass hashing.
    Repeat writes to a large-file path are reported as a single
    :data:`WriteOutcome.LARGE_FILE_COLLISION` summary rather than per-path
    warnings.
    """

    _entries: dict[Path, OutputWriteEntry] = Factory(dict)
    _hash_limit_bytes: int = field(factory=_resolve_hash_limit_bytes)
    _large_file_collision_count: int = 0

    def record_write(
        self,
        output_path: Path,
        *,
        content: bytes | None = None,
        content_source: Path | None = None,
        source: Path | None = None,
    ) -> WriteResult:
        """Record an intended write and return what the caller should do.

        Exactly one of ``content`` or ``content_source`` must be supplied.
        ``output_path`` must be absolute — the dedup key needs a canonical
        form, and resolving relative paths here would hide caller bugs.

        Args:
            output_path: Absolute destination of the write.
            content: In-memory bytes that will be written.
            content_source: Source file whose bytes will be copied verbatim
                (avoids reading the file into memory just to hash it).
            source: Logical ``CourseFile`` source path used in conflict
                diagnostics. Optional — purely informational.
        """
        if not output_path.is_absolute():
            raise ValueError(f"output_path must be absolute: {output_path}")
        if (content is None) == (content_source is None):
            raise ValueError("provide exactly one of content= or content_source=")

        size = len(content) if content is not None else content_source.stat().st_size  # type: ignore[union-attr]
        is_large = size > self._hash_limit_bytes

        existing = self._entries.get(output_path)

        if is_large or (existing is not None and existing.is_large_file):
            return self._record_large_file_write(output_path, source, existing)

        new_hash = _hash_bytes(content) if content is not None else _hash_file(content_source)  # type: ignore[arg-type]

        if existing is None:
            entry = OutputWriteEntry(
                output_path=output_path,
                content_hash=new_hash,
                first_writer_source=source,
                first_writer_hash=new_hash,
                last_writer_source=source,
                last_writer_hash=new_hash,
            )
            self._entries[output_path] = entry
            return WriteResult(outcome=WriteOutcome.FIRST_WRITE, entry=entry)

        if existing.content_hash == new_hash:
            existing.dedup_count += 1
            existing.last_writer_source = source
            existing.last_writer_hash = new_hash
            return WriteResult(outcome=WriteOutcome.DEDUP, entry=existing)

        existing.conflict_count += 1
        existing.last_writer_source = source
        existing.last_writer_hash = new_hash
        existing.content_hash = new_hash
        return WriteResult(outcome=WriteOutcome.CONFLICT, entry=existing)

    def _record_large_file_write(
        self,
        output_path: Path,
        source: Path | None,
        existing: OutputWriteEntry | None,
    ) -> WriteResult:
        if existing is None:
            entry = OutputWriteEntry(
                output_path=output_path,
                content_hash="",
                first_writer_source=source,
                first_writer_hash="",
                last_writer_source=source,
                is_large_file=True,
            )
            self._entries[output_path] = entry
            return WriteResult(outcome=WriteOutcome.FIRST_WRITE, entry=entry)

        existing.is_large_file = True
        existing.last_writer_source = source
        existing.content_hash = ""
        existing.last_writer_hash = ""
        self._large_file_collision_count += 1
        return WriteResult(outcome=WriteOutcome.LARGE_FILE_COLLISION, entry=existing)

    def get(self, output_path: Path) -> OutputWriteEntry | None:
        return self._entries.get(output_path)

    @property
    def entries(self) -> dict[Path, OutputWriteEntry]:
        """Snapshot of all recorded entries (copy; safe to mutate)."""
        return dict(self._entries)

    @property
    def total_dedups(self) -> int:
        return sum(e.dedup_count for e in self._entries.values())

    @property
    def total_conflicts(self) -> int:
        return sum(e.conflict_count for e in self._entries.values())

    @property
    def conflict_entries(self) -> list[OutputWriteEntry]:
        """Entries that have at least one recorded conflict."""
        return [e for e in self._entries.values() if e.conflict_count > 0]

    @property
    def large_file_collision_count(self) -> int:
        return self._large_file_collision_count

    @property
    def hash_limit_bytes(self) -> int:
        return self._hash_limit_bytes

    def clear(self) -> None:
        self._entries.clear()
        self._large_file_collision_count = 0
