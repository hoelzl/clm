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

import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STAGING_SUFFIX = ".staging-"
_LOCK_SUFFIX = ".lock"
_COMPLETION_MARKER_SUFFIX = ".completed"
_COMPLETION_MARKER_SCHEMA = 1
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
    """Seed this worker's staging file from the canonical cassette.

    Orphan staging files from previously-killed builds are swept once
    per build in :meth:`clm.core.course.Course.process_all` (and
    :meth:`clm.core.course.Course.process_file`) via
    ``_sweep_orphan_cassette_staging_files``, *before* any worker
    starts — so by the time this function runs, the canonical cassette
    already includes any recoverable orphan interactions. We just need
    to give vcrpy a starting point: copy canonical (if it exists) into
    this worker's per-invocation staging file so the kernel can replay
    recorded interactions offline.

    An earlier version of this function also ran the merge sweep
    here, but that raced with concurrent workers — Worker B's seed
    would unlink Worker A's still-active staging file before A's
    kernel had loaded it, causing
    ``CannotOverwriteExistingCassetteException`` on the first
    request in replay mode (issue #86).
    """
    paths.staging.parent.mkdir(parents=True, exist_ok=True)
    if paths.canonical.exists():
        shutil.copy2(paths.canonical, paths.staging)


def merge_staging_into_canonical(
    paths: CassettePaths,
    *,
    sweep_orphans: bool = False,
) -> int:
    """Merge per-worker staging files into the canonical cassette.

    Acquires a cross-process file lock at ``<canonical>.lock`` for the
    duration of the merge so two workers cannot corrupt the canonical
    cassette by writing concurrently. The per-file action depends on
    whether the staging file carries a completion marker (see
    :func:`write_completion_marker`) and on the ``sweep_orphans`` flag:

    +-----------------+-----------------+----------------------------------+
    | Marker present? | ``sweep_orphans``| Action                          |
    +=================+=================+==================================+
    | yes             | any             | Fold entries; delete staging + marker. |
    +-----------------+-----------------+----------------------------------+
    | no              | ``True``        | Discard entries; delete staging. |
    +-----------------+-----------------+----------------------------------+
    | no              | ``False``       | Leave alone (concurrent worker?).|
    +-----------------+-----------------+----------------------------------+

    Folded entries are deduplicated against canonical and against each
    other by request fingerprint (:func:`_dedup_key`). The merged
    cassette is written atomically via :func:`os.replace`.

    Args:
        paths: Canonical + this worker's staging location. The ``staging``
            field is only relevant for naming; the merge globs every
            ``*.staging-*`` sibling of the canonical regardless.
        sweep_orphans: When ``True`` (pre-build invocation from
            :meth:`clm.core.course.Course._sweep_orphan_cassette_staging_files`),
            markerless staging files are treated as confirmed orphans from
            aborted previous builds: their entries are discarded and the
            staging files are deleted. When ``False`` (default,
            per-worker post-execution invocation), markerless staging
            files are left untouched — they may belong to a concurrent
            worker that hasn't completed yet.

    Returns:
        Number of staging files folded into the canonical (markered
        files only). Discarded markerless files are not counted; the
        caller can detect "had work to do but it was all orphan" by
        observing remaining files on disk.
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
            all_staging = sorted(canonical.parent.glob(staging_glob))
            # Marker files match the staging glob too (they end in
            # ``.completed``); strip them from the staging set so the
            # marker presence check is the *only* discriminator.
            staging_files = [
                p for p in all_staging if not p.name.endswith(_COMPLETION_MARKER_SUFFIX)
            ]
            if not staging_files:
                return 0

            markered: list[Path] = []
            markerless: list[Path] = []
            for staging_path in staging_files:
                if has_completion_marker(staging_path):
                    markered.append(staging_path)
                else:
                    markerless.append(staging_path)

            # Per-worker invocation: markerless staging files belong to
            # concurrent workers or aborted sessions. Either way we
            # don't touch them — the next build's pre-build sweep
            # decides their fate when there's no concurrency to worry
            # about. Logged at DEBUG to keep ordinary builds quiet but
            # diagnosable when concurrency-test triage needs it.
            if not sweep_orphans:
                for staging_path in markerless:
                    logger.debug(
                        f"Skipping markerless staging cassette '{staging_path}' "
                        f"(concurrent worker still recording, or session aborted; "
                        f"next pre-build sweep will decide)."
                    )

            if not markered and (not sweep_orphans or not markerless):
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

            for staging_path in markered:
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

            # Only rewrite canonical if we actually folded markered
            # work in. Pre-build sweeps that find only markerless
            # orphans must not touch canonical — discarding does not
            # change its content.
            if markered:
                payload = vcr_serialize(
                    {"requests": merged_requests, "responses": merged_responses},
                    yamlserializer,
                )
                _atomic_write_text(canonical, payload)

            for staging_path in markered:
                _delete_quietly(staging_path)
                _delete_quietly(marker_path(staging_path))

            if sweep_orphans:
                for staging_path in markerless:
                    logger.info(
                        f"Discarded orphan staging cassette '{staging_path}' "
                        f"(no completion marker — aborted previous-build session)."
                    )
                    _delete_quietly(staging_path)

            return len(markered)
    except Timeout:
        logger.error(
            f"Timed out after {_MERGE_LOCK_TIMEOUT_SECONDS:.0f}s waiting for cassette "
            f"merge lock at '{lock_path}'. Cassette '{canonical}' was not updated. "
            f"Staging files remain on disk and will be picked up by the next build."
        )
        return 0


def _delete_quietly(path: Path) -> None:
    """Best-effort delete: ignore ``FileNotFoundError``, log other ``OSError``s.

    Used during merge cleanup where we don't want one stuck file (e.g.,
    Windows antivirus holding a handle for a beat) to surface as a
    user-visible error — the worst that happens is the file gets
    cleaned up by the next sweep instead of this one.
    """
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"Could not delete '{path}': {exc}")


def marker_path(staging: Path) -> Path:
    """Return the completion-marker path that sits beside ``staging``.

    The marker is a small JSON file written by the host process *after*
    a worker's notebook execution returned cleanly and *before* the
    post-execution merge runs (see issue #115). Its existence — not its
    content — is the signal that the staging file holds a complete
    recording session whose entries are safe to fold into the canonical
    cassette. Markerless staging files belong to aborted sessions or
    still-running concurrent workers and are handled differently by
    :func:`merge_staging_into_canonical`.
    """
    return staging.parent / f"{staging.name}{_COMPLETION_MARKER_SUFFIX}"


def has_completion_marker(staging: Path) -> bool:
    """Return ``True`` when the completion marker sibling of ``staging`` exists."""
    return marker_path(staging).is_file()


def write_completion_marker(paths: CassettePaths) -> None:
    """Atomically write the completion marker for this worker's staging file.

    Called by the host process from the success path of
    :meth:`NotebookProcessor._create_using_nbconvert` — after the
    kernel returned cleanly, before the finally-block triggers the
    merge. The marker tells the merge that this staging file is safe
    to fold into the canonical cassette; without it, the staging file
    is treated as a partial recording (kernel killed, build aborted,
    chain-closer cell never ran) and its entries are discarded by
    the pre-build sweep on the next build.

    Idempotent: re-writing the marker on a retry path is safe. Atomic:
    written via :func:`_atomic_write_text` so a partially-written
    marker is never observable. Best-effort: an :class:`OSError` while
    writing degrades the session to "aborted" semantics (recordings
    lost on next build), which is correctness-preserving — we log a
    warning and do not raise.
    """
    target = marker_path(paths.staging)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": _COMPLETION_MARKER_SCHEMA,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "host_pid": os.getpid(),
    }
    try:
        _atomic_write_text(target, json.dumps(payload, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning(
            f"Could not write cassette completion marker '{target}' "
            f"({type(exc).__name__}: {exc}); this worker's recordings "
            f"will be treated as aborted and discarded on the next build."
        )


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
