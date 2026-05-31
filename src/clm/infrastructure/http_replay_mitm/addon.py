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


# Mode mapping mirrors vcrpy semantics one-for-one. ``disabled`` is
# handled by the manager (it doesn't start mitmproxy at all), so the
# addon never sees it.
MODE_REPLAY = "replay"  # serve from cassette, error on miss
MODE_RECORD = "record"  # always hit upstream, record (overwrite)
MODE_NEW_EPISODES = "new-episodes"  # serve cassette hits, record misses
MODE_REFRESH = "refresh"  # always hit upstream, overwrite cassette
MODE_ONCE = "once"  # like new-episodes but cassette must exist

# Modes that serve recorded responses from the cassette.
_REPLAY_CAPABLE = (MODE_REPLAY, MODE_NEW_EPISODES, MODE_ONCE)
# Modes that persist newly observed interactions.
_RECORD_CAPABLE = (MODE_RECORD, MODE_NEW_EPISODES, MODE_REFRESH, MODE_ONCE)
# Modes that preserve (rather than overwrite) existing cassette entries
# when writing the *catch-all* cassette in full (tagged staging files only
# ever hold the build's new interactions; the host merge folds them).
_ADDITIVE = (MODE_NEW_EPISODES, MODE_ONCE)

# Per-request worker tag identifying the destination cassette (lower-cased
# because HTTP header lookups are case-insensitive and HTTP/2 lowercases).
_TAG_HEADER = "x-clm-cassette"
# Stashed on the flow so response() can route after request() strips the
# header (the tag must not be forwarded upstream or recorded).
_FLOW_TAG_KEY = "clm_cassette_tag"
_FLOW_SERVED_KEY = "clm_served_from_cache"

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

    __slots__ = ("canonical", "write_path", "is_staging", "loaded", "index", "interactions", "seen")

    def __init__(self, canonical: Path, write_path: Path, *, is_staging: bool) -> None:
        # Where the interactions ultimately live (replay source; merge target).
        self.canonical = canonical
        # Where this addon writes: a per-build staging file for tagged
        # targets (folded by the host), or the canonical itself for the
        # untagged catch-all.
        self.write_path = write_path
        self.is_staging = is_staging
        self.loaded = False
        self.index: dict[tuple[str, str, bytes], dict] = {}
        self.interactions: list = []
        self.seen: set[tuple[str, str, bytes]] = set()


class ClmReplayAddon:
    """Cassette-backed request/response interception (vcrpy-YAML storage).

    Lifecycle:
      * ``load(loader)`` declares the options the manager passes.
      * ``running()`` records the mode + catch-all cassette and a per-build
        id used to name staging files.
      * ``request(flow)`` routes by the ``X-CLM-Cassette`` tag, strips it,
        and either serves a cassette hit (short-circuit) or — in strict
        ``replay`` mode — synthesizes a 599 on miss so the worker fails
        cleanly instead of escaping to the network.
      * ``response(flow)`` persists a real upstream response into the
        flow's target cassette (eagerly, so a kernel/proxy kill cannot
        lose recordings) unless the mode forbids it.

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
            help="CLM replay mode: replay | record | new-episodes | refresh | once.",
        )
        loader.add_option(
            name="clm_build_id",
            typespec=str,
            default="",
            help="Per-build id used to name staging files so the host can "
            "mark this build's recordings complete.",
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

        # NOTE: vcrpy's strict ``once`` (cassette-must-exist) and ``refresh``
        # (overwrite) semantics are not enforced per target under the
        # out-of-process transport yet. The Phase-0 model had a single shared
        # cassette, so this method could existence-check / unlink it; but under
        # P2's per-(topic,language,kind) tag routing, ``clm_cassette_path`` is
        # only the build-scratch *catch-all* (created empty on every fresh
        # build — see build.py). A ``once`` existence check against it would
        # wrongly abort the proxy at startup, and a ``refresh`` unlink of it
        # would clear nothing real. So we do neither here. Until per-target
        # strict semantics land (issue #165 P3), ``once`` behaves like
        # ``new-episodes`` (serve cassette hits, record misses) and ``refresh``
        # records fresh interactions additively (the host merge dedups against
        # canonical — the same imperfect overwrite the in-process vcrpy path
        # exhibits today). The mode sets above encode this routing.

        logger.info(
            "Addon ready: mode=%s default_cassette=%s build=%s",
            self._mode,
            self._default_cassette,
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

        target = self._target_for(tag)
        if target is None:
            return  # untagged with no catch-all configured -> pass through

        self._ensure_loaded(target)
        key = self._request_key(flow.request)
        cached = target.index.get(key)
        if cached is not None:
            flow.response = self._build_reply(cached)
            flow.metadata[_FLOW_SERVED_KEY] = True
            return

        if self._mode == MODE_REPLAY:
            flow.response = http.Response.make(
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

    def response(self, flow: http.HTTPFlow) -> None:
        if cf is None or flow.response is None:
            return
        if self._mode not in _RECORD_CAPABLE:
            return
        if flow.metadata.get(_FLOW_SERVED_KEY):
            return  # served from cassette this build — nothing new to record
        if flow.response.status_code == 599 and self._is_replay_miss_marker(flow.response):
            return  # synthetic miss; never went upstream

        target = self._target_for(flow.metadata.get(_FLOW_TAG_KEY))
        if target is None:
            return

        request = cf.vcr_request_from_parts(
            flow.request.method,
            flow.request.url,
            flow.request.headers.fields,
            flow.request.raw_content or b"",
        )
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
        target.interactions.append((request, response))
        target.index[key] = response
        target.seen.add(key)
        # Eager rewrite so a build-timeout kill of mitmdump loses nothing.
        cf.write_cassette(target.write_path, target.interactions)

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
        if self._mode not in _REPLAY_CAPABLE or not target.canonical.exists():
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
            key = cf.fingerprint(request)
            target.index[key] = response
            target.seen.add(key)
            # The catch-all rewrites the whole cassette, so it must keep
            # existing entries; tagged staging files hold only new
            # interactions (the host merge folds them into canonical).
            if not target.is_staging and self._mode in _ADDITIVE:
                target.interactions.append((request, response))

    # -- helpers ---------------------------------------------------------

    def _request_key(self, request: http.Request) -> tuple[str, str, bytes]:
        vcr_request = cf.vcr_request_from_parts(
            request.method, request.url, request.headers.fields, request.raw_content or b""
        )
        return cf.fingerprint(vcr_request)

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
