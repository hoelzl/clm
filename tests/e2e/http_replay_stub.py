"""The local stub server behind the #681 replay course.

Serves exactly the three shapes ``slides_replay_shapes.py`` requests — an
OpenAI-style chat completion (JSON POST + bearer token), a chunked
``text/event-stream`` response, and a same-host redirect carrying an auth
header. Deterministic on purpose: identical bytes every run, so a recorded
cassette replays byte-identically.

Used two ways:

* imported by ``tests/e2e/test_e2e_http_replay.py`` (the record half of the
  record→replay round trip);
* run directly for the re-record ritual on the COMMITTED cassette, which is
  recorded against the fixed default port the deck falls back to::

      python tests/e2e/http_replay_stub.py            # serves on 47113
      # then, in a copy of tests/test-data:
      #   clm build course-specs/test-spec-http-replay.xml --http-replay=refresh ...
      # and copy the refreshed cassette back into the repo.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: The port the committed cassette was recorded against — the deck's
#: ``CLM_TEST_HTTP_BASE`` fallback. Strict replay never binds it; only the
#: re-record ritual does.
DEFAULT_PORT = 47113


class ReplayStubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/chat/completions":
            self._send_json({"error": "unknown path"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("Authorization") != "Bearer test-token-not-a-secret":
            self._send_json({"error": "missing bearer token"}, status=401)
            return
        self._send_json(
            {
                "id": "chatcmpl-stub-0001",
                "object": "chat.completion",
                "model": request.get("model", "stub-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello from the replay stub.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/stream":
            # Chunked SSE: no Content-Length, several flushed events — the
            # transport shape the mitmproxy addon is historically fragile on.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for event in ("alpha", "beta", "gamma"):
                chunk = f"data: {event}\n\n".encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.path == "/old-location":
            self.send_response(302)
            self.send_header("Location", "/new-location")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/new-location":
            self._send_json(
                {
                    "location": "new",
                    "auth_seen": self.headers.get("Authorization") is not None,
                }
            )
            return
        self._send_json({"error": "unknown path"}, status=404)

    def log_message(self, *_args, **_kwargs) -> None:  # silence stderr noise
        pass


@contextlib.contextmanager
def serve(port: int = 0) -> Iterator[str]:
    """Run the stub in a thread; yield its base URL; always shut down."""
    server = ThreadingHTTPServer(("127.0.0.1", port), ReplayStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    import sys

    chosen = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    with serve(chosen) as base:
        print(f"replay stub serving on {base} — Ctrl+C to stop")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
