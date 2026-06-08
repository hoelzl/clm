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


def _trace(event: str, data: dict | None = None) -> None:
    """Emit a cassette-stream trace event (no-op when tracing is off)."""
    from clm.workers.notebook.http_replay_trace import get_writer

    get_writer("host").emit(event, data)


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
    seeded_bytes = 0
    if paths.canonical.exists():
        shutil.copy2(paths.canonical, paths.staging)
        try:
            seeded_bytes = paths.staging.stat().st_size
        except OSError:
            seeded_bytes = 0
    _trace(
        "cassette.seed",
        {
            "canonical": str(paths.canonical),
            "staging": str(paths.staging),
            "canonical_existed": paths.canonical.exists() or seeded_bytes > 0,
            "seeded_bytes": seeded_bytes,
        },
    )


def merge_staging_into_canonical(
    paths: CassettePaths,
    *,
    sweep_orphans: bool = False,
    overwrite_existing: bool = False,
    preserve_sequence: bool = False,
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
        overwrite_existing: Mode-aware merge for ``refresh`` (vcrpy ``all``).
            When ``True`` a staging interaction **wins** over a canonical
            interaction with the same fingerprint (last-seen within staging
            wins, so a freshly recorded response replaces a stale one) while
            preserving canonical position for minimal diff churn. When
            ``False`` (default — ``new-episodes`` / ``once`` / ``replay`` and
            every pre-build sweep) the long-standing first-seen-wins behavior
            is used, byte-identical to before this parameter existed. This is
            the producer-agnostic fix for the refresh-overwrite gap (issue
            #165 P3) and benefits both the vcrpy and mitmproxy transports.
        preserve_sequence: Sequence-aware fold for the **mitmproxy** transport
            only (the in-kernel vcrpy path leaves this ``False``). When ``True``
            the fold keeps every distinct ``(request, response)`` interaction in
            recorded order — deduping only an exact ``(request, response)`` pair,
            never by request alone — so a per-request *response sequence* (the
            same request answered differently on successive calls by a
            non-deterministic endpoint) survives into canonical and replays
            intact. With ``overwrite_existing`` (refresh) the build's staging
            *is* the complete recording and replaces canonical wholesale;
            otherwise (new-episodes) the staging sequence is appended after the
            existing canonical entries.

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
            _trace(
                "cassette.merge.start",
                {
                    "canonical": str(canonical),
                    "n_staging_files": len(staging_files),
                    "sweep_orphans": sweep_orphans,
                },
            )
            if not staging_files:
                _trace("cassette.merge.end", {"canonical": str(canonical), "folded": 0})
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

            # ``preserve_sequence`` (mitmproxy transport) keeps every distinct
            # (request, response) interaction in recorded order, deduping only
            # exact pairs — so a per-request response *sequence* survives.
            # ``overwrite_existing`` (refresh) then means "this build's staging
            # is the complete recording" → replace canonical; otherwise append.
            if preserve_sequence:
                if overwrite_existing:
                    merged_requests = []
                    merged_responses = []
                    seen_pairs: set = set()
                else:
                    merged_requests = list(canonical_requests)
                    merged_responses = list(canonical_responses)
                    seen_pairs = {
                        (_dedup_key(req), _response_body_bytes(resp))
                        for req, resp in zip(canonical_requests, canonical_responses, strict=False)
                    }
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
                        _trace(
                            "cassette.merge.decision",
                            {
                                "staging": str(staging_path),
                                "decision": "load_failed",
                                "exc_type": type(exc).__name__,
                            },
                        )
                        continue
                    folded_from_this = 0
                    duplicates_in_this = 0
                    for request, response in zip(staging_requests, staging_responses, strict=False):
                        pair_key = (_dedup_key(request), _response_body_bytes(response))
                        if pair_key in seen_pairs:
                            duplicates_in_this += 1
                            continue
                        merged_requests.append(request)
                        merged_responses.append(response)
                        seen_pairs.add(pair_key)
                        folded_from_this += 1
                    _trace(
                        "cassette.merge.decision",
                        {
                            "staging": str(staging_path),
                            "decision": "folded_sequence",
                            "marker_present": True,
                            "interactions_loaded": len(staging_requests),
                            "interactions_folded": folded_from_this,
                            "interactions_deduped": duplicates_in_this,
                        },
                    )
            # ``overwrite_existing`` lets a staging interaction replace a
            # canonical one with the same fingerprint (refresh/``all``); the
            # default first-seen-wins path is kept verbatim so a no-op build's
            # cassette stays byte-identical to before this parameter existed.
            elif overwrite_existing:
                # Last-seen within staging wins (a freshly recorded response
                # supersedes an earlier/seeded one); canonical entries are
                # replaced in place to keep the diff minimal, and genuinely new
                # staging entries are appended in first-seen order.
                staging_value: dict[tuple, tuple] = {}
                staging_order: list[tuple] = []
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
                        _trace(
                            "cassette.merge.decision",
                            {
                                "staging": str(staging_path),
                                "decision": "load_failed",
                                "exc_type": type(exc).__name__,
                            },
                        )
                        continue
                    folded_from_this = 0
                    duplicates_in_this = 0
                    for request, response in zip(staging_requests, staging_responses, strict=False):
                        key = _dedup_key(request)
                        if key in staging_value:
                            duplicates_in_this += 1
                        else:
                            staging_order.append(key)
                            folded_from_this += 1
                        staging_value[key] = (request, response)  # last-seen wins
                    _trace(
                        "cassette.merge.decision",
                        {
                            "staging": str(staging_path),
                            "decision": "folded_overwrite",
                            "marker_present": True,
                            "interactions_loaded": len(staging_requests),
                            "interactions_folded": folded_from_this,
                            "interactions_deduped": duplicates_in_this,
                        },
                    )
                merged_requests = []
                merged_responses = []
                placed: set = set()
                for request, response in zip(canonical_requests, canonical_responses, strict=False):
                    key = _dedup_key(request)
                    if key in staging_value:
                        new_request, new_response = staging_value[key]
                        merged_requests.append(new_request)
                        merged_responses.append(new_response)
                        placed.add(key)
                    else:
                        merged_requests.append(request)
                        merged_responses.append(response)
                for key in staging_order:
                    if key not in placed:
                        new_request, new_response = staging_value[key]
                        merged_requests.append(new_request)
                        merged_responses.append(new_response)
            else:
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
                        _trace(
                            "cassette.merge.decision",
                            {
                                "staging": str(staging_path),
                                "decision": "load_failed",
                                "exc_type": type(exc).__name__,
                            },
                        )
                        continue
                    folded_from_this = 0
                    duplicates_in_this = 0
                    for request, response in zip(staging_requests, staging_responses, strict=False):
                        key = _dedup_key(request)
                        if key in seen_keys:
                            duplicates_in_this += 1
                            continue
                        merged_requests.append(request)
                        merged_responses.append(response)
                        seen_keys.add(key)
                        folded_from_this += 1
                    _trace(
                        "cassette.merge.decision",
                        {
                            "staging": str(staging_path),
                            "decision": "folded",
                            "marker_present": True,
                            "interactions_loaded": len(staging_requests),
                            "interactions_folded": folded_from_this,
                            "interactions_deduped": duplicates_in_this,
                        },
                    )

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
                    _trace(
                        "cassette.merge.decision",
                        {
                            "staging": str(staging_path),
                            "decision": "discarded_orphan",
                            "marker_present": False,
                        },
                    )
                    _delete_quietly(staging_path)
            else:
                for staging_path in markerless:
                    _trace(
                        "cassette.merge.decision",
                        {
                            "staging": str(staging_path),
                            "decision": "skipped_concurrent",
                            "marker_present": False,
                        },
                    )

            _trace(
                "cassette.merge.end",
                {
                    "canonical": str(canonical),
                    "folded": len(markered),
                    "final_interaction_count": len(merged_requests),
                },
            )
            return len(markered)
    except Timeout:
        logger.error(
            f"Timed out after {_MERGE_LOCK_TIMEOUT_SECONDS:.0f}s waiting for cassette "
            f"merge lock at '{lock_path}'. Cassette '{canonical}' was not updated. "
            f"Staging files remain on disk and will be picked up by the next build."
        )
        _trace(
            "cassette.merge.lock_timeout",
            {"canonical": str(canonical), "lock_timeout_seconds": _MERGE_LOCK_TIMEOUT_SECONDS},
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
        _trace(
            "cassette.completion_marker.write",
            {"staging": str(paths.staging), "marker": str(target)},
        )
    except OSError as exc:
        logger.warning(
            f"Could not write cassette completion marker '{target}' "
            f"({type(exc).__name__}: {exc}); this worker's recordings "
            f"will be treated as aborted and discarded on the next build."
        )
        _trace(
            "cassette.completion_marker.error",
            {
                "staging": str(paths.staging),
                "marker": str(target),
                "exc_type": type(exc).__name__,
                "exc_msg": str(exc),
            },
        )


def _dedup_key(request) -> tuple:
    """Build a hashable fingerprint for a vcrpy request.

    The dedup key is intentionally narrower than vcrpy's default matcher
    chain — we only care that two ``append`` calls referring to the same
    HTTP call do not produce two stored interactions, not that we
    replicate every nuance of vcrpy's matching semantics. Method, URI,
    and body cover the common cases (GET with query strings, POST with
    a body, REST endpoints with different paths) for teaching material.

    Body extraction handles ``bytes``/``str``/``None`` plus
    ``BytesIO``/file-like objects. Streaming bodies are a real-world
    case: LangSmith's compressed-multipart upload (``api.smith.langchain.com``)
    passes a ``BytesIO`` to ``requests.Session.post``, which vcrpy stores
    as-is and YAML serializes via ``!!python/object/new:_io.BytesIO``.
    Without reading the stream into bytes here, ``str(BytesIO_instance)``
    would key on the object's repr (``<_io.BytesIO object at 0x...>``) —
    different across instances even for identical payloads — and merge
    would treat every loaded entry as a fresh interaction, growing the
    canonical cassette by N entries on every build.
    """
    body = getattr(request, "body", None)
    body_key = _body_to_dedup_bytes(body)
    return (
        getattr(request, "method", ""),
        getattr(request, "uri", ""),
        body_key,
    )


def _body_to_dedup_bytes(body) -> bytes:
    """Coerce a request body to stable bytes for dedup fingerprinting."""
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8", errors="replace")
    # Stream-like (BytesIO, file objects): read into bytes, rewind so the
    # body remains consumable by anything that reads it after us.
    read = getattr(body, "read", None)
    if callable(read):
        try:
            data = read()
        except Exception:  # noqa: BLE001 — defensive: never crash dedup
            return repr(body).encode("utf-8", errors="replace")
        seek = getattr(body, "seek", None)
        if callable(seek):
            try:
                seek(0)
            except Exception:  # noqa: BLE001 — best-effort rewind
                pass
        if isinstance(data, bytes):
            return data
        if isinstance(data, bytearray):
            return bytes(data)
        return str(data).encode("utf-8", errors="replace")
    return repr(body).encode("utf-8", errors="replace")


def _response_body_bytes(response) -> bytes:
    """Coerce a recorded vcr response's body to stable bytes.

    Pairs with :func:`_dedup_key` to form the exact-``(request, response)``
    dedup key the sequence-preserving mitmproxy merge uses: a response loaded
    from a cassette is a dict with ``body.string`` (vcrpy's stub format), so two
    *different* responses to the same request hash differently and are kept as
    separate ordered interactions, while a byte-identical re-record collapses.
    """
    body = (response or {}).get("body") if isinstance(response, dict) else None
    raw = body.get("string", b"") if isinstance(body, dict) else b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    return str(raw).encode("utf-8", errors="replace")


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically.

    Writes to a temporary file in the same directory and then calls
    :func:`os.replace`, which is atomic on POSIX and Windows. This
    prevents a concurrent reader from observing a half-written cassette.

    ``newline="\\n"`` is required: without it ``Path.write_text`` applies
    the platform default (``os.linesep``), so Windows writes ``\\r\\n``.
    Cassettes committed via a repository with ``* text=auto eol=lf`` in
    ``.gitattributes`` are then rewritten LF→CRLF on every build and
    CRLF→LF on every subsequent ``git checkout``/``restore``, producing
    spurious "modified" rows in ``git status`` and (more importantly)
    perturbing the cassette bytes between builds.
    """
    tmp = target.parent / f"{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
