"""Host-side cassette staging maintenance (Phase 8 S3, #802).

The pre-build orphan sweep and the post-build mitmproxy merge, relocated
from ``Course`` — every live entry point was already CLI-layer (the build's
pre-stage/finally hooks and watch mode), and the machinery they drive lives
in this package. Both functions take the canonical cassette paths directly
(``Course.http_replay_canonical_paths()``), so they carry no course
dependency.
"""

import logging
from collections.abc import Iterable
from pathlib import Path

from clm.core.http_replay_trace import get_writer
from clm.infrastructure.http_replay_mitm.http_replay_cassette import (
    CassettePaths,
    merge_staging_into_canonical,
    write_completion_marker,
)

logger = logging.getLogger(__name__)


def merge_mitmproxy_cassette_staging(
    canonical_paths: Iterable[Path],
    build_id: str | None = None,
    *,
    mode: str | None = None,
) -> int:
    """Fold per-target staging files written by the mitmproxy transport.

    The replay proxy (issue #165) records into per-(topic,
    language,kind) ``<cassette>.staging-mitm-<build_id>`` files beside
    each canonical cassette. This runs **after** the proxy stops (from
    the build's ``finally``) to fold them into their canonicals via the
    shared dedup/merge path.

    Reaching this function *is* the build-completion signal (the build's
    ``finally`` ran), so for each canonical we write the ``.completed``
    marker for **this build's** staging file (``build_id``) — the marker
    is what tells the merge a staging file holds a complete recording
    session. mitmproxy's ``done`` hook is unreliable on a Windows
    ``CTRL_BREAK`` shutdown, so the host owns this signal. A force-killed
    build never reaches here, so its staging stays markerless and is
    discarded by the next build's pre-build sweep (issue #115).

    ``sweep_orphans=False``: markerless staging (older builds, or a
    concurrent build still recording) is left untouched. A no-op for
    builds that did not use the transport.

    ``mode`` is the CLM http-replay mode; ``refresh`` folds with
    ``overwrite_existing=True`` so a re-recorded interaction supersedes the
    stale canonical entry (issue #165 P3), matching vcrpy ``all`` semantics.

    Returns the number of staging files folded into canonical.
    """
    canonical_set = set(canonical_paths)
    if not canonical_set:
        return 0

    overwrite_existing = mode == "refresh"
    folded = 0
    for canonical in sorted(canonical_set):
        if not canonical.parent.is_dir():
            continue
        # Mark this build's staging file complete so the merge folds it.
        if build_id:
            staging = canonical.parent / f"{canonical.name}.staging-mitm-{build_id}"
            if staging.is_file():
                write_completion_marker(CassettePaths(canonical=canonical, staging=staging))
        if not any(canonical.parent.glob(f"{canonical.name}.staging-*")):
            continue
        synthetic = canonical.parent / f"{canonical.name}.staging-mitm-merge"
        try:
            merged = merge_staging_into_canonical(
                CassettePaths(canonical=canonical, staging=synthetic),
                sweep_orphans=False,
                overwrite_existing=overwrite_existing,
                # The replay proxy records a per-request response
                # *sequence* (a non-deterministic endpoint answers an
                # identical request differently on successive calls); fold it
                # order-preserving so a downstream request that embedded the
                # later response still replay-matches. Only the pre-build
                # orphan sweep keeps the deduped fold (preserve_sequence
                # defaults to False there).
                preserve_sequence=True,
            )
        except Exception as exc:  # noqa: BLE001 — never mask the build result
            logger.warning(
                f"Post-build mitmproxy cassette merge failed for "
                f"'{canonical}' ({type(exc).__name__}: {exc})."
            )
            continue
        if merged:
            logger.info(f"Merged {merged} mitmproxy staging cassette(s) into '{canonical}'.")
            folded += merged
    return folded


def sweep_orphan_cassette_staging_files(canonical_paths: Iterable[Path]) -> int:
    """Merge any orphan HTTP-replay staging cassettes into canonical.

    For each *unique* canonical cassette location with at least one
    ``*.http-cassette.yaml.staging-*`` sibling, invokes
    :func:`merge_staging_into_canonical` with ``sweep_orphans=True``.
    That helper takes the cross-process file lock, folds completed
    sibling stagings (those with a ``.completed`` marker) into
    canonical and **discards** markerless stagings — partial chains
    from previously-aborted recording sessions whose chain-closing
    request never landed on disk (issue #115). Without the discard,
    a markerless chain-opener with a body that depends on the
    chain-closer's stored response would poison the canonical
    cassette permanently (dedup is first-seen-wins).

    Running this *before* payload construction prevents a stale
    staging file from being enumerated by
    ``ProcessNotebookOperation.compute_other_files`` and then
    deleted by a concurrent worker's post-execution merge, which
    used to surface as a ``FileNotFoundError`` during base64
    encoding. Defense-in-depth: ``compute_other_files`` also filters
    ``*.staging-*`` via ``is_ignored_file_for_output`` so a *new*
    orphan appearing mid-build can't sneak into the payload either.

    The discriminator between "decisive discard" here and
    "conservative leave-alone" in the post-execution worker sweep
    is the ``sweep_orphans`` flag: by contract this function runs
    single-threaded before any worker starts, so every staging file
    present must be from a previous build — no concurrency, no risk
    of clobbering a still-recording worker.

    Returns:
        Number of canonical cassettes for which a merge ran (i.e.,
        had at least one staging file present). Zero when nothing
        needed sweeping or when ``http-replay`` is not used.
    """
    canonical_set = set(canonical_paths)
    if not canonical_set:
        return 0

    _host_writer = get_writer("host")
    _host_writer.emit(
        "cassette.sweep.start",
        {"n_canonical_paths": len(canonical_set)},
    )

    swept = 0
    for canonical in sorted(canonical_set):
        staging_glob = f"{canonical.name}.staging-*"
        if not canonical.parent.is_dir():
            continue
        if not any(canonical.parent.glob(staging_glob)):
            continue
        # The ``staging`` field is irrelevant for the sweep call —
        # ``merge_staging_into_canonical`` globs for every staging
        # file in the canonical's parent dir, including orphans this
        # worker did not produce. Pass a synthetic name to satisfy
        # the dataclass.
        synthetic = canonical.parent / f"{canonical.name}.staging-sweep"
        try:
            merged = merge_staging_into_canonical(
                CassettePaths(canonical=canonical, staging=synthetic),
                sweep_orphans=True,
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                f"Pre-build orphan staging sweep failed for "
                f"'{canonical}' ({type(exc).__name__}: {exc}); "
                f"continuing — the worker-side sweep will retry."
            )
            continue
        if merged:
            logger.info(f"Merged {merged} orphan staging cassette(s) into '{canonical}'.")
            swept += 1
    _host_writer.emit(
        "cassette.sweep.end",
        {"n_canonical_paths": len(canonical_set), "swept": swept},
    )
    return swept
