- **The #681 HTTP-replay test course: record → replay → byte-identical is now
  covered end-to-end.** A bundled replay course
  (`tests/test-data/.../module_060_http_replay`, spec
  `test-spec-http-replay.xml`) requests the three shapes that historically
  break the transport — an OpenAI-style JSON POST with a bearer token, a
  chunked `text/event-stream` response, and a redirect carrying an auth
  header — against a deterministic local stub
  (`tests/e2e/http_replay_stub.py`). Two e2e gates: the full round trip
  (record with the stub up, strict-replay a fresh copy with the stub down,
  `--verify-against` proves byte-identical output) and a committed-cassette
  replay (no server, no recording — the committed traces alone carry the
  build). The re-record ritual is one command per side and documented in the
  test module.
