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
:mod:`clm.infrastructure.http_replay_mitm.cassette_format` module so it
can be unit-tested in CLM's venv and imported by bare path inside the
isolated ``mitmdump`` interpreter.

Request→cassette routing for concurrent multi-topic builds (the
``X-CLM-Cassette`` tag + per-target staging files) is P2; this module
still records into the single ``clm_cassette_path`` it is given.
"""

from __future__ import annotations

import json
import logging
import sys
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
# Modes that preserve (rather than overwrite) any existing cassette entries.
_ADDITIVE = (MODE_NEW_EPISODES, MODE_ONCE)

# Headers stripped from a served (replayed) response: we hold the full
# decoded body in memory, so the recorded content-length / transfer
# framing no longer applies — let mitmproxy recompute them from the body.
_SERVE_DROP_HEADERS = frozenset({"content-length", "transfer-encoding"})


class ClmReplayAddon:
    """Cassette-backed request/response interception (vcrpy-YAML storage).

    Lifecycle:
      * ``load(loader)`` declares the options the manager passes.
      * ``running()`` loads the existing cassette (if any) and indexes it
        by request fingerprint for O(1) replay lookup.
      * ``request(flow)`` intercepts before the upstream call: on a hit we
        set ``flow.response`` to short-circuit; on a miss in strict
        ``replay`` mode we set a 599 so the worker fails cleanly instead
        of escaping to the network.
      * ``response(flow)`` runs after a real upstream response; we persist
        the interaction to the cassette (eagerly, so a kernel/proxy kill
        cannot lose recordings) unless the mode forbids it.
    """

    def __init__(self) -> None:
        self._cassette_path: Path | None = None
        self._mode: str = MODE_REPLAY
        # Replay index: request fingerprint -> stored vcr response dict.
        self._index: dict[tuple[str, str, bytes], dict] = {}
        # Ordered interactions to persist. Seeded from the existing
        # cassette in additive modes so a rewrite preserves prior entries;
        # empty (overwrite) otherwise.
        self._interactions: list = []
        self._seen: set[tuple[str, str, bytes]] = set()

    def load(self, loader) -> None:
        loader.add_option(
            name="clm_cassette_path",
            typespec=str,
            default="",
            help="Path to the vcrpy-YAML cassette file for CLM replay/record.",
        )
        loader.add_option(
            name="clm_mode",
            typespec=str,
            default=MODE_REPLAY,
            help="CLM replay mode: replay | record | new-episodes | refresh | once.",
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

        cassette_path_str = ctx.options.clm_cassette_path
        if not cassette_path_str:
            logger.warning("clm_cassette_path not set; addon will pass through all traffic")
            return
        self._cassette_path = Path(cassette_path_str)
        self._mode = ctx.options.clm_mode

        if self._mode == MODE_REFRESH and self._cassette_path.exists():
            self._cassette_path.unlink()

        if self._mode == MODE_ONCE and not self._cassette_path.exists():
            logger.error(
                "Mode 'once' requires an existing cassette at %s — refusing to start",
                self._cassette_path,
            )
            ctx.master.shutdown()
            return

        if self._mode in _REPLAY_CAPABLE and self._cassette_path.exists():
            self._load_index()

        logger.info(
            "Addon ready: mode=%s cassette=%s indexed=%d",
            self._mode,
            self._cassette_path,
            len(self._index),
        )

    def done(self) -> None:
        # The cassette is rewritten eagerly on every recorded response, so
        # there is nothing to flush here for the single-cassette (P1)
        # model. Kept for lifecycle symmetry / P2 marker writing.
        return

    def request(self, flow: http.HTTPFlow) -> None:
        if self._cassette_path is None or cf is None:
            return  # no cassette configured -> pure pass-through

        key = self._flow_request_key(flow.request)
        cached = self._index.get(key)
        if cached is not None:
            flow.response = self._build_reply(cached)
            return

        if self._mode == MODE_REPLAY:
            # Strict replay: a cassette miss is a programming error. Emit a
            # diagnostic 599 so the worker fails clearly rather than the
            # request being silently dropped or escaping to the network.
            flow.response = http.Response.make(
                599,
                json.dumps(
                    {
                        "error": "clm_replay_miss",
                        "method": flow.request.method,
                        "url": flow.request.pretty_url,
                        "cassette": str(self._cassette_path),
                    }
                ).encode(),
                {"Content-Type": "application/json"},
            )

    def response(self, flow: http.HTTPFlow) -> None:
        if self._cassette_path is None or cf is None:
            return
        if self._mode not in _RECORD_CAPABLE:
            return
        if flow.response is None:
            return

        # Skip recording the synthetic 599 we emitted on a replay miss —
        # it never went upstream.
        if flow.response.status_code == 599 and self._is_replay_miss_marker(flow.response):
            return

        request = cf.vcr_request_from_parts(
            flow.request.method,
            flow.request.url,
            flow.request.headers.fields,
            flow.request.raw_content or b"",
        )
        key = cf.fingerprint(request)
        if key in self._seen:
            # Already recorded this build: don't duplicate. The host-side
            # merge dedups too, but keeping the in-memory cassette deduped
            # keeps the eager rewrite cheap and the file stable.
            return

        response = cf.vcr_response_dict_from_parts(
            flow.response.status_code,
            flow.response.reason,
            flow.response.headers.fields,
            flow.response.raw_content or b"",
            decode_compressed=True,
        )
        self._interactions.append((request, response))
        self._index[key] = response
        self._seen.add(key)
        # Eager rewrite so a build-timeout kill of mitmdump loses nothing.
        cf.write_cassette(self._cassette_path, self._interactions)

    # -- helpers ---------------------------------------------------------

    def _flow_request_key(self, request: http.Request) -> tuple[str, str, bytes]:
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

    def _load_index(self) -> None:
        assert self._cassette_path is not None
        try:
            interactions = cf.load_interactions(self._cassette_path)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "Failed to load cassette %s (%s: %s); starting with empty index",
                self._cassette_path,
                type(exc).__name__,
                exc,
            )
            return
        for request, response in interactions:
            key = cf.fingerprint(request)
            self._index[key] = response
            self._seen.add(key)
            if self._mode in _ADDITIVE:
                # Preserve existing entries on the next rewrite.
                self._interactions.append((request, response))


addons = [ClmReplayAddon()]
