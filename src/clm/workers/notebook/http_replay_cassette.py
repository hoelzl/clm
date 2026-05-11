"""HTTP-replay cassette persistence helpers.

The notebook kernel records HTTP interactions into a per-worker *staging*
file that lives in the same directory as the topic's canonical cassette.
This module owns the logic that

1. resolves the canonical and per-worker staging paths,
2. seeds the staging file from the canonical cassette so already-recorded
   interactions can be replayed without hitting the network,
3. merges the staging file (plus any orphan staging files left behind by
   previously-killed workers) into the canonical cassette under a
   cross-process file lock, deduplicating by request fingerprint,
4. writes the merged cassette atomically.

The merge step runs in a ``finally`` block so it executes even when the
notebook raised — vcrpy's eager-save patch in the bootstrap means partial
recordings are already on disk by the time the failure propagates.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_STAGING_SUFFIX = ".staging-"
_LOCK_SUFFIX = ".lock"
_MERGE_LOCK_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class CassettePaths:
    """Resolved locations for the canonical cassette and this worker's staging file.

    ``canonical`` is the path the cassette ends up at after a successful
    merge — this is what authors commit and what subsequent builds replay
    from. ``staging`` is unique per worker invocation so that concurrent
    workers (e.g., German and English builds of the same notebook) do not
    write to the same file.
    """

    canonical: Path
    staging: Path


def resolve_paths(target_dir: Path, cassette_name: str) -> CassettePaths:
    """Compute the canonical and staging paths for a topic.

    Args:
        target_dir: Directory the kernel can write to that maps to the
            source tree (host source-topic dir in direct mode, container
            source mount in Docker mode).
        cassette_name: Cassette name relative to ``target_dir``, possibly
            including a ``_cassettes/`` prefix (e.g.,
            ``_cassettes/slides.http-cassette.yaml``).
    """
    canonical = target_dir / cassette_name
    unique = f"{os.getpid()}-{uuid.uuid4().hex}"
    staging = canonical.parent / f"{canonical.name}{_STAGING_SUFFIX}{unique}"
    return CassettePaths(canonical=canonical, staging=staging)


def seed_staging_from_canonical(paths: CassettePaths) -> None:
    """Sweep orphan staging files into canonical, then seed staging from canonical.

    Without the orphan sweep, every failed build (e.g., one that ran
    past the build-level timeout and was force-killed before its merge
    finally block ran) would leave its partial recordings stranded in a
    ``.staging-*`` file that nobody ever incorporates into the canonical
    cassette — and the next build would re-record those interactions
    over the network instead of replaying them. Sweeping pre-execution
    means canonical converges across a sequence of failed builds.

    The seed copy after the sweep gives vcrpy a starting point inside
    the kernel: when the bootstrap calls ``Cassette.load``, it reads
    every interaction in canonical (now including freshly-merged
    orphans) so the notebook can replay them offline.
    """
    paths.staging.parent.mkdir(parents=True, exist_ok=True)
    # Sweep orphans into canonical so this worker's seed picks them up.
    # Failures here are non-fatal — we still seed from whatever
    # canonical currently is, and the post-execution merge will retry.
    try:
        merge_staging_into_canonical(paths)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            f"Pre-execution orphan sweep failed for '{paths.canonical}' "
            f"({type(exc).__name__}: {exc}); seeding from canonical as-is."
        )
    if paths.canonical.exists():
        shutil.copy2(paths.canonical, paths.staging)


def merge_staging_into_canonical(paths: CassettePaths) -> int:
    """Merge this worker's staging file and any orphan staging files into canonical.

    Acquires a cross-process file lock at ``<canonical>.lock`` for the
    duration of the merge so two workers cannot corrupt the canonical
    cassette by writing concurrently. Loads the existing canonical
    cassette (if any), folds in interactions from every staging file in
    the canonical's directory (this worker's plus orphans from
    previously-killed workers), deduplicates by request fingerprint, and
    writes the merged cassette atomically via :func:`os.replace`. The
    staging files are deleted after a successful merge.

    Returns:
        Number of staging files merged. Zero is returned for replay-only
        topics where no recording happened.
    """
    # vcrpy is an optional extra; importing here keeps the module
    # importable for tests that exercise pure path resolution without
    # requiring the [replay] install.
    from filelock import FileLock, Timeout
    from vcr.persisters.filesystem import FilesystemPersister
    from vcr.serialize import serialize as vcr_serialize
    from vcr.serializers import yamlserializer

    canonical = paths.canonical
    lock_path = canonical.parent / f"{canonical.name}{_LOCK_SUFFIX}"
    canonical.parent.mkdir(parents=True, exist_ok=True)

    try:
        with FileLock(str(lock_path), timeout=_MERGE_LOCK_TIMEOUT_SECONDS):
            staging_glob = f"{canonical.name}{_STAGING_SUFFIX}*"
            staging_files = sorted(canonical.parent.glob(staging_glob))
            if not staging_files:
                return 0

            canonical_requests: list = []
            canonical_responses: list = []
            if canonical.exists():
                try:
                    canonical_requests, canonical_responses = FilesystemPersister.load_cassette(
                        canonical, serializer=yamlserializer
                    )
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.warning(
                        f"Could not load existing canonical cassette '{canonical}' "
                        f"before merge ({type(exc).__name__}: {exc}); treating as empty."
                    )

            seen_keys = {_dedup_key(req) for req in canonical_requests}
            merged_requests = list(canonical_requests)
            merged_responses = list(canonical_responses)

            for staging_path in staging_files:
                try:
                    staging_requests, staging_responses = FilesystemPersister.load_cassette(
                        staging_path, serializer=yamlserializer
                    )
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.warning(
                        f"Could not load staging cassette '{staging_path}' "
                        f"({type(exc).__name__}: {exc}); skipping."
                    )
                    continue
                for request, response in zip(staging_requests, staging_responses, strict=False):
                    key = _dedup_key(request)
                    if key in seen_keys:
                        continue
                    merged_requests.append(request)
                    merged_responses.append(response)
                    seen_keys.add(key)

            payload = vcr_serialize(
                {"requests": merged_requests, "responses": merged_responses},
                yamlserializer,
            )
            _atomic_write_text(canonical, payload)

            for staging_path in staging_files:
                try:
                    staging_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning(
                        f"Could not delete merged staging cassette '{staging_path}': {exc}"
                    )

            return len(staging_files)
    except Timeout:
        logger.error(
            f"Timed out after {_MERGE_LOCK_TIMEOUT_SECONDS:.0f}s waiting for cassette "
            f"merge lock at '{lock_path}'. Cassette '{canonical}' was not updated. "
            f"Staging files remain on disk and will be picked up by the next build."
        )
        return 0


def _dedup_key(request) -> tuple:
    """Build a hashable fingerprint for a vcrpy request.

    The dedup key is intentionally narrower than vcrpy's default matcher
    chain — we only care that two ``append`` calls referring to the same
    HTTP call do not produce two stored interactions, not that we
    replicate every nuance of vcrpy's matching semantics. Method, URI,
    and body cover the common cases (GET with query strings, POST with
    a body, REST endpoints with different paths) for teaching material.
    """
    body = getattr(request, "body", None)
    if isinstance(body, bytes):
        body_key: object = body
    elif body is None:
        body_key = b""
    else:
        body_key = str(body).encode("utf-8", errors="replace")
    return (
        getattr(request, "method", ""),
        getattr(request, "uri", ""),
        body_key,
    )


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically.

    Writes to a temporary file in the same directory and then calls
    :func:`os.replace`, which is atomic on POSIX and Windows. This
    prevents a concurrent reader from observing a half-written cassette.
    """
    tmp = target.parent / f"{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
