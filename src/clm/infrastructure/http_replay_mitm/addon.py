"""mitmproxy addon implementing CLM's record/replay semantics.

This module is loaded by ``mitmdump`` (not by the CLM workers). It is
invoked via ``mitmdump --scripts <this-file>`` and reads its config from
mitmproxy options the manager sets on the command line.

Storage is the **vcrpy v1 YAML cassette schema** (issue #165, P1) so that
committed course cassettes, ``clm cassette doctor``,
``strip_cassette_hosts.py`` and the host-side
``merge_staging_into_canonical`` all keep working unchanged — a
mitmproxy-recorded cassette is byte-identical to a vcrpy-recorded one for
the same HTTP exchange. The format conversion lives in the pure
:mod:`clm.infrastructure.http_replay_mitm.cassette_format` module.

**Request→cassette routing (P2):** a single shared proxy serves a whole
build, so each worker tags its outgoing requests with an ``X-CLM-Cassette``
header (the absolute canonical cassette path, injected by the kernel
bootstrap). This addon demuxes flows by that tag into one
``*.staging-mitm-<build>`` file per canonical cassette (vcrpy-YAML), writes
a ``.completed`` marker on clean shutdown, and the host folds each staging
file into its canonical via the existing ``merge_staging_into_canonical``.
The tag header is stripped before recording or forwarding upstream.

Untagged traffic (a client stack the tag bootstrap does not patch, or a
kernel that somehow bypassed it) falls back to the single
``clm_cassette_path`` catch-all so strict ``replay`` mode still returns a
non-retryable 404 instead of escaping to the network — and triggers a
once-per-build :data:`UNTAGGED_FLOW_SENTINEL` warning that the manager
relays into the build log, because catch-all routing means the topic's
canonical cassette is silently out of the loop.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from mitmproxy import ctx, http

logger = logging.getLogger("clm.http_replay_mitm.addon")

# The cassette format bridge is pure (vcr + stdlib only). Inside CLM's own
# venv it imports as a package submodule; inside the isolated mitmdump
# interpreter (``uv tool install mitmproxy --with vcrpy``) the ``clm``
# package is absent, so we fall back to a bare path import of the sibling
# module. If vcrpy itself is missing from the mitmdump environment, both
# imports fail and we surface a loud, actionable error at startup.
_CF_IMPORT_ERROR: Exception | None = None
try:  # CLM venv
    from clm.infrastructure.http_replay_mitm import cassette_format as cf
except ImportError:  # mitmdump interpreter — import the sibling by path
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import cassette_format as cf  # type: ignore[import-not-found,no-redef]
    except ImportError as exc:  # vcrpy missing from the mitmdump env
        cf = None  # type: ignore[assignment]
        _CF_IMPORT_ERROR = exc

# The proxy-flow trace writer (issue #165 P5 forensic harness). Pure stdlib, so
# it always imports — both as a CLM submodule and by bare path inside the
# mitmdump interpreter. Off unless the manager passes ``clm_trace_dir``.
try:  # CLM venv
    from clm.infrastructure.http_replay_mitm import trace_log as _trace_log
except ImportError:  # mitmdump interpreter — import the sibling by path
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import trace_log as _trace_log  # type: ignore[import-not-found,no-redef]
    except ImportError:  # pragma: no cover — pure stdlib, should never fail
        _trace_log = None  # type: ignore[assignment]


# CLM HTTP-replay modes (the four CLM exposes; ``disabled`` is handled by the
# manager — it doesn't start mitmproxy at all — so the addon never sees it).
# These map 1:1 to vcrpy record modes: replay→none, once→once,
# new-episodes→new_episodes, refresh→all. ``record`` is kept as an alias of
# refresh for defensiveness (CLM does not emit it). The serve/record/overwrite
# semantics each implies are computed per-target in ``_modes_for`` because
# ``once`` depends on whether the target cassette already exists.
MODE_REPLAY = "replay"  # serve from cassette, 404 on miss, never record
MODE_RECORD = "record"  # alias of refresh (not emitted by CLM)
MODE_NEW_EPISODES = "new-episodes"  # serve cassette hits, record misses
MODE_REFRESH = "refresh"  # always hit upstream, overwrite cassette
MODE_ONCE = "once"  # cassette present → strict replay; absent → record

# Per-request worker tag identifying the destination cassette (lower-cased
# because HTTP header lookups are case-insensitive and HTTP/2 lowercases).
_TAG_HEADER = "x-clm-cassette"
# Stashed on the flow so response() can route after request() strips the
# header (the tag must not be forwarded upstream or recorded).
_FLOW_TAG_KEY = "clm_cassette_tag"
_FLOW_SERVED_KEY = "clm_served_from_cache"
# Set when request() decided this flow must NOT be recorded (an ``ignore_hosts``
# host such as LangSmith telemetry, or an unfilterable request): it is forwarded
# upstream untouched and response() skips persisting it.
_FLOW_IGNORED_KEY = "clm_ignored_no_record"

# Staging filename infix. Must start with ``.staging-`` so the host-side
# ``merge_staging_into_canonical`` glob (``<name>.staging-*``) finds it. The
# trailing build id (set by the manager) lets the host write the
# ``.completed`` marker for *this* build's staging files only.
_STAGING_INFIX = ".staging-mitm-"

# Headers stripped from a served (replayed) response: we hold the full
# decoded body in memory, so the recorded content-length / transfer
# framing no longer applies — let mitmproxy recompute them from the body.
_SERVE_DROP_HEADERS = frozenset({"content-length", "transfer-encoding"})

# Marker prefixing the once-per-build warning for untagged non-ignored
# traffic. The addon runs inside mitmdump, so its log lines land in the
# manager's stdout ring buffer; the manager greps drained lines for this
# sentinel and re-logs them through CLM's own logger so the warning reaches
# the build log (see ``MitmproxyManager._handle_output_line``). Keep the two
# in lockstep.
UNTAGGED_FLOW_SENTINEL = "CLM-HTTP-REPLAY-UNTAGGED"

# Status synthesized for a strict-replay miss. It MUST be a status the LLM
# SDKs do NOT retry, so a stale-cassette miss fails on the first attempt — the
# out-of-process analogue of vcrpy raising CannotOverwriteExistingCassetteException
# synchronously. openai/anthropic/langchain retry 408/409/429 and every 5xx
# (incl. the old 599 we used), which under a deck's many ``.batch()`` calls
# amplified one miss into 3x the requests + backoff and blew past the build's
# job timeout (issue #165 P3, measured). A 4xx like 404 raises immediately
# (NotFoundError) with no retry. The miss is still identified by its
# ``clm_replay_miss`` body, not this status (see ``_is_replay_miss_marker``),
# so it never collides with a legitimately recorded 404 response.
_REPLAY_MISS_STATUS = 404


class _TraceLike(Protocol):
    """The slice of ``ProxyTraceLog`` the addon depends on (so the trace can be
    either a real log or the no-op stand-in without confusing the type checker)."""

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None: ...

    def close(self) -> None: ...


class _DisabledTrace:
    """No-op proxy-trace stand-in used when the trace module can't be imported.

    ``trace_log`` is pure stdlib so this should never be needed in practice,
    but it lets the addon call ``self._trace.emit(...)`` unconditionally.
    """

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        pass

    def close(self) -> None:
        pass


_DISABLED_TRACE = _DisabledTrace()


class _Target:
    """One destination cassette and its in-build recording/replay state."""

    __slots__ = (
        "canonical",
        "write_path",
        "is_staging",
        "loaded",
        "cassette_existed",
        "recorded",
        "to_write",
        "seen",
        "served",
    )

    def __init__(self, canonical: Path, write_path: Path, *, is_staging: bool) -> None:
        # Where the interactions ultimately live (replay source; merge target).
        self.canonical = canonical
        # Where this addon writes: a per-build staging file for tagged
        # targets (folded by the host), or the canonical itself for the
        # untagged catch-all.
        self.write_path = write_path
        self.is_staging = is_staging
        self.loaded = False
        # Whether the canonical cassette existed when first referenced this
        # build — the discriminator for ``once`` (present → strict replay).
        self.cassette_existed = False
        # Replay match corpus: existing canonical entries (replay-capable
        # modes) plus interactions recorded this build. Scanned pairwise with
        # the vcrpy matcher chain so JSON bodies match semantically.
        self.recorded: list[tuple] = []
        # What gets serialized to ``write_path``. For a tagged staging file
        # this holds only this build's new interactions (the host merge folds
        # them into canonical); for the untagged catch-all it holds the full
        # cassette (seeded existing entries + new) so the in-place rewrite
        # preserves prior recordings.
        self.to_write: list[tuple] = []
        # Sequence-aware dedup key: ``fingerprint(request) + (response_fp,)``.
        # Same request + same response collapses; same request + a *different*
        # response (non-deterministic endpoint) is kept as a separate ordered
        # interaction so a downstream request embedding the later response still
        # replay-matches. The host-side mitmproxy merge preserves the same
        # ordering (``preserve_sequence=True``).
        self.seen: set[tuple] = set()
        # Replay cursor: indices of ``recorded`` already served this build.
        # Repeated identical requests are served in recorded order (R1, R2, …);
        # once a request's recordings are exhausted the *last* match is served
        # again, so a genuinely repeatable request never misses and a
        # single-entry cassette stays byte-for-byte replay-compatible.
        self.served: set[int] = set()


class ClmReplayAddon:
    """Cassette-backed request/response interception (vcrpy-YAML storage).

    Lifecycle:
      * ``load(loader)`` declares the options the manager passes.
      * ``running()`` records the mode + catch-all cassette + a per-build id
        used to name staging files, and builds the secret/ignore-host request
        filter (parity with the in-kernel vcrpy ``before_record_request``).
      * ``request(flow)`` filters the request (dropping secrets; ignore_hosts
        → pass straight through, never recorded), routes by the
        ``X-CLM-Cassette`` tag, strips it, and either serves a cassette hit
        (matched with the vcrpy matcher chain incl. JSON-semantic bodies) or —
        in a strict mode (``replay`` / ``once`` with an existing cassette) —
        synthesizes a non-retryable 404 on miss so the worker fails cleanly
        and fast instead of escaping to the network.
      * ``response(flow)`` persists a real upstream response into the flow's
        target cassette (eagerly, so a kernel/proxy kill cannot lose
        recordings), recording the *filtered* request so the on-disk cassette
        is byte-identical to a vcrpy-recorded one, unless the mode forbids it.

    The ``.completed`` markers that tell the host merge a staging file is
    safe to fold are written by the **host** in the build's ``finally``
    (see ``Course.merge_mitmproxy_cassette_staging``) — that is the
    reliable build-completion signal, and mitmproxy's ``done`` hook does
    not fire on a Windows ``CTRL_BREAK`` shutdown. A force-killed build
    never reaches the host marker step, so its staging stays markerless
    and the next pre-build sweep discards it.
    """

    def __init__(self) -> None:
        self._mode: str = MODE_REPLAY
        self._default_cassette: Path | None = None
        self._build_id: str = ""
        # Built in running() from clm_ignore_hosts: filters secrets out of every
        # recorded request and returns None for ignore_hosts so telemetry passes
        # straight through (never recorded). Mirrors the in-kernel vcrpy filters.
        self._request_filter: Callable[..., object] | None = None
        # Keyed by canonical-path string; "" is the untagged catch-all.
        self._targets: dict[str, _Target] = {}
        # Once-per-build latch for the untagged-flow warning (see request()).
        self._warned_untagged = False
        # Forensic per-flow trace (issue #165 P5). Disabled until running()
        # reads ``clm_trace_dir``; off entirely unless the build sets
        # CLM_HTTP_REPLAY_TRACE=1 and the manager forwards the directory.
        self._trace: _TraceLike = _DISABLED_TRACE

    def load(self, loader) -> None:
        loader.add_option(
            name="clm_cassette_path",
            typespec=str,
            default="",
            help="Catch-all vcrpy-YAML cassette for untagged traffic (tagged "
            "requests route to per-cassette staging files).",
        )
        loader.add_option(
            name="clm_mode",
            typespec=str,
            default=MODE_REPLAY,
            help="CLM replay mode: replay | new-episodes | refresh | once.",
        )
        loader.add_option(
            name="clm_build_id",
            typespec=str,
            default="",
            help="Per-build id used to name staging files so the host can "
            "mark this build's recordings complete.",
        )
        loader.add_option(
            name="clm_ignore_hosts",
            typespec=str,
            default="",
            help="Comma-separated hosts whose traffic is forwarded but never "
            "recorded (LangSmith telemetry by default). Mirrors vcrpy ignore_hosts.",
        )
        loader.add_option(
            name="clm_trace_dir",
            typespec=str,
            default="",
            help="Forensic HTTP-replay trace directory (issue #165 P5). When set, "
            "the addon writes per-flow proxy events to proxy-<pid>.jsonl there. "
            "Empty disables tracing.",
        )

    def running(self) -> None:
        if cf is None:
            logger.error(
                "CLM mitmproxy addon requires vcrpy in the mitmdump environment "
                "(install with: uv tool install mitmproxy --with vcrpy). "
                "Import failed: %s",
                _CF_IMPORT_ERROR,
            )
            ctx.master.shutdown()
            return

        self._mode = ctx.options.clm_mode
        self._build_id = ctx.options.clm_build_id or uuid.uuid4().hex
        cassette_path_str = ctx.options.clm_cassette_path
        self._default_cassette = Path(cassette_path_str) if cassette_path_str else None

        ignore_hosts = tuple(
            h.strip() for h in (ctx.options.clm_ignore_hosts or "").split(",") if h.strip()
        )
        # The before_record_request closure: removes secret headers
        # (authorization/cookie/x-api-key), strips api_key/token query params and
        # password/token/api_key body params, and returns None for ignore_hosts.
        # filter_* default to cassette_format's constants (kept in lockstep with
        # the in-kernel vcrpy bootstrap by a drift-guard test).
        self._request_filter = cf.build_request_filter(ignore_hosts=ignore_hosts)

        # ``once``/``refresh`` strictness is resolved per target in
        # ``_modes_for`` (``once`` depends on whether the target cassette
        # already exists), so there is nothing to existence-check or unlink
        # here — ``clm_cassette_path`` is only the build-scratch catch-all.

        # Forensic trace (issue #165 P5). The ``proxy.ready`` event records the
        # listen port so the analyzer can tell a worker's connect-to-proxy from
        # a genuine bypass (a connect whose port is NOT this proxy's).
        trace_dir = getattr(ctx.options, "clm_trace_dir", "") or ""
        if trace_dir and _trace_log is not None:
            self._trace = _trace_log.ProxyTraceLog.from_trace_dir(trace_dir)
        listen_port = getattr(ctx.options, "listen_port", None)
        listen_host = getattr(ctx.options, "listen_host", "")
        self._trace.emit(
            "proxy.ready",
            {
                "listen_host": listen_host,
                "listen_port": listen_port,
                "mode": self._mode,
                "build_id": self._build_id,
                "ignore_hosts": list(ignore_hosts),
            },
        )

        logger.info(
            "Addon ready: mode=%s default_cassette=%s ignore_hosts=%s build=%s",
            self._mode,
            self._default_cassette,
            ignore_hosts,
            self._build_id,
        )

    def done(self) -> None:
        # Staging files are written eagerly on each recorded response, and
        # the host writes the .completed markers after the proxy stops, so
        # there is nothing to flush here. Best-effort close the trace log
        # (every trace line is already flushed, so a missed close — e.g. a
        # Windows CTRL_BREAK that skips this hook — loses nothing).
        self._trace.close()

    def request(self, flow: http.HTTPFlow) -> None:
        if cf is None:
            return

        tag = flow.request.headers.get(_TAG_HEADER)
        flow.metadata[_FLOW_TAG_KEY] = tag
        if tag is not None:
            # Strip the routing tag so it is neither forwarded upstream nor
            # recorded into the cassette.
            del flow.request.headers[_TAG_HEADER]

        # Filter first: secrets are removed and an ignore_hosts host yields None.
        # The filtered request is what we match against (and what we record), so
        # secret removal and matching stay consistent on both sides.
        filtered = self._filter_request(flow.request)
        if filtered is None:
            # ignore_hosts (e.g. LangSmith telemetry) or an unfilterable request:
            # forward upstream untouched, record nothing, never a miss response.
            flow.metadata[_FLOW_IGNORED_KEY] = True
            self._trace_request(flow, tag, "ignored")
            return

        if tag is None and not self._warned_untagged:
            # A kernel client stack the tag bootstrap does not patch (anything
            # other than httpx/requests/aiohttp — e.g. urllib.request, raw
            # urllib3/http.client, or a subprocess honouring HTTP(S)_PROXY)
            # reached the proxy untagged. Its traffic is matched/recorded
            # against the build's catch-all cassette, NOT the topic's canonical
            # cassette, so record/replay is silently broken for it — exactly
            # the failure mode that hid the missing ``requests`` patch. Warn
            # loudly, once per build (the first flow names the culprit; a
            # per-flow warning would flood the log on a chatty deck).
            self._warned_untagged = True
            logger.warning(
                "%s: %s %s reached the replay proxy without an X-CLM-Cassette "
                "routing tag; it is matched/recorded against the build's "
                "catch-all cassette instead of the topic's canonical cassette. "
                "Only httpx, requests and aiohttp clients are tag-routed by "
                "the kernel bootstrap. Further untagged flows this build are "
                "not logged.",
                UNTAGGED_FLOW_SENTINEL,
                flow.request.method,
                flow.request.pretty_url,
            )

        target = self._target_for(tag)
        if target is None:
            self._trace_request(flow, tag, "passthrough")
            return  # untagged with no catch-all configured -> pass through

        self._ensure_loaded(target)
        serve, record, _overwrite = self._modes_for(target.cassette_existed)

        if serve:
            chosen = self._select_serve_index(target.recorded, filtered, target.served)
            if chosen is not None:
                target.served.add(chosen)
                flow.response = self._build_reply(target.recorded[chosen][1])
                flow.metadata[_FLOW_SERVED_KEY] = True
                self._trace_request(flow, tag, "served")
                return

        if not record:
            # Strict replay (``replay``, or ``once`` with an existing cassette):
            # a miss must fail loudly, never escaping to the real network.
            flow.response = self._replay_miss_response(flow, target)
            self._trace_request(flow, tag, "miss")
            return

        # Recording modes (new-episodes / refresh / once-when-absent) on a
        # cache miss: the request is forwarded upstream and response() persists
        # the reply. This is the only path that produces a real upstream connect.
        self._trace_request(flow, tag, "forward")

    def response(self, flow: http.HTTPFlow) -> None:
        if cf is None or flow.response is None:
            return
        if flow.metadata.get(_FLOW_IGNORED_KEY):
            return  # ignore_hosts / unfilterable: never record
        if flow.metadata.get(_FLOW_SERVED_KEY):
            return  # served from cassette this build — nothing new to record
        if flow.response.status_code == _REPLAY_MISS_STATUS and self._is_replay_miss_marker(
            flow.response
        ):
            return  # synthetic miss; never went upstream

        target = self._target_for(flow.metadata.get(_FLOW_TAG_KEY))
        if target is None:
            return
        self._ensure_loaded(target)
        _serve, record, _overwrite = self._modes_for(target.cassette_existed)
        if not record:
            return

        request = self._filter_request(flow.request)
        if request is None:
            return  # defensive: should have been flagged ignored in request()

        response = cf.vcr_response_dict_from_parts(
            flow.response.status_code,
            flow.response.reason,
            flow.response.headers.fields,
            flow.response.raw_content or b"",
            decode_compressed=True,
        )
        # Sequence-aware key: same request + same response collapses (cheap eager
        # rewrite), but the same request returning a *different* response is kept
        # as a new ordered interaction so the replay sequence is complete.
        key = cf.fingerprint(request) + (cf.response_fingerprint(response),)
        if key in target.seen:
            return  # this exact (request, response) already recorded this build

        target.recorded.append((request, response))
        target.to_write.append((request, response))
        target.seen.add(key)
        # Eager rewrite so a build-timeout kill of mitmdump loses nothing.
        cf.write_cassette(target.write_path, target.to_write)
        self._trace.emit(
            "proxy.response",
            {
                "host": flow.request.host,
                "port": flow.request.port,
                "status": flow.response.status_code,
                "recorded": True,
            },
        )

    # -- tracing ---------------------------------------------------------

    def _trace_request(self, flow: http.HTTPFlow, tag: str | None, action: str) -> None:
        """Emit one ``proxy.request`` forensic event for this flow.

        ``action`` is the addon's decision for the request: ``served`` (cassette
        hit), ``miss`` (strict-replay 404), ``ignored`` (ignore_hosts, forwarded
        not recorded), ``forward`` (recording mode, will hit upstream) or
        ``passthrough`` (untagged, no catch-all). The analyzer uses these as the
        interception-evidence stream that replaces the (now-dark) ``vcr`` stream.
        """
        self._trace.emit(
            "proxy.request",
            {
                "method": flow.request.method,
                "scheme": flow.request.scheme,
                "host": flow.request.host,
                "port": flow.request.port,
                "has_tag": tag is not None,
                "action": action,
            },
        )

    # -- routing ---------------------------------------------------------

    def _target_for(self, tag: str | None) -> _Target | None:
        if tag:
            key = str(Path(tag))
            target = self._targets.get(key)
            if target is None:
                canonical = Path(tag)
                staging = canonical.parent / f"{canonical.name}{_STAGING_INFIX}{self._build_id}"
                target = _Target(canonical, staging, is_staging=True)
                self._targets[key] = target
            return target

        if self._default_cassette is None:
            return None
        target = self._targets.get("")
        if target is None:
            target = _Target(self._default_cassette, self._default_cassette, is_staging=False)
            self._targets[""] = target
        return target

    def _ensure_loaded(self, target: _Target) -> None:
        if target.loaded:
            return
        target.loaded = True
        existed = target.canonical.exists()
        target.cassette_existed = existed
        _serve, _record, overwrite = self._modes_for(existed)
        # ``refresh``/``record`` (overwrite) starts from a clean slate — never
        # seed the match corpus (we always hit upstream) nor ``to_write`` (the
        # cassette is rewritten from this build only). Nothing to load if the
        # canonical doesn't exist yet either.
        if overwrite or not existed:
            return
        try:
            interactions = cf.load_interactions(target.canonical)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "Failed to load cassette %s (%s: %s); starting empty",
                target.canonical,
                type(exc).__name__,
                exc,
            )
            return
        for request, response in interactions:
            target.recorded.append((request, response))
            target.seen.add(cf.fingerprint(request) + (cf.response_fingerprint(response),))
            # The catch-all rewrites the whole cassette in place, so it must
            # keep existing entries; tagged staging files hold only this
            # build's new interactions (the host merge folds them into
            # canonical). ``to_write`` is never serialized in non-recording
            # modes, so seeding it for the catch-all is harmless there.
            if not target.is_staging:
                target.to_write.append((request, response))

    # -- helpers ---------------------------------------------------------

    def _modes_for(self, cassette_existed: bool) -> tuple[bool, bool, bool]:
        """Return ``(serve, record, overwrite)`` for the current mode.

        Only ``once`` depends on ``cassette_existed`` (present → strict replay,
        absent → record-and-serve like new-episodes). This is the per-target
        resolution of vcrpy's record-mode semantics:

        * ``replay``          → serve hits, 404 on miss, never record;
        * ``new-episodes``    → serve hits, record misses;
        * ``refresh``/``record`` → never serve, always record, overwrite;
        * ``once`` + present  → serve hits, 404 on miss, never record;
        * ``once`` + absent   → serve hits, record misses.
        """
        mode = self._mode
        if mode == MODE_NEW_EPISODES:
            return (True, True, False)
        if mode in (MODE_REFRESH, MODE_RECORD):
            return (False, True, True)
        if mode == MODE_ONCE:
            return (True, False, False) if cassette_existed else (True, True, False)
        # MODE_REPLAY and any unknown mode: strict replay is the safe default.
        return (True, False, False)

    def _filter_request(self, request: http.Request):
        """Build the filtered vcr Request for matching/recording, or ``None``.

        ``None`` means "do not record, pass straight through" — either an
        ignore_hosts host or (defensively) a request the vcrpy filters could not
        process. Returning the unfiltered request is never an option: it could
        leak a secret-bearing ``authorization`` header into the cassette.
        """
        request_filter = self._request_filter
        if request_filter is None:
            # running() always builds the filter before any flow is handled;
            # if it somehow hasn't, fail safe (don't record) rather than leak.
            return None
        try:
            vcr_request = cf.vcr_request_from_parts(
                request.method,
                request.url,
                request.headers.fields,
                request.raw_content or b"",
            )
            return request_filter(vcr_request)
        except Exception as exc:  # noqa: BLE001 — never crash the proxy
            logger.warning(
                "Request filtering failed (%s: %s); forwarding without recording",
                type(exc).__name__,
                exc,
            )
            return None

    def _replay_miss_response(self, flow: http.HTTPFlow, target: _Target) -> http.Response:
        # A strict-replay miss returns a NON-RETRYABLE 4xx (``_REPLAY_MISS_STATUS``)
        # so the request never escapes to the network AND the kernel's SDK raises
        # on the FIRST attempt — the out-of-process analogue of vcrpy raising
        # CannotOverwriteExistingCassetteException synchronously. The SDK surfaces
        # it as an APIStatusError (NotFoundError) → cell error → the #93
        # fail-on-error policy → non-zero build exit, fast. (Using a 5xx such as
        # the old 599 made the SDK retry the miss as a server error, amplifying
        # one miss into 3x the requests + backoff across a deck's ``.batch()``
        # calls and stalling the build until its job timeout — issue #165 P3.)
        # ``Connection: close`` keeps a flood of misses from piling onto one
        # pooled connection.
        #
        # The body uses the ``{"error": {message, type, code}}`` envelope the
        # LLM SDKs expect, so the kernel surfaces a clean
        # ``NotFoundError: clm_replay_miss: …`` instead of a confusing pydantic
        # "invalid error body" complaint — a loud, *clear* failure (the gate-7
        # goal). The top-level ``clm_replay_miss`` flag is the marker
        # ``_is_replay_miss_marker`` keys on (status-independent).
        method = flow.request.method
        url = flow.request.pretty_url
        message = f"clm_replay_miss: no recorded interaction for {method} {url} in cassette {target.canonical}"
        return http.Response.make(
            _REPLAY_MISS_STATUS,
            json.dumps(
                {
                    "error": {
                        "message": message,
                        "type": "clm_replay_miss",
                        "code": "clm_replay_miss",
                    },
                    "clm_replay_miss": True,
                    "method": method,
                    "url": url,
                    "cassette": str(target.canonical),
                }
            ).encode(),
            {"Content-Type": "application/json", "Connection": "close"},
        )

    def _build_reply(self, response: dict) -> http.Response:
        status_code, header_pairs, content = cf.response_dict_to_reply_parts(response)
        # mitmproxy's Headers (the Iterable-of-tuples form of Response.make)
        # requires bytes field pairs — the str/dict form would collapse
        # duplicate header names (e.g. Set-Cookie). Encode as ASCII (header
        # values were ASCII-decoded at record time). content-length /
        # transfer-encoding are dropped so mitmproxy reframes from the body.
        headers = [
            (k.encode("ascii", "replace"), v.encode("ascii", "replace"))
            for k, v in header_pairs
            if k.lower() not in _SERVE_DROP_HEADERS
        ]
        return http.Response.make(status_code, content, headers)

    @staticmethod
    def _select_serve_index(recorded: list, filtered, served: set) -> int | None:
        """Pick which recorded interaction to serve for ``filtered`` (replay cursor).

        Scans ``recorded`` in order and returns the index of the first
        not-yet-served interaction whose request matches; once every match has
        been served, returns the **last** matching index (the repeatable tail);
        ``None`` when nothing matches. This replays a per-request response
        *sequence* in recorded order — so a deck that re-issues an identical
        request whose non-deterministic response feeds a *later* request matches
        — while a single-entry recording (the overwhelmingly common case) is
        still served repeatably, byte-for-byte as before this cursor existed.
        """
        chosen: int | None = None
        last_match: int | None = None
        for i, (rec_request, _rec_response) in enumerate(recorded):
            if cf.requests_match(filtered, rec_request):
                last_match = i
                if i not in served:
                    chosen = i
                    break
        return chosen if chosen is not None else last_match

    @staticmethod
    def _is_replay_miss_marker(response: http.Response) -> bool:
        if not (response.headers.get("Content-Type") or "").startswith("application/json"):
            return False
        try:
            body = json.loads(response.content or b"{}")
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(body, dict) and body.get("clm_replay_miss") is True


addons = [ClmReplayAddon()]
