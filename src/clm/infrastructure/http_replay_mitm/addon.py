"""mitmproxy addon implementing CLM's record/replay semantics.

This module is loaded by ``mitmdump`` (not by the CLM workers). It is
invoked via ``mitmdump --scripts <this-file>`` and reads its config from
mitmproxy options the manager sets on the command line.

Storage format for the prototype is mitmproxy's native ``.mitm`` binary
flow stream — chosen so we can validate the transport architecture
without also rebuilding the cassette-format and staging/merge layers.
Compatibility with the existing vcrpy YAML schema is follow-up work
(see the design doc).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import IO

from mitmproxy import ctx, http
from mitmproxy.io import FlowReader, FlowWriter

logger = logging.getLogger("clm.http_replay_mitm.addon")


# Mode mapping mirrors vcrpy semantics one-for-one. ``disabled`` is
# handled by the manager (it doesn't start mitmproxy at all), so the
# addon never sees it.
MODE_REPLAY = "replay"  # serve from cassette, error on miss
MODE_RECORD = "record"  # always hit upstream, append new flows
MODE_NEW_EPISODES = "new-episodes"  # serve cassette hits, record misses
MODE_REFRESH = "refresh"  # always hit upstream, overwrite cassette
MODE_ONCE = "once"  # like new-episodes but cassette must exist


def _request_key(request: http.Request) -> tuple[str, str, bytes]:
    """Fingerprint a request for cassette matching.

    Matches CLM's existing dedup logic: method + full URI + body bytes.
    Body is included to defend against the silent-wrong-response failure
    mode that vcrpy's default ``match_on`` exhibits when call order shifts.
    """
    body = request.raw_content or b""
    return (request.method.upper(), request.pretty_url, body)


class ClmReplayAddon:
    """Cassette-backed request/response interception.

    Lifecycle:
      * ``load(loader)`` declares the custom options the manager passes.
      * ``running()`` loads the existing cassette (if any) and indexes it
        by request fingerprint for O(1) replay lookup.
      * ``request(flow)`` intercepts before the upstream call: on a hit
        we set ``flow.response`` to short-circuit; on a miss in strict
        ``replay`` mode we set an error response so the worker fails
        cleanly instead of escaping to the network.
      * ``response(flow)`` runs after a real upstream response; we
        persist the flow to the cassette unless the mode forbids it.
    """

    def __init__(self) -> None:
        self._cassette_path: Path | None = None
        self._mode: str = MODE_REPLAY
        # Maps request fingerprint -> recorded HTTPFlow. We keep the
        # full flow rather than just the response so future format
        # changes (matching on headers, etc.) only need to extend the
        # key — the value already has everything.
        self._index: dict[tuple[str, str, bytes], http.HTTPFlow] = {}
        # New flows that haven't been written yet. Persisted on each
        # response (eager append) so a worker crash mid-build cannot
        # lose in-memory recordings — same invariant as vcrpy patch #6.
        self._writer: FlowWriter | None = None
        self._writer_handle: IO[bytes] | None = None

    def load(self, loader) -> None:
        loader.add_option(
            name="clm_cassette_path",
            typespec=str,
            default="",
            help="Path to the .mitm cassette file for CLM replay/record.",
        )
        loader.add_option(
            name="clm_mode",
            typespec=str,
            default=MODE_REPLAY,
            help="CLM replay mode: replay | record | new-episodes | refresh | once.",
        )

    def running(self) -> None:
        cassette_path_str = ctx.options.clm_cassette_path
        if not cassette_path_str:
            logger.warning("clm_cassette_path not set; addon will pass through all traffic")
            return
        self._cassette_path = Path(cassette_path_str)
        self._mode = ctx.options.clm_mode

        # Refresh mode discards the existing cassette before loading.
        if self._mode == MODE_REFRESH and self._cassette_path.exists():
            self._cassette_path.unlink()

        # Load existing flows into the lookup index. ``record`` and
        # ``refresh`` start from an empty index — they're explicitly
        # overwriting whatever was there.
        if (
            self._mode in (MODE_REPLAY, MODE_NEW_EPISODES, MODE_ONCE)
            and self._cassette_path.exists()
        ):
            self._load_index()
        elif self._mode == MODE_ONCE and not self._cassette_path.exists():
            logger.error(
                "Mode 'once' requires an existing cassette at %s — refusing to start",
                self._cassette_path,
            )
            ctx.master.shutdown()
            return

        # Open the writer in append mode for record-capable modes.
        if self._mode in (MODE_RECORD, MODE_NEW_EPISODES, MODE_REFRESH):
            self._cassette_path.parent.mkdir(parents=True, exist_ok=True)
            self._writer_handle = open(self._cassette_path, "ab")
            self._writer = FlowWriter(self._writer_handle)

        logger.info(
            "Addon ready: mode=%s cassette=%s indexed=%d",
            self._mode,
            self._cassette_path,
            len(self._index),
        )

    def done(self) -> None:
        if self._writer_handle is not None:
            self._writer_handle.flush()
            self._writer_handle.close()
            self._writer_handle = None
            self._writer = None

    def request(self, flow: http.HTTPFlow) -> None:
        if self._cassette_path is None:
            return  # no cassette configured -> pure pass-through

        key = _request_key(flow.request)
        cached = self._index.get(key)
        if cached is not None and cached.response is not None:
            # Serve from cassette. Setting flow.response short-circuits
            # the upstream call. Copying preserves the cached response
            # for subsequent identical requests (allow_playback_repeats
            # equivalent — we keep the entry in the index).
            flow.response = cached.response.copy()
            return

        if self._mode == MODE_REPLAY:
            # Strict replay: a cassette miss is a programming error.
            # We synthesize a 599 so the worker sees a clear failure
            # rather than the request being silently dropped.
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
        if self._writer is None or flow.response is None:
            return

        # Skip recording for synthetic 599s we emitted ourselves on
        # replay miss — they never went upstream.
        if flow.response.status_code == 599 and (
            flow.response.headers.get("Content-Type") or ""
        ).startswith("application/json"):
            try:
                body = json.loads(flow.response.content or b"{}")
                if isinstance(body, dict) and body.get("error") == "clm_replay_miss":
                    return
            except (json.JSONDecodeError, ValueError):
                pass

        key = _request_key(flow.request)
        if key in self._index:
            # Already in cassette: don't duplicate. (Even in record
            # mode we dedupe so repeated invocations of the same
            # notebook don't grow the cassette unboundedly.)
            return

        self._index[key] = flow
        self._writer.add(flow)
        # Force flush so a kernel kill doesn't lose this entry.
        if self._writer_handle is not None:
            self._writer_handle.flush()

    def _load_index(self) -> None:
        assert self._cassette_path is not None
        try:
            with open(self._cassette_path, "rb") as handle:
                reader = FlowReader(handle)
                for flow in reader.stream():
                    if isinstance(flow, http.HTTPFlow) and flow.response is not None:
                        self._index[_request_key(flow.request)] = flow
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "Failed to load cassette %s (%s: %s); starting with empty index",
                self._cassette_path,
                type(exc).__name__,
                exc,
            )


addons = [ClmReplayAddon()]
