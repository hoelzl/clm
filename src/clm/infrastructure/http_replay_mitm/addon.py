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

Untagged traffic (a kernel that somehow bypassed the tag bootstrap) falls
back to the single ``clm_cassette_path`` catch-all so strict ``replay``
mode still returns a 599 instead of escaping to the network.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

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


# CLM HTTP-replay modes (the four CLM exposes; ``disabled`` is handled by the
# manager — it doesn't start mitmproxy at all — so the addon never sees it).
# These map 1:1 to vcrpy record modes: replay→none, once→once,
# new-episodes→new_episodes, refresh→all. ``record`` is kept as an alias of
# refresh for defensiveness (CLM does not emit it). The serve/record/overwrite
# semantics each implies are computed per-target in ``_modes_for`` because
# ``once`` depends on whether the target cassette already exists.
MODE_REPLAY = "replay"  # serve from cassette, 599 on miss, never record
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
        # Fingerprints already recorded this build (dedup; mirrors the host
        # merge's ``_dedup_key`` so the addon and merge agree).
        self.seen: set[tuple[str, str, bytes]] = set()


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
        synthesizes a 599 on miss so the worker fails cleanly instead of
        escaping to the network.
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
        # there is nothing to flush here. Kept for lifecycle symmetry.
        return

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
            # forward upstream untouched, record nothing, never 599.
            flow.metadata[_FLOW_IGNORED_KEY] = True
            return

        target = self._target_for(tag)
        if target is None:
            return  # untagged with no catch-all configured -> pass through

        self._ensure_loaded(target)
        serve, record, _overwrite = self._modes_for(target.cassette_existed)

        if serve:
            for rec_request, rec_response in target.recorded:
                if cf.requests_match(filtered, rec_request):
                    flow.response = self._build_reply(rec_response)
                    flow.metadata[_FLOW_SERVED_KEY] = True
                    return

        if not record:
            # Strict replay (``replay``, or ``once`` with an existing cassette):
            # a miss must fail loudly, never escaping to the real network.
            flow.response = self._replay_miss_response(flow, target)

    def response(self, flow: http.HTTPFlow) -> None:
        if cf is None or flow.response is None:
            return
        if flow.metadata.get(_FLOW_IGNORED_KEY):
            return  # ignore_hosts / unfilterable: never record
        if flow.metadata.get(_FLOW_SERVED_KEY):
            return  # served from cassette this build — nothing new to record
        if flow.response.status_code == 599 and self._is_replay_miss_marker(flow.response):
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

        key = cf.fingerprint(request)
        if key in target.seen:
            return  # already recorded this build — keep the eager rewrite cheap

        response = cf.vcr_response_dict_from_parts(
            flow.response.status_code,
            flow.response.reason,
            flow.response.headers.fields,
            flow.response.raw_content or b"",
            decode_compressed=True,
        )
        target.recorded.append((request, response))
        target.to_write.append((request, response))
        target.seen.add(key)
        # Eager rewrite so a build-timeout kill of mitmdump loses nothing.
        cf.write_cassette(target.write_path, target.to_write)

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
            target.seen.add(cf.fingerprint(request))
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

        * ``replay``          → serve hits, 599 on miss, never record;
        * ``new-episodes``    → serve hits, record misses;
        * ``refresh``/``record`` → never serve, always record, overwrite;
        * ``once`` + present  → serve hits, 599 on miss, never record;
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
        # A strict-replay miss returns HTTP 599 so the request never escapes to
        # the network. This is the out-of-process analogue of vcrpy's in-kernel
        # CannotOverwriteExistingCassetteException: the kernel's HTTP SDK
        # (openai/anthropic/langchain) surfaces the 599 as an APIStatusError →
        # cell error → the #93 fail-on-error policy → non-zero build exit.
        # SDKs treat 5xx as retryable, so a miss is briefly retried (bounded by
        # the SDK's max_retries, typically 2-3) before it raises — it cannot
        # hang or silently pass. Defense-in-depth against an unbounded/raised
        # SDK retry ceiling: the replay-engaged per-cell timeout
        # (``notebook_processor._HTTP_REPLAY_DEFAULT_CELL_TIMEOUT``, default
        # 600s) bounds any stall regardless of SDK behavior.
        return http.Response.make(
            599,
            json.dumps(
                {
                    "error": "clm_replay_miss",
                    "method": flow.request.method,
                    "url": flow.request.pretty_url,
                    "cassette": str(target.canonical),
                }
            ).encode(),
            {"Content-Type": "application/json"},
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
    def _is_replay_miss_marker(response: http.Response) -> bool:
        if not (response.headers.get("Content-Type") or "").startswith("application/json"):
            return False
        try:
            body = json.loads(response.content or b"{}")
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(body, dict) and body.get("error") == "clm_replay_miss"


addons = [ClmReplayAddon()]
